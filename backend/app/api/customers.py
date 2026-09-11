"""Tenant-scoped customer identity endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.dependencies import (
    TenantContext,
    get_db,
    get_tenant_context,
)
from backend.app.models.customer import Customer
from backend.app.models.payment import Payment
from backend.app.schemas.customer import CustomerCreate, CustomerResponse
from backend.app.schemas.payment import PaymentResponse
from backend.app.services.customer_identity import create_customer

router = APIRouter(prefix="/customers", tags=["Customers"])


@router.post("", response_model=CustomerResponse, status_code=status.HTTP_201_CREATED)
def create_customer_endpoint(
    payload: CustomerCreate,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> CustomerResponse:
    try:
        customer = create_customer(
            db,
            business_id=tenant_ctx.business_id,
            display_name=payload.display_name,
            gstin=payload.gstin,
            customer_ref=payload.customer_ref,
        )
        db.commit()
        db.refresh(customer)
        return CustomerResponse.model_validate(customer)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("", response_model=list[CustomerResponse])
def list_customers(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[CustomerResponse]:
    customers = db.scalars(
        select(Customer)
        .where(Customer.business_id == tenant_ctx.business_id)
        .order_by(Customer.display_name, Customer.id)
        .offset(skip)
        .limit(limit)
    ).all()
    return [CustomerResponse.model_validate(customer) for customer in customers]


@router.get("/{customer_id}", response_model=CustomerResponse)
def get_customer(
    customer_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> CustomerResponse:
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.business_id == tenant_ctx.business_id,
        )
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )
    return CustomerResponse.model_validate(customer)


@router.get("/{customer_id}/payments", response_model=list[PaymentResponse])
def list_customer_payments(
    customer_id: uuid.UUID,
    tenant_ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[PaymentResponse]:
    customer = db.scalar(
        select(Customer).where(
            Customer.id == customer_id,
            Customer.business_id == tenant_ctx.business_id,
        )
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer not found.",
        )
    payments = db.scalars(
        select(Payment)
        .where(
            Payment.business_id == tenant_ctx.business_id,
            Payment.customer_identity_key == f"customer:{customer.id}",
        )
        .order_by(Payment.payment_date, Payment.id)
    ).all()
    return [PaymentResponse.model_validate(payment) for payment in payments]
