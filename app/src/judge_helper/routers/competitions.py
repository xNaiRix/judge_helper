"""Соревнования: создание, список, жизненный цикл, судьи, приглашения.

Все значимые действия пишутся в аудит-лог и порождают уведомления по ТЗ."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import (
    Competition,
    CompetitionStatus,
    Invite,
    Judge,
    Participant,
    Score,
    User,
    utcnow,
)
from ..schemas import (
    CompetitionDetailOut,
    CompetitionIn,
    CompetitionOut,
    FinishOut,
    InviteIn,
    InviteOut,
    JudgeBrief,
)
from ..services import (
    audit,
    comp_judge_users,
    fio_display,
    get_competition,
    is_judge,
    notify_all_judges,
    notify_user,
    require_admin,
    require_member,
    require_status,
    sweep_expired,
)
from ..settings import settings

router = APIRouter(prefix="/competitions", tags=["Соревнования"])


def _brief(comp: Competition, user_id: int) -> CompetitionOut:
    return CompetitionOut(
        id=comp.id,
        name=comp.name,
        place=comp.place,
        dates=comp.dates,
        status=comp.status,
        is_admin=comp.admin_id == user_id,
    )


@router.post("", response_model=CompetitionOut, status_code=201, summary="Создать соревнование")
def create_competition(
    data: CompetitionIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = Competition(
        name=data.name.strip(),
        place=data.place.strip(),
        dates=data.dates.strip(),
        admin_id=user.id,
    )
    db.add(comp)
    db.flush()
    # Создатель автоматически становится судьёй соревнования (ТЗ)
    db.add(Judge(competition_id=comp.id, user_id=user.id))
    audit(
        db,
        request,
        user,
        "admin",
        "create",
        "competition",
        comp.id,
        new_value={"name": comp.name, "dates": comp.dates, "place": comp.place},
    )
    db.commit()
    db.refresh(comp)
    return _brief(comp, user.id)


@router.get("", response_model=list[CompetitionOut], summary="Список моих соревнований")
def list_competitions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Ленивая регламентная операция: автоудаление завершённых более года назад (ТЗ)
    sweep_expired(db)
    comps = db.execute(
        select(Competition)
        .join(Judge, Judge.competition_id == Competition.id)
        .where(Judge.user_id == user.id, Competition.deleted.is_(False))
        .order_by(Competition.id.desc())
    ).scalars()
    return [_brief(c, user.id) for c in comps]


@router.get(
    "/{competition_id}",
    response_model=CompetitionDetailOut,
    summary="Информация о соревновании и список судей",
)
def competition_detail(
    competition_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    judges = [
        JudgeBrief(id=j.id, full_name=j.full_name, email=j.email)
        for j in comp_judge_users(db, comp.id)
    ]
    return CompetitionDetailOut(**_brief(comp, user.id).model_dump(), judges=judges)


@router.delete("/{competition_id}", status_code=204, summary="Удалить соревнование (soft-delete)")
def delete_competition(
    competition_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    require_admin(comp, user)
    # Удаление разрешено до начала и после завершения; во время — запрещено (ТЗ)
    require_status(comp, CompetitionStatus.planned, CompetitionStatus.finished)

    # При ручном удалении все судьи получают уведомление (ТЗ)
    notify_all_judges(db, comp, "deleted", f"Соревнование «{comp.name}» удалено администратором")
    comp.deleted = True
    comp.deleted_at = utcnow()
    # Аннулируем необработанные приглашения
    for invite in db.execute(
        select(Invite).where(Invite.competition_id == comp.id, Invite.status == "pending")
    ).scalars():
        invite.status = "expired"
    audit(
        db,
        request,
        user,
        "admin",
        "delete",
        "competition",
        comp.id,
        old_value={"name": comp.name, "status": comp.status},
    )
    db.commit()


@router.post(
    "/{competition_id}/start",
    response_model=CompetitionOut,
    summary="Начать соревнование",
)
def start_competition(
    competition_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    require_admin(comp, user)
    if comp.status != CompetitionStatus.planned.value:
        raise HTTPException(status_code=409, detail="Соревнование уже начато или завершено")
    comp.status = CompetitionStatus.started.value
    # После старта все необработанные приглашения истекают (ТЗ)
    for invite in db.execute(
        select(Invite).where(Invite.competition_id == comp.id, Invite.status == "pending")
    ).scalars():
        invite.status = "expired"
    notify_all_judges(db, comp, "started", f"Соревнование «{comp.name}» началось")
    audit(
        db,
        request,
        user,
        "admin",
        "start",
        "competition",
        comp.id,
        old_value={"status": "planned"},
        new_value={"status": "started"},
    )
    db.commit()
    db.refresh(comp)
    return _brief(comp, user.id)


@router.post(
    "/{competition_id}/finish", response_model=FinishOut, summary="Завершить соревнование"
)
def finish_competition(
    competition_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    require_admin(comp, user)
    require_status(comp, CompetitionStatus.started)

    participants = list(
        db.execute(
            select(Participant)
            .where(Participant.competition_id == comp.id)
            .order_by(Participant.id)
        ).scalars()
    )
    scored_ids = {
        pid
        for (pid,) in db.execute(
            select(Score.participant_id).where(Score.competition_id == comp.id)
        )
    }
    unmarked = [fio_display(p) for p in participants if p.id not in scored_ids]
    if unmarked:
        # Завершение блокируется, пока есть участники без оценок (ТЗ)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Завершение невозможно: есть участники без оценок",
                "participants": unmarked,
            },
        )

    # Предупреждение (не блокирующее): судьи, не оценившие части участников
    warnings: list[str] = []
    for judge in comp_judge_users(db, comp.id):
        judged = {
            pid
            for (pid,) in db.execute(
                select(Score.participant_id).where(
                    Score.competition_id == comp.id, Score.judge_id == judge.id
                )
            )
        }
        missed = [fio_display(p) for p in participants if p.id not in judged]
        if missed:
            warnings.append(f"Судья {judge.full_name} не оценил участников: {', '.join(missed)}")

    comp.status = CompetitionStatus.finished.value
    comp.finished_at = utcnow()
    notify_all_judges(db, comp, "finished", f"Соревнование «{comp.name}» завершено")
    audit(
        db,
        request,
        user,
        "admin",
        "finish",
        "competition",
        comp.id,
        old_value={"status": "started"},
        new_value={"status": "finished"},
    )
    db.commit()
    return FinishOut(status=comp.status, unmarked_participants=[], warnings=warnings)


@router.get("/{competition_id}/judges", response_model=list[JudgeBrief], summary="Список судей")
def list_judges(
    competition_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    return [
        JudgeBrief(id=j.id, full_name=j.full_name, email=j.email)
        for j in comp_judge_users(db, comp.id)
    ]


@router.delete(
    "/{competition_id}/judges/{user_id}",
    status_code=204,
    summary="Удалить судью из соревнования (только до начала)",
)
def remove_judge(
    competition_id: int,
    user_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    require_admin(comp, user)
    require_status(comp, CompetitionStatus.planned)
    if user_id == comp.admin_id:
        raise HTTPException(
            status_code=403,
            detail="Удаление администратора соревнования запрещено",
        )
    link = db.execute(
        select(Judge).where(Judge.competition_id == comp.id, Judge.user_id == user_id)
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(
            status_code=404,
            detail="Этот пользователь не является судьёй соревнования",
        )

    removed = db.get(User, user_id)
    db.delete(link)
    if removed is not None:
        # Удалённый судья получает уведомление (ТЗ)
        notify_user(db, removed, "removed", f"Вы удалены из соревнования «{comp.name}»")
    audit(
        db,
        request,
        user,
        "admin",
        "delete",
        "judge_link",
        f"{comp.id}/{user_id}",
        old_value={"user_id": user_id},
    )
    db.commit()


# ---------------------------------------------------------------------------
# Приглашения
# ---------------------------------------------------------------------------


@router.post(
    "/{competition_id}/invites",
    response_model=InviteOut,
    status_code=201,
    summary="Пригласить судью по email (только до начала)",
)
def create_invite(
    competition_id: int,
    data: InviteIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    require_admin(comp, user)
    # Отправка приглашений разрешена только до начала соревнования (ТЗ)
    require_status(comp, CompetitionStatus.planned)

    # Судья уже состоит в соревновании — приглашение не нужно
    invitee = db.execute(select(User).where(User.email == data.email)).scalar_one_or_none()
    if invitee is not None and is_judge(db, comp.id, invitee.id):
        raise HTTPException(
            status_code=409,
            detail="Этот пользователь уже является судьёй соревнования",
        )

    # Одновременно может висеть только одно неотвеченное приглашение
    pending = db.execute(
        select(Invite).where(
            Invite.competition_id == comp.id,
            Invite.email == data.email,
            Invite.status == "pending",
        )
    ).scalar_one_or_none()
    if pending is not None:
        raise HTTPException(
            status_code=409,
            detail="Приглашение этому судье уже отправлено и ещё не обработано "
            "(дождитесь ответа или истечения срока)",
        )

    invite = Invite(
        competition_id=comp.id,
        email=data.email,
        expires_at=utcnow() + timedelta(days=settings.INVITE_TTL_DAYS),
    )
    db.add(invite)
    db.flush()
    # Уведомление о приглашении, если аккаунт уже существует (ТЗ)
    if invitee is not None:
        notify_user(
            db, invitee, "invited", f"Вас пригласили в соревнование «{comp.name}» ({comp.dates})"
        )
    audit(
        db,
        request,
        user,
        "admin",
        "invite",
        "invite",
        invite.id,
        new_value={"email": data.email, "competition_id": comp.id},
    )
    db.commit()
    db.refresh(invite)
    return InviteOut.model_validate(invite)


@router.get(
    "/{competition_id}/invites",
    response_model=list[InviteOut],
    summary="Список приглашений (для администратора)",
)
def list_invites(
    competition_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    require_admin(comp, user)
    invites = db.execute(
        select(Invite).where(Invite.competition_id == comp.id).order_by(Invite.id)
    ).scalars()
    return [InviteOut.model_validate(i) for i in invites]


def _get_invite(db: Session, competition_id: int, invite_id: int) -> Invite:
    invite = db.get(Invite, invite_id)
    if invite is None or invite.competition_id != competition_id:
        raise HTTPException(status_code=404, detail="Приглашение не найдено")
    return invite


def _check_invite_usable(invite: Invite, comp: Competition, db: Session) -> None:
    if invite.status == "expired":
        # Истёкшее (в т.ч. аннулированное стартом соревнования) принимать нельзя (ТЗ)
        raise HTTPException(
            status_code=410,
            detail="Срок действия приглашения истёк или соревнование уже началось",
        )
    if invite.status != "pending":
        raise HTTPException(status_code=409, detail="Приглашение уже обработано")
    if invite.expires_at < utcnow():
        invite.status = "expired"
        db.commit()
        raise HTTPException(status_code=410, detail="Срок действия приглашения истёк")
    if comp.status != CompetitionStatus.planned.value:
        # После начала соревнования приглашения недоступны (ТЗ)
        invite.status = "expired"
        db.commit()
        raise HTTPException(
            status_code=410,
            detail="Соревнование уже началось — приглашение истекло",
        )


@router.post(
    "/{competition_id}/invites/{invite_id}/accept",
    response_model=InviteOut,
    summary="Принять приглашение",
)
def accept_invite(
    competition_id: int,
    invite_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = get_competition(db, competition_id)
    invite = _get_invite(db, competition_id, invite_id)
    if invite.email != user.email:
        raise HTTPException(
            status_code=403,
            detail="Приглашение предназначено другому пользователю",
        )
    _check_invite_usable(invite, comp, db)

    invite.status = "accepted"
    already = db.execute(
        select(Judge).where(Judge.competition_id == comp.id, Judge.user_id == user.id)
    ).scalar_one_or_none()
    if already is None:
        db.add(Judge(competition_id=comp.id, user_id=user.id))
    # Администратор получает уведомление об изменении статуса приглашения (ТЗ)
    admin = db.get(User, comp.admin_id)
    if admin is not None:
        notify_user(
            db,
            admin,
            "invite_status",
            f"Судья {user.full_name} принял приглашение в «{comp.name}»",
        )
    audit(
        db,
        request,
        user,
        "judge",
        "accept",
        "invite",
        invite.id,
        new_value={"competition_id": comp.id},
    )
    db.commit()
    db.refresh(invite)
    return InviteOut.model_validate(invite)


@router.post(
    "/{competition_id}/invites/{invite_id}/decline",
    response_model=InviteOut,
    summary="Отклонить приглашение",
)
def decline_invite(
    competition_id: int,
    invite_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = get_competition(db, competition_id)
    invite = _get_invite(db, competition_id, invite_id)
    if invite.email != user.email:
        raise HTTPException(
            status_code=403,
            detail="Приглашение предназначено другому пользователю",
        )
    _check_invite_usable(invite, comp, db)

    invite.status = "declined"
    admin = db.get(User, comp.admin_id)
    if admin is not None:
        notify_user(
            db,
            admin,
            "invite_status",
            f"Судья {user.full_name} отклонил приглашение в «{comp.name}»",
        )
    audit(
        db,
        request,
        user,
        "judge",
        "decline",
        "invite",
        invite.id,
        new_value={"competition_id": comp.id},
    )
    db.commit()
    db.refresh(invite)
    return InviteOut.model_validate(invite)
