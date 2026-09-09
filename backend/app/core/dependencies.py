"""
FastAPI route dependencies for authentication, database session, and tenant resolution.
"""

import uuid
from dataclasses import dataclass
from typing import Generator
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import decode_access_token
from backend.app.db.session import get_db
from backend.app.models.business import Business
from backend.app.models.membership import Membership
from backend.app.models.user import User

# HTTP Bearer authentication scheme for OpenAPI / Swagger UI
http_bearer = HTTPBearer(
    bearerFormat="JWT",
    description="Enter your JWT Bearer token",
    auto_error=True,
)
oauth2_scheme = http_bearer  # Retained for backward compatibility


@dataclass
class TenantContext:
    """
    Encapsulates the verified tenant execution context for an authenticated request.
    Server-side authoritative — never trusts client-supplied tenant IDs.
    """
    user: User
    business: Business
    role: str

    @property
    def business_id(self) -> uuid.UUID:
        return self.business.id

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


def get_current_user(
    auth: HTTPAuthorizationCredentials = Depends(http_bearer),
    db: Session = Depends(get_db),
) -> User:
    """
    Decodes JWT token and validates that the user exists and is active.
    Raises 401 on invalid/expired token, 403 if inactive.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate authentication credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token = auth.credentials
    try:
        payload = decode_access_token(token)
        user_id_str: str | None = payload.get("sub")
        if not user_id_str:
            raise credentials_exception
        user_id = uuid.UUID(user_id_str)
    except Exception as e:
        raise credentials_exception from e

    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user account.",
        )

    return user


def get_current_business(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Business:
    """
    Resolves the authenticated user's business tenant from server-side membership.
    Never trusts client-supplied tenant IDs.
    """
    membership = db.scalar(
        select(Membership).where(Membership.user_id == current_user.id)
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to any business tenant.",
        )

    business = db.scalar(
        select(Business).where(Business.id == membership.business_id)
    )
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business tenant record not found.",
        )

    return business


def get_tenant_context(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TenantContext:
    """
    Provides the full authenticated TenantContext (user, business, role).
    """
    membership = db.scalar(
        select(Membership).where(Membership.user_id == current_user.id)
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User does not belong to any business tenant.",
        )

    business = db.scalar(
        select(Business).where(Business.id == membership.business_id)
    )
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Business tenant record not found.",
        )

    return TenantContext(
        user=current_user,
        business=business,
        role=membership.role,
    )
