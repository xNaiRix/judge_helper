"""Настройки пользователя и уведомления."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import Competition, Invite, Notification, User
from ..schemas import MyInvitationOut, NotificationOut, SettingsOut, SettingsUpdateIn
from ..services import audit

router = APIRouter(tags=["Пользователь"])


@router.get("/settings", response_model=SettingsOut, summary="Настройки уведомлений")
def get_settings(user: User = Depends(get_current_user)):
    return SettingsOut.model_validate(user)


@router.patch("/settings", response_model=SettingsOut, summary="Изменить настройки уведомлений")
def update_settings(
    data: SettingsUpdateIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    updates = data.model_dump(exclude_unset=True)
    old = {
        "notifications_enabled": user.notifications_enabled,
        "email_notifications_enabled": user.email_notifications_enabled,
    }
    for key, value in updates.items():
        setattr(user, key, value)
    audit(
        db,
        request,
        user,
        "judge",
        "update",
        "user_settings",
        user.id,
        old_value=old,
        new_value=updates,
    )
    db.commit()
    db.refresh(user)
    return SettingsOut.model_validate(user)


@router.get("/notifications", response_model=list[NotificationOut], summary="Список уведомлений")
def list_notifications(
    unread_only: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        query = query.where(Notification.read.is_(False))
    notifications = db.execute(query.order_by(Notification.id.desc())).scalars()
    return [NotificationOut.model_validate(n) for n in notifications]


@router.post(
    "/notifications/read-all", status_code=204, summary="Пометить все уведомления прочитанными"
)
def read_all_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for n in db.execute(
        select(Notification).where(Notification.user_id == user.id, Notification.read.is_(False))
    ).scalars():
        n.read = True
    db.commit()


@router.get(
    "/invitations",
    response_model=list[MyInvitationOut],
    summary="Мои активные приглашения (с данными соревнования)",
)
def my_invitations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    result = []
    for invite in db.execute(
        select(Invite)
        .where(Invite.email == user.email, Invite.status == "pending")
        .order_by(Invite.id)
    ).scalars():
        comp = db.get(Competition, invite.competition_id)
        if comp is None or comp.deleted:
            continue
        admin = db.get(User, comp.admin_id)
        result.append(
            MyInvitationOut(
                invite_id=invite.id,
                competition_id=comp.id,
                competition_name=comp.name,
                dates=comp.dates,
                place=comp.place,
                admin_name=admin.full_name if admin else "",
                admin_email=admin.email if admin else "",
                expires_at=invite.expires_at,
            )
        )
    return result
