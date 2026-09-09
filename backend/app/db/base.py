"""
Database Base import module for Alembic and metadata discovery.
Imports Base and all models so target_metadata contains all tables.
"""

from backend.app.models.base import Base
from backend.app.models.business import Business
from backend.app.models.customer import Customer
from backend.app.models.forecast import CashflowForecast
from backend.app.models.invoice import Invoice
from backend.app.models.invoice_document import InvoiceDocument
from backend.app.models.membership import Membership
from backend.app.models.payment import Payment
from backend.app.models.prediction import PredictionResult
from backend.app.models.task import Task
from backend.app.models.user import User

__all__ = [
    "Base",
    "Business",
    "Customer",
    "CashflowForecast",
    "Invoice",
    "InvoiceDocument",
    "Membership",
    "Payment",
    "PredictionResult",
    "Task",
    "User",
]
