"""
Authentication API endpoints.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.app.core.dependencies import TenantContext, get_db, get_tenant_context
from backend.app.schemas.auth import LoginRequest, RegisterRequest, RegisterResponse, TokenResponse
from backend.app.schemas.business import BusinessResponse
from backend.app.schemas.user import CurrentUserResponse, UserResponse
from backend.app.services.auth_service import authenticate_user, register_tenant

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new business tenant and user account",
)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
) -> RegisterResponse:
    """
    Registers a new MSME business tenant with an initial owner user account.
    Creates Business, User, and Membership atomically within one transaction.
    """
    user, business, token = register_tenant(
        db=db,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
        business_name=payload.business_name,
    )
    return RegisterResponse(
        message="Registration successful",
        user=UserResponse.model_validate(user),
        business=BusinessResponse.model_validate(business),
        access_token=token,
        token_type="bearer",
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in and retrieve a JWT access token",
)
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """
    Authenticates a user via email and password, returning a signed JWT access token.
    The client must provide this token in the 'Authorization: Bearer <token>' header.
    """
    user, business, token = authenticate_user(
        db=db,
        email=payload.email,
        password=payload.password,
    )
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse.model_validate(user),
        business=BusinessResponse.model_validate(business),
    )


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    summary="Get details of currently authenticated user and business",
)
def get_me(
    tenant_ctx: TenantContext = Depends(get_tenant_context),
) -> CurrentUserResponse:
    """
    Returns the authenticated user's profile and their associated business tenant.
    Never returns password hash or sensitive credentials.
    """
    return CurrentUserResponse(
        id=tenant_ctx.user.id,
        email=tenant_ctx.user.email,
        full_name=tenant_ctx.user.full_name,
        is_active=tenant_ctx.user.is_active,
        created_at=tenant_ctx.user.created_at,
        business_id=tenant_ctx.business.id,
        business_name=tenant_ctx.business.name,
        role=tenant_ctx.role,
    )
