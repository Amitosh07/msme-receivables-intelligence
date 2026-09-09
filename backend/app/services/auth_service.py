"""
Authentication service handling registration, password verification, and token issuance.
"""

import logging
from typing import Tuple
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.security import create_access_token, get_password_hash, verify_password
from backend.app.models.business import Business
from backend.app.models.membership import Membership
from backend.app.models.user import User

logger = logging.getLogger(__name__)


def register_tenant(
    db: Session,
    email: str,
    password: str,
    full_name: str,
    business_name: str,
) -> Tuple[User, Business, str]:
    """
    Atomically registers a new business tenant and owner user.

    1. Checks email uniqueness.
    2. Hashes password.
    3. Creates Business, User, and Membership with 'owner' role in a single transaction.
    4. Generates a signed JWT access token.

    Rolls back transaction on any failure.
    """
    normalized_email = email.strip().lower()

    # Check email uniqueness
    existing_user = db.scalar(
        select(User).where(User.email == normalized_email)
    )
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email address already exists.",
        )

    try:
        # Create Business
        business = Business(
            name=business_name.strip(),
            currency="USD",
        )
        db.add(business)
        db.flush()  # Generate business.id

        # Create User
        password_hash = get_password_hash(password)
        user = User(
            email=normalized_email,
            full_name=full_name.strip(),
            password_hash=password_hash,
            is_active=True,
        )
        db.add(user)
        db.flush()  # Generate user.id

        # Create Membership as owner
        membership = Membership(
            user_id=user.id,
            business_id=business.id,
            role="owner",
        )
        db.add(membership)

        db.commit()
        db.refresh(user)
        db.refresh(business)

        logger.info("Successfully registered tenant: business_id=%s, user_id=%s", business.id, user.id)

    except Exception as e:
        db.rollback()
        logger.error("Failed to register tenant: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to register account. Please try again.",
        ) from e

    token = create_access_token(
        subject=str(user.id),
        extra_claims={
            "business_id": str(business.id),
            "role": "owner",
        },
    )
    return user, business, token


def authenticate_user(
    db: Session,
    email: str,
    password: str,
) -> Tuple[User, Business, str]:
    """
    Authenticates a user via email and password, resolving their business tenant.

    Returns:
        Tuple of (User, Business, access_token)
    """
    normalized_email = email.strip().lower()

    user = db.scalar(
        select(User).where(User.email == normalized_email)
    )
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive.",
        )

    # Resolve primary business membership
    membership = db.scalar(
        select(Membership).where(Membership.user_id == user.id)
    )
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User has no associated business membership.",
        )

    business = db.scalar(
        select(Business).where(Business.id == membership.business_id)
    )
    if not business:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated business tenant not found.",
        )

    token = create_access_token(
        subject=str(user.id),
        extra_claims={
            "business_id": str(business.id),
            "role": membership.role,
        },
    )
    return user, business, token
