from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, SoftDelete, TenantScoped, Timestamps, UUIDPk


class Organization(UUIDPk, Timestamps, SoftDelete, Base):
    """Tenant. Every business record is scoped to an organisation."""

    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    reporting_currency: Mapped[str] = mapped_column(String(3), default="GBP")
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/London")
    country_code: Mapped[str | None] = mapped_column(String(2))
    demo_mode: Mapped[bool] = mapped_column(Boolean, default=False)


class Permission(UUIDPk, Base):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"),
                                               primary_key=True)
    permission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True),
                                                     ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)


class Role(UUIDPk, Timestamps, Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True),
                                                        ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(100))
    is_system: Mapped[bool] = mapped_column(Boolean, default=True)
    permissions: Mapped[list[Permission]] = relationship(secondary="role_permissions", lazy="selectin")


class User(UUIDPk, TenantScoped, Timestamps, SoftDelete, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)
    email: Mapped[str] = mapped_column(String(254))
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("roles.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict] = mapped_column(JSONB, default=dict)
    role: Mapped[Role] = relationship(lazy="joined", innerjoin=True)


class UserSession(UUIDPk, TenantScoped, Timestamps, Base):
    """Refresh-token session. Tokens rotate on every use; reuse revokes the family."""

    __tablename__ = "sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                                               index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(64))
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
