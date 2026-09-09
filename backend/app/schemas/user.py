"""
Pydantic schemas for User entity.
"""

import uuid
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr


class UserBase(BaseModel):
    email: EmailStr
    full_name: str


class UserCreate(UserBase):
    password: str


class UserResponse(UserBase):
    id: uuid.UUID
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CurrentUserResponse(UserResponse):
    business_id: uuid.UUID
    business_name: str
    role: str

    model_config = ConfigDict(from_attributes=True)
