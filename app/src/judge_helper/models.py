"""ORM-модели приложения «Помощник судьи»."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    """Наивный UTC (SQLite не сохраняет tzinfo корректно)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CompetitionStatus(str, enum.Enum):
    planned = "planned"  # не начато
    started = "started"  # начато
    finished = "finished"  # завершено


class Category(str, enum.Enum):
    children = "Дети"
    juniors = "Юниоры"
    adults = "Взрослые"
    veterans = "Ветераны"


class User(Base):
    """Судья (зарегистрированный пользователь)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(512))
    # подтверждение email обязательно для входа при регистрации по email (ТЗ)
    email_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # версия токенов: bump завершает все сессии (сброс пароля)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    # настройки уведомлений (ТЗ «Уведомления»)
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Competition(Base):
    """Соревнование. Создатель становится администратором и судьёй."""

    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    place: Mapped[str] = mapped_column(String(200))
    dates: Mapped[str] = mapped_column(String(200))  # дата/диапазон/перечисление дат (ДД.ММ.ГГГГ)
    status: Mapped[str] = mapped_column(String(16), default=CompetitionStatus.planned.value)
    admin_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # soft-delete
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    judges: Mapped[list["Judge"]] = relationship(back_populates="competition")


class Judge(Base):
    """Связь «судья — соревнование»."""

    __tablename__ = "judges"
    __table_args__ = (UniqueConstraint("competition_id", "user_id", name="uq_judge_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    competition: Mapped[Competition] = relationship(back_populates="judges")


class Invite(Base):
    """Приглашение судьи в соревнование (срок действия — 1 неделя)."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"))
    email: Mapped[str] = mapped_column(String(255), index=True)
    # pending / accepted / declined / expired
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Participant(Base):
    """Карточка участника соревнования."""

    __tablename__ = "participants"
    __table_args__ = (
        UniqueConstraint("competition_id", "normalized_fio", name="uq_participant_fio"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), index=True)
    last_name: Mapped[str] = mapped_column(String(100))
    first_name: Mapped[str] = mapped_column(String(100))
    patronymic: Mapped[str] = mapped_column(String(100), default="")
    category: Mapped[str] = mapped_column(String(20))  # значение из Category
    comment: Mapped[str] = mapped_column(Text, default="")
    normalized_fio: Mapped[str] = mapped_column(String(255))  # для проверки дублей


class Score(Base):
    """Оценка судьи участнику в соревновании.

    Уникальность (judge_id, participant_id, competition_id) гарантируется
    ограничением БД. Для офлайн-синхронизации хранится клиентское время
    последней записи (LWW: по клиентскому времени, при равенстве — по времени
    приёма сервером).
    """

    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint("competition_id", "judge_id", "participant_id", name="uq_score_trio"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), index=True)
    judge_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id"))
    artistry: Mapped[int] = mapped_column(Integer)  # 0..10
    technique: Mapped[int] = mapped_column(Integer)  # 0..10
    comment: Mapped[str] = mapped_column(Text, default="")
    client_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    server_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuthToken(Base):
    """Одноразовые токены: подтверждение email (срок 24ч) и сброс пароля (24ч)."""

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(16))  # confirm / reset
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RevokedToken(Base):
    """Отозванные JWT (logout): jti + срок, для проверки отзыва."""

    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    """Push-уведомление пользователя (внутренняя доставка)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # invited / removed / started / finished / deleted / invite_status /
    # score_overwritten
    kind: Mapped[str] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmailOutbox(Base):
    """Исходящие email-дубликаты уведомлений (имитация отправки почты)."""

    __tablename__ = "email_outbox"

    id: Mapped[int] = mapped_column(primary_key=True)
    to_email: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """Аудит-лог (хранение 6 месяцев по ТЗ).

    Для офлайн-конфликтов — пометка conflict_overwritten: в old_value хранится
    проигравшая запись, в new_value — победившая.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="")  # admin / judge / system
    # create / update / delete / start / finish / invite / accept /
    # decline / score / conflict
    action: Mapped[str] = mapped_column(String(32))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    conflict_overwritten: Mapped[bool] = mapped_column(Boolean, default=False)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    request_id: Mapped[str] = mapped_column(String(32), default="")
