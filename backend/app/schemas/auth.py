"""
Pydantic schemas for Authentication endpoints.
"""

import uuid
from pydantic import BaseModel, EmailStr, Field
from backend.app.schemas.user import UserResponse
from backend.app.schemas.business import BusinessResponse


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters")
    full_name: str = Field(..., min_length=1, max_length=255)
    business_name: str = Field(..., min_length=1, max_length=255)


class RegisterResponse(BaseModel):
    message: str = "Registration successful"
    user: UserResponse
    business: BusinessResponse
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
    business: BusinessResponse
