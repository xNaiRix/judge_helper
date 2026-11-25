"""Участники соревнования: карточки, дубликаты ФИО, категории."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import CompetitionStatus, Participant, User
from ..schemas import ParticipantIn, ParticipantOut, ParticipantUpdateIn
from ..services import (
    MAX_PARTICIPANTS,
    audit,
    require_admin,
    require_member,
    require_status,
)
from ..utils import normalize_fio

router = APIRouter(prefix="/competitions/{competition_id}/participants", tags=["Участники"])


def _get_participant(db: Session, competition_id: int, participant_id: int) -> Participant:
    p = db.get(Participant, participant_id)
    if p is None or p.competition_id != competition_id:
        raise HTTPException(status_code=404, detail="Участник не найден")
    return p


def _dup_detail(existing: Participant) -> dict:
    return {
        "message": "Участник с таким ФИО уже существует",
        "existing": ParticipantOut.model_validate(existing).model_dump(),
    }


@router.get("", response_model=list[ParticipantOut], summary="Список участников")
def list_participants(
    competition_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_member(db, competition_id, user)
    participants = db.execute(
        select(Participant)
        .where(Participant.competition_id == competition_id)
        .order_by(Participant.id)
    ).scalars()
    return [ParticipantOut.model_validate(p) for p in participants]


@router.post("", response_model=ParticipantOut, status_code=201, summary="Добавить участника")
def create_participant(
    competition_id: int,
    data: ParticipantIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    require_admin(comp, user)
    # Редактирование состава разрешено только до начала соревнования (ТЗ)
    require_status(comp, CompetitionStatus.planned)

    count = db.execute(
        select(func.count())
        .select_from(Participant)
        .where(Participant.competition_id == competition_id)
    ).scalar_one()
    if count >= MAX_PARTICIPANTS:
        raise HTTPException(
            status_code=400, detail=f"Достигнут максимальный лимит участников ({MAX_PARTICIPANTS})"
        )

    normalized = normalize_fio(data.last_name, data.first_name, data.patronymic)
    existing = db.execute(
        select(Participant).where(
            Participant.competition_id == competition_id,
            Participant.normalized_fio == normalized,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=_dup_detail(existing))

    participant = Participant(
        competition_id=competition_id,
        last_name=data.last_name.strip(),
        first_name=data.first_name.strip(),
        patronymic=data.patronymic.strip(),
        category=data.category.value,
        comment=data.comment.strip(),
        normalized_fio=normalized,
    )
    db.add(participant)
    db.flush()
    audit(
        db,
        request,
        user,
        "admin",
        "create",
        "participant",
        participant.id,
        new_value={"fio": normalized, "category": participant.category},
    )
    db.commit()
    db.refresh(participant)
    return ParticipantOut.model_validate(participant)


@router.patch(
    "/{participant_id}",
    response_model=ParticipantOut,
    summary="Редактировать карточку (только до начала)",
)
def update_participant(
    competition_id: int,
    participant_id: int,
    data: ParticipantUpdateIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    require_admin(comp, user)
    require_status(comp, CompetitionStatus.planned)

    participant = _get_participant(db, competition_id, participant_id)
    old_value = {
        "last_name": participant.last_name,
        "first_name": participant.first_name,
        "patronymic": participant.patronymic,
        "category": participant.category,
        "comment": participant.comment,
    }

    updates = data.model_dump(exclude_unset=True)
    for field in ("last_name", "first_name", "patronymic", "comment"):
        if field in updates and updates[field] is not None:
            setattr(participant, field, updates[field].strip())
    if "category" in updates and updates["category"] is not None:
        value = updates["category"]
        participant.category = value.value if hasattr(value, "value") else value

    # Пересчитываем нормализованное ФИО и проверяем дубликаты
    participant.normalized_fio = normalize_fio(
        participant.last_name, participant.first_name, participant.patronymic
    )
    existing = db.execute(
        select(Participant).where(
            Participant.competition_id == competition_id,
            Participant.normalized_fio == participant.normalized_fio,
            Participant.id != participant.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=_dup_detail(existing))

    audit(
        db,
        request,
        user,
        "admin",
        "update",
        "participant",
        participant.id,
        old_value=old_value,
        new_value={
            "last_name": participant.last_name,
            "first_name": participant.first_name,
            "patronymic": participant.patronymic,
            "category": participant.category,
            "comment": participant.comment,
        },
    )
    db.commit()
    db.refresh(participant)
    return ParticipantOut.model_validate(participant)


@router.delete("/{participant_id}", status_code=204, summary="Удалить карточку (только до начала)")
def delete_participant(
    competition_id: int,
    participant_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    require_admin(comp, user)
    require_status(comp, CompetitionStatus.planned)

    participant = _get_participant(db, competition_id, participant_id)
    audit(
        db,
        request,
        user,
        "admin",
        "delete",
        "participant",
        participant.id,
        old_value={"fio": participant.normalized_fio},
    )
    db.delete(participant)
    db.commit()
