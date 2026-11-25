"""Оценки судей: сохранение, «мои оценки» и офлайн-синхронизация (LWW)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import CompetitionStatus, Participant, Score, User, utcnow
from ..schemas import (
    MyScoreOut,
    ParticipantOut,
    ScoreIn,
    ScoreOut,
    SyncIn,
    SyncItemOut,
    SyncOut,
)
from ..services import audit, notify_user, require_member, require_status

router = APIRouter(prefix="/competitions/{competition_id}/scores", tags=["Оценки"])


def _score_dict(s: Score) -> dict:
    return {
        "artistry": s.artistry,
        "technique": s.technique,
        "comment": s.comment,
        "client_time": s.client_time.isoformat() if s.client_time else None,
    }


def _find_score(
    db: Session,
    competition_id: int,
    judge_id: int,
    participant_id: int,
) -> Score | None:
    return db.execute(
        select(Score).where(
            Score.competition_id == competition_id,
            Score.judge_id == judge_id,
            Score.participant_id == participant_id,
        )
    ).scalar_one_or_none()


def _get_participant(db: Session, competition_id: int, participant_id: int) -> Participant:
    participant = db.get(Participant, participant_id)
    if participant is None or participant.competition_id != competition_id:
        raise HTTPException(status_code=404, detail="Участник не найден")
    return participant


@router.put(
    "/participants/{participant_id}",
    response_model=ScoreOut,
    summary="Выставить/обновить оценку (после начала, до завершения)",
)
def save_score(
    competition_id: int,
    participant_id: int,
    data: ScoreIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    # Оценки можно выставлять только после начала и до завершения (ТЗ)
    require_status(comp, CompetitionStatus.started)

    _get_participant(db, competition_id, participant_id)
    new_value = {
        "artistry": data.artistry,
        "technique": data.technique,
        "comment": data.comment.strip(),
    }

    # Оценка привязана к (судья, участник, соревнование): повтор — обновление, не дубль
    score = _find_score(db, competition_id, user.id, participant_id)
    if score is None:
        score = Score(
            competition_id=competition_id,
            judge_id=user.id,
            participant_id=participant_id,
            artistry=data.artistry,
            technique=data.technique,
            comment=data.comment.strip(),
            client_time=utcnow(),
            server_received_at=utcnow(),
        )
        db.add(score)
        old_value = None
    else:
        old_value = _score_dict(score)
        score.artistry = data.artistry
        score.technique = data.technique
        score.comment = data.comment.strip()
        score.client_time = utcnow()
        score.server_received_at = utcnow()

    audit(
        db,
        request,
        user,
        "judge",
        "update" if old_value else "create",
        "score",
        f"{competition_id}/{user.id}/{participant_id}",
        old_value=old_value,
        new_value=new_value,
    )
    db.commit()
    return ScoreOut(
        participant_id=participant_id,
        artistry=score.artistry,
        technique=score.technique,
        total=score.artistry + score.technique,
        comment=score.comment,
    )


@router.post(
    "/sync", response_model=SyncOut, summary="Офлайн-синхронизация локальной очереди оценок (LWW)"
)
def sync_scores(
    competition_id: int,
    data: SyncIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Last-write-wins по времени клиента; при равенстве — по времени приёма
    сервером (позже принятая побеждает). Проигравшая запись сохраняется в
    аудит-логе с пометкой conflict_overwritten; дублей в БД не возникает."""
    comp = require_member(db, competition_id, user)
    require_status(comp, CompetitionStatus.started)

    results: list[SyncItemOut] = []
    applied = skipped = 0
    now = utcnow()

    for item in data.items:
        participant = _get_participant(db, competition_id, item.participant_id)
        incoming = {
            "artistry": item.artistry,
            "technique": item.technique,
            "comment": item.comment.strip(),
            "client_time": item.client_time.replace(tzinfo=None).isoformat(),
        }
        existing = _find_score(db, competition_id, user.id, participant.id)

        if existing is None:
            db.add(
                Score(
                    competition_id=competition_id,
                    judge_id=user.id,
                    participant_id=participant.id,
                    artistry=item.artistry,
                    technique=item.technique,
                    comment=item.comment.strip(),
                    client_time=item.client_time.replace(tzinfo=None),
                    server_received_at=now,
                )
            )
            audit(
                db,
                request,
                user,
                "judge",
                "create",
                "score",
                f"{competition_id}/{user.id}/{participant.id}",
                new_value=incoming,
            )
            applied += 1
            results.append(
                SyncItemOut(participant_id=participant.id, applied=True, reason="applied")
            )
            continue

        # LWW: новое клиентское время побеждает; при равенстве — по времени
        # приёма сервером (позже принятая запись побеждает, ТЗ)
        if item.client_time.replace(tzinfo=None) >= existing.client_time:
            audit(
                db,
                request,
                user,
                "judge",
                "update",
                "score",
                f"{competition_id}/{user.id}/{participant.id}",
                old_value=_score_dict(existing),
                new_value=incoming,
            )
            existing.artistry = item.artistry
            existing.technique = item.technique
            existing.comment = item.comment.strip()
            existing.client_time = item.client_time.replace(tzinfo=None)
            existing.server_received_at = now
            # Ненавязчивое уведомление об обновлении с другого устройства (ТЗ)
            notify_user(
                db, user, "score_overwritten", "Оценка была обновлена с другого устройства"
            )
            applied += 1
            results.append(
                SyncItemOut(participant_id=participant.id, applied=True, reason="applied")
            )
        else:
            # Проигравшая (входящая) запись — в аудит с пометкой конфликта
            audit(
                db,
                request,
                user,
                "judge",
                "conflict",
                "score",
                f"{competition_id}/{user.id}/{participant.id}",
                old_value=_score_dict(existing),
                new_value=incoming,
                conflict=True,
            )
            skipped += 1
            results.append(
                SyncItemOut(
                    participant_id=participant.id,
                    applied=False,
                    reason="older",
                )
            )

    db.commit()
    return SyncOut(applied=applied, skipped=skipped, results=results)


@router.get(
    "/my",
    response_model=list[MyScoreOut],
    summary="Свои оценки с сортировкой и фильтром по категории",
)
def my_scores(
    competition_id: int,
    sort_by: str = Query("total", pattern="^(artistry|technique|total)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    category: str | None = Query(None, description="Фильтр по категории"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_member(db, competition_id, user)

    rows = db.execute(
        select(Score, Participant)
        .join(Participant, Participant.id == Score.participant_id)
        .where(Score.competition_id == competition_id, Score.judge_id == user.id)
    ).all()
    items = [
        MyScoreOut(
            participant=ParticipantOut.model_validate(p),
            artistry=s.artistry,
            technique=s.technique,
            total=s.artistry + s.technique,
            comment=s.comment,
        )
        for s, p in rows
    ]
    if category is not None:
        items = [i for i in items if i.participant.category == category]
    items.sort(
        key=lambda i: getattr(i, sort_by),
        reverse=(order == "desc"),
    )
    return items
