"""Итоговые результаты завершённого соревнования."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import (
    Category,
    CompetitionStatus,
    Judge,
    Participant,
    User,
)
from ..schemas import JudgeScoreOut, ParticipantOut, ResultRow
from ..services import participant_scores, require_member
from ..utils import mean, round2

router = APIRouter(prefix="/competitions/{competition_id}/results", tags=["Результаты"])

SORT_FIELDS = {"competition_score", "artistry_score", "technique_score"}


@router.get(
    "",
    response_model=list[ResultRow],
    summary="Результаты (после завершения; администратору — с разбивкой по судьям)",
)
def get_results(
    competition_id: int,
    sort_by: str = Query(
        "competition_score",
        pattern="^(competition_score|artistry_score|technique_score)$",
    ),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    category: Category | None = Query(None, description="Фильтр по категории"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    comp = require_member(db, competition_id, user)
    if comp.status != CompetitionStatus.finished.value:
        raise HTTPException(
            status_code=409, detail="Результаты доступны только после завершения соревнования"
        )
    is_admin = comp.admin_id == user.id

    participants = (
        db.execute(
            select(Participant)
            .where(Participant.competition_id == competition_id)
            .order_by(Participant.id)
        )
        .scalars()
        .all()
    )

    # Судьи соревнования — для разбивки (только администратору)
    judges_by_id: dict[int, User] = {}
    if is_admin:
        for j in db.execute(
            select(User)
            .join(Judge, Judge.user_id == User.id)
            .where(Judge.competition_id == competition_id)
        ).scalars():
            judges_by_id[j.id] = j

    rows: list[ResultRow] = []
    for p in participants:
        if category is not None and p.category != category.value:
            continue
        scores = participant_scores(db, competition_id, p.id)
        if scores:
            competition_score = round2(mean([s.artistry + s.technique for s in scores]))
            artistry_score = round2(mean([s.artistry for s in scores]))
            technique_score = round2(mean([s.technique for s in scores]))
        else:
            # Пустой набор оценок даёт null, а не 0 (ТЗ)
            competition_score = artistry_score = technique_score = None

        breakdown = None
        if is_admin:
            breakdown = [
                JudgeScoreOut(
                    judge=judges_by_id[s.judge_id].full_name,
                    email=judges_by_id[s.judge_id].email,
                    artistry=s.artistry,
                    technique=s.technique,
                    total=s.artistry + s.technique,
                    comment=s.comment,
                )
                for s in scores
                if s.judge_id in judges_by_id
            ]
        rows.append(
            ResultRow(
                participant=ParticipantOut.model_validate(p),
                competition_score=competition_score,
                artistry_score=artistry_score,
                technique_score=technique_score,
                judges=breakdown,
            )
        )

    # Сортировка по округлённому значению; участники без оценок — всегда в конце
    non_null = [r for r in rows if getattr(r, sort_by) is not None]
    nulls = [r for r in rows if getattr(r, sort_by) is None]
    non_null.sort(key=lambda r: getattr(r, sort_by), reverse=(order == "desc"))
    return non_null + nulls
