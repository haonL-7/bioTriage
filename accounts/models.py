"""账户系统 ORM 模型：users / stars / evaluations / usage_counters。"""
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    orcid_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # 管理员能力：封禁标记 + 个人配额覆盖（NULL = 用全局默认 AI_DAILY_QUOTA_PER_USER）
    is_banned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    quota_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)


# 旧库的 users 表没有这两列：db.migrate_user_columns() 依赖这份定义生成 ALTER TABLE 的列 DDL，
# 与模型同源，避免迁移用的类型/默认值和 ORM 漂移。
USER_ADDED_COLUMNS = {
    "is_banned": User.__table__.c.is_banned,
    "quota_limit": User.__table__.c.quota_limit,
}


class Star(Base):
    __tablename__ = "stars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_orcid: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="")
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (UniqueConstraint("input_hash", name="uq_evaluations_input_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_orcid: Mapped[str] = mapped_column(String(64), index=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[str] = mapped_column(Text, default="")
    response_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthToken(Base):
    """Bearer token（GitHub Pages 账户页用）：只存 sha256 哈希，明文只出现在 302 的 fragment 里。"""

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_orcid: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageCounter(Base):
    __tablename__ = "usage_counters"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
