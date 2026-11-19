from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Role


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LaneRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    access_point_name: Optional[str] = None
    campus_name: Optional[str] = None


class CurrentUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    full_name: str
    email: Optional[str] = None
    role: Role
    must_change_password: bool = False
    default_lane: Optional[LaneRef] = None
    last_login_at: Optional[datetime] = None


class LoginResponse(BaseModel):
    user: CurrentUser
    csrf_token: str


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=10, max_length=256)
