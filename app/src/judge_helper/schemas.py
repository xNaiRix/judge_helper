"""Pydantic-схемы (входные и выходные DTO)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Category

EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class RegisterIn(BaseModel):
    email: str = Field(max_length=255)
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=6, max_length=128)
    adult: bool  # обязательное подтверждение 18+

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        import re

        v = v.strip().lower()
        if not re.match(EMAIL_RE, v):
            raise ValueError("Некорректный email")
        return v

    @field_validator("adult")
    @classmethod
    def check_adult(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Подтверждение совершеннолетия (18+) обязательно")
        return v


class LoginIn(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    email_confirmed: bool = False
    notifications_enabled: bool = True
    email_notifications_enabled: bool = False


class RegisterOut(BaseModel):
    """Ответ регистрации: access/refresh НЕ выдаются до подтверждения email."""

    user: UserOut
    # Токен подтверждения возвращается только в демо-режиме (ECHO_EMAIL_TOKENS);
    # в production письмо уходит на email пользователя.
    confirmation_token: str | None = None
    message: str = "Зарегистрировано. Подтвердите email, чтобы войти."


class LoginOut(BaseModel):
    access_token: str
    refresh_token: str
    user: UserOut


class RefreshIn(BaseModel):
    refresh_token: str


class RefreshOut(BaseModel):
    access_token: str
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str | None = None


class ConfirmEmailIn(BaseModel):
    token: str


class ForgotPasswordIn(BaseModel):
    email: str


class ForgotPasswordOut(BaseModel):
    message: str = "Если аккаунт существует, письмо со ссылкой для сброса отправлено"
    reset_token: str | None = None  # только в демо-режиме


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str = Field(min_length=6, max_length=128)


class GoogleUrlOut(BaseModel):
    url: str
    message: str = ""


# --------------------------- Соревнования ----------------------------------


class CompetitionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    place: str = Field(min_length=1, max_length=200)
    dates: str = Field(min_length=1, max_length=200)


class JudgeBrief(BaseModel):
    id: int
    full_name: str
    email: str


class CompetitionOut(BaseModel):
    id: int
    name: str
    place: str
    dates: str
    status: str
    is_admin: bool


class CompetitionDetailOut(CompetitionOut):
    judges: list[JudgeBrief]


class InviteIn(BaseModel):
    email: str = Field(max_length=255)

    @field_validator("email")
    @classmethod
    def check_email(cls, v: str) -> str:
        import re

        v = v.strip().lower()
        if not re.match(EMAIL_RE, v):
            raise ValueError("Некорректный email")
        return v


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    status: str
    created_at: datetime
    expires_at: datetime


class ParticipantIn(BaseModel):
    last_name: str = Field(min_length=1, max_length=100)
    first_name: str = Field(min_length=1, max_length=100)
    patronymic: str = Field(default="", max_length=100)
    category: Category
    comment: str = Field(default="", max_length=1000)


class ParticipantUpdateIn(BaseModel):
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    patronymic: str | None = Field(default=None, max_length=100)
    category: Category | None = None
    comment: str | None = Field(default=None, max_length=1000)


class ParticipantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    last_name: str
    first_name: str
    patronymic: str
    category: str
    comment: str


class ScoreIn(BaseModel):
    artistry: int = Field(ge=0, le=10, description="Оценка за артистизм: целое число 0..10")
    technique: int = Field(ge=0, le=10, description="Оценка за технику: целое число 0..10")
    comment: str = Field(default="", max_length=2000)


class ScoreOut(BaseModel):
    participant_id: int
    artistry: int
    technique: int
    total: int
    comment: str


class MyScoreOut(BaseModel):
    """Своя оценка с карточкой участника (список «мои оценки»)."""

    participant: ParticipantOut
    artistry: int
    technique: int
    total: int
    comment: str


class SyncItemIn(BaseModel):
    """Элемент локальной офлайн-очереди оценок."""

    participant_id: int
    artistry: int = Field(ge=0, le=10)
    technique: int = Field(ge=0, le=10)
    comment: str = Field(default="", max_length=2000)
    client_time: datetime  # время клиента (для LWW)


class SyncIn(BaseModel):
    items: list[SyncItemIn] = Field(min_length=1, max_length=1000)


class SyncItemOut(BaseModel):
    participant_id: int
    applied: bool
    reason: str  # applied / older / equal_server_time_wins


class SyncOut(BaseModel):
    applied: int
    skipped: int
    results: list[SyncItemOut]


class JudgeScoreOut(BaseModel):
    judge: str
    email: str
    artistry: int
    technique: int
    total: int
    comment: str


class ResultRow(BaseModel):
    participant: ParticipantOut
    competition_score: float | None  # соревновательный балл
    artistry_score: float | None  # итоговая артистичность
    technique_score: float | None  # итоговая техника
    judges: list[JudgeScoreOut] | None = None  # только для администратора


class FinishOut(BaseModel):
    status: str
    unmarked_participants: list[str]
    warnings: list[str]


# ------------------------- Уведомления и настройки -------------------------


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    notifications_enabled: bool
    email_notifications_enabled: bool


class SettingsUpdateIn(BaseModel):
    notifications_enabled: bool | None = None
    email_notifications_enabled: bool | None = None


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    text: str
    read: bool
    created_at: datetime


class MyInvitationOut(BaseModel):
    """Приглашение, адресованное текущему пользователю (ТЗ: в приглашении
    видны название, даты, данные администратора и id соревнования)."""

    invite_id: int
    competition_id: int
    competition_name: str
    dates: str
    place: str
    admin_name: str
    admin_email: str
    expires_at: datetime


# --------------------------- Статистика по судьям ---------------------------


class JudgeStatEntry(BaseModel):
    participant: str
    category: str
    artistry: int  # балл судьи за артистичность
    artistry_final: float | None  # итоговая артистичность
    technique: int  # балл судьи за технику
    technique_final: float | None  # итоговая техника
    total: int  # итоговый балл судьи
    competition_score: float | None  # соревновательный балл


class JudgeStatsRow(BaseModel):
    judge_id: int
    full_name: str
    email: str
    avg_artistry: float | None
    avg_technique: float | None
    avg_total: float | None
    entries: list[JudgeStatEntry]
