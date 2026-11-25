"""Сервисный слой: правила доступа, аудит, уведомления, регламентные операции."""

from __future__ import annotations

import json
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AuditLog,
    Competition,
    CompetitionStatus,
    EmailOutbox,
    Invite,
    Judge,
    Notification,
    Participant,
    Score,
    User,
    utcnow,
)
from .settings import settings

MAX_PARTICIPANTS = 1000  # ТЗ: до 1000 участников в соревновании


# ------------------------- Доступ и бизнес-правила -------------------------


def get_competition(db: Session, competition_id: int) -> Competition:
    """Соревнование по id; удалённые (soft-delete) не отдаются (404)."""
    comp = db.get(Competition, competition_id)
    if comp is None or comp.deleted:
        raise HTTPException(status_code=404, detail="Соревнование не найдено")
    return comp


def is_judge(db: Session, competition_id: int, user_id: int) -> bool:
    return (
        db.execute(
            select(Judge).where(Judge.competition_id == competition_id, Judge.user_id == user_id)
        ).scalar_one_or_none()
        is not None
    )


def require_member(db: Session, competition_id: int, user: User) -> Competition:
    """Доступ только для судей соревнования (IDOR-защита: чужим — 404)."""
    comp = get_competition(db, competition_id)
    if not is_judge(db, comp.id, user.id):
        raise HTTPException(status_code=404, detail="Соревнование не найдено")
    return comp


def require_admin(comp: Competition, user: User) -> None:
    if comp.admin_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Действие доступно только администратору соревнования",
        )


def require_status(comp: Competition, *statuses: CompetitionStatus) -> None:
    if comp.status not in {s.value for s in statuses}:
        allowed = " / ".join(
            {"planned": "не начато", "started": "начато", "finished": "завершено"}[s.value]
            for s in statuses
        )
        raise HTTPException(
            status_code=403,
            detail=(
                f"Действие разрешено только в статусе: {allowed}. Текущий статус: {comp.status}"
            ),
        )


def competition_participants(db: Session, competition_id: int) -> list[Participant]:
    return list(
        db.execute(
            select(Participant)
            .where(Participant.competition_id == competition_id)
            .order_by(Participant.id)
        ).scalars()
    )


def participant_scores(db: Session, competition_id: int, participant_id: int) -> list[Score]:
    return list(
        db.execute(
            select(Score).where(
                Score.competition_id == competition_id, Score.participant_id == participant_id
            )
        ).scalars()
    )


def unmarked_participants(db: Session, comp: Competition) -> list[Participant]:
    """Участники без единой оценки — блокируют завершение (ТЗ)."""
    scored_ids = {
        p_id
        for (p_id,) in db.execute(
            select(Score.participant_id).where(Score.competition_id == comp.id)
        )
    }
    return [p for p in competition_participants(db, comp.id) if p.id not in scored_ids]


def fio_display(p: Participant) -> str:
    return " ".join(x for x in (p.last_name, p.first_name, p.patronymic) if x)


# ------------------------------- Аудит -------------------------------------


def request_info(request: Request | None) -> dict:
    """Технические данные запроса для аудит-лога."""
    from .middleware import request_id_var

    if request is None:
        return {"ip": "", "user_agent": "", "request_id": request_id_var.get("")}
    return {
        "ip": request.client.host if request.client else "",
        "user_agent": (request.headers.get("user-agent") or "")[:255],
        "request_id": request_id_var.get(""),
    }


def audit(
    db: Session,
    request: Request | None,
    user: User | None,
    role: str,
    action: str,
    entity_type: str,
    entity_id=None,
    old_value=None,
    new_value=None,
    conflict: bool = False,
) -> None:
    """Запись в аудит-лог. old/new_value сериализуются в JSON."""
    info = request_info(request)
    old_json = (
        json.dumps(old_value, ensure_ascii=False, default=str) if old_value is not None else None
    )
    new_json = (
        json.dumps(new_value, ensure_ascii=False, default=str) if new_value is not None else None
    )
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            role=role,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else "",
            old_value=old_json,
            new_value=new_json,
            conflict_overwritten=conflict,
            ip=info["ip"],
            user_agent=info["user_agent"],
            request_id=info["request_id"],
        )
    )


# ---------------------------- Уведомления ----------------------------------


def send_email(db: Session, to_email: str, subject: str, body: str) -> None:
    """Имитация отправки email (в реальном приложении — SMTP/push-провайдер)."""
    db.add(EmailOutbox(to_email=to_email, subject=subject, body=body))


def notify_user(db: Session, user: User, kind: str, text: str) -> None:
    """Уведомление пользователю + email-дубликат, если включён в настройках (ТЗ)."""
    if user.notifications_enabled:
        db.add(Notification(user_id=user.id, kind=kind, text=text))
    if user.email_notifications_enabled:
        send_email(db, user.email, "Помощник судьи: уведомление", text)


def notify_all_judges(
    db: Session, comp: Competition, kind: str, text: str, exclude_user_id: int | None = None
) -> None:
    for u in db.execute(
        select(User).join(Judge, Judge.user_id == User.id).where(Judge.competition_id == comp.id)
    ).scalars():
        if exclude_user_id is None or u.id != exclude_user_id:
            notify_user(db, u, kind, text)


def comp_judge_users(db: Session, competition_id: int) -> list[User]:
    return list(
        db.execute(
            select(User)
            .join(Judge, Judge.user_id == User.id)
            .where(Judge.competition_id == competition_id)
            .order_by(User.id)
        ).scalars()
    )


# ------------------------- Регламентные операции ---------------------------


def sweep_expired(db: Session) -> dict:
    """Регламентные операции (вызываются лениво):

    * автоудаление соревнований через год после завершения (soft-delete,
      без уведомлений — ТЗ различает ручное и автоматическое удаление);
    * очистка аудит-лога старше 6 месяцев;
    * очистка использованных/истёкших одноразовых токенов.
    """
    now = utcnow()
    horizon = now - timedelta(days=settings.COMPETITION_AUTODELETE_DAYS)
    autodeleted = 0
    for comp in db.execute(
        select(Competition).where(
            Competition.deleted.is_(False),
            Competition.finished_at.is_not(None),
            Competition.finished_at < horizon,
        )
    ).scalars():
        comp.deleted = True
        comp.deleted_at = now
        for invite in db.execute(
            select(Invite).where(Invite.competition_id == comp.id, Invite.status == "pending")
        ).scalars():
            invite.status = "expired"
        autodeleted += 1

    audit_horizon = now - timedelta(days=settings.AUDIT_RETENTION_DAYS)
    purged_audit = db.query(AuditLog).filter(AuditLog.ts < audit_horizon).delete()

    db.query(Notification).filter(Notification.created_at < now - timedelta(days=30)).delete(
        synchronize_session=False
    )

    from .models import AuthToken

    purged_tokens = (
        db.query(AuthToken)
        .filter((AuthToken.used.is_(True)) | (AuthToken.expires_at < now))
        .delete(synchronize_session=False)
    )

    if autodeleted or purged_audit or purged_tokens:
        db.commit()
    return {
        "auto_deleted_competitions": autodeleted,
        "purged_audit_rows": purged_audit,
        "purged_tokens": purged_tokens,
    }
