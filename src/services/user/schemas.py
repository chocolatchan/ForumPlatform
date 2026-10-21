"""User Service Pydantic schemas for authentication and account lifecycle."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# --- Public/Shared Schemas ---
class UserPublic(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str | None = None
    bio: str | None = None
    role: str


class UserProfileOwn(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str | None = None
    bio: str | None = None
    email: str
    role: str
    created_at: datetime


class UserAdminView(BaseModel):
    id: uuid.UUID
    username: str
    display_name: str | None = None
    email: str
    role: str
    is_banned: bool
    ban_reason: str | None = None
    created_at: datetime


class SessionMetadata(BaseModel):
    idle_expires_at: datetime
    absolute_expires_at: datetime


class LoginResponse(BaseModel):
    user: UserPublic
    session: SessionMetadata


class RegistrationPendingResponse(BaseModel):
    status: str = "pending_verification"
    message: str = "If the registration details are eligible, a verification code has been sent."


class NeutralAcceptedResponse(BaseModel):
    status: str = "accepted"
    message: str = "If details are eligible, the request will be processed."


class VerifyOtpResponse(BaseModel):
    user: UserPublic


# --- Request Schemas ---
class RegisterRequest(BaseModel):
    username: str = Field(..., pattern=r"^[A-Za-z0-9_]{3,32}$")
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8)


class VerifyOtpRequest(BaseModel):
    email: str
    otp: str = Field(..., pattern=r"^[0-9]{6}$")


class ResendOtpRequest(BaseModel):
    email: str


class LoginRequest(BaseModel):
    login: str
    password: str


class RecoveryRequest(BaseModel):
    email: str


class RecoveryVerifyRequest(BaseModel):
    email: str
    otp: str = Field(..., pattern=r"^[0-9]{6}$")


class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str = Field(..., min_length=8)


class UpdateProfileRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=50)
    bio: str | None = Field(default=None, max_length=200)


class ChangeEmailRequest(BaseModel):
    new_email: str


class ChangeEmailVerifyRequest(BaseModel):
    otp: str = Field(..., pattern=r"^[0-9]{6}$")


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=8)


class ChangeUserRoleRequest(BaseModel):
    role: str = Field(..., pattern=r"^(member|moderator|admin)$")


class BanUserRequest(BaseModel):
    is_banned: bool
    ban_reason: str | None = None
