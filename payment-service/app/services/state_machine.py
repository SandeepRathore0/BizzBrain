import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictException
from app.models.audit import AuditLog
from app.models.payment import Payment

# Allowed Payment Status Transitions
# CREATED -> PAYER_APPROVED, CANCELLED, EXPIRED, FAILED
# PAYER_APPROVED -> CAPTURE_PENDING, CANCELLED, EXPIRED, FAILED
# CAPTURE_PENDING -> COMPLETED, FAILED, REQUIRES_REVIEW
# REQUIRES_REVIEW -> COMPLETED, FAILED
# COMPLETED -> REFUNDED
# Terminal states: FAILED, CANCELLED, EXPIRED, REFUNDED
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "CREATED": {"PAYER_APPROVED", "CANCELLED", "EXPIRED", "FAILED"},
    "PAYER_APPROVED": {"CAPTURE_PENDING", "CANCELLED", "EXPIRED", "FAILED"},
    "CAPTURE_PENDING": {"COMPLETED", "FAILED", "REQUIRES_REVIEW"},
    "REQUIRES_REVIEW": {"COMPLETED", "FAILED"},
    "COMPLETED": {"REFUNDED"},
    "FAILED": set(),
    "CANCELLED": set(),
    "EXPIRED": set(),
    "REFUNDED": set(),
}

TERMINAL_STATUSES: set[str] = {"FAILED", "CANCELLED", "EXPIRED", "REFUNDED"}

# Allowed Allocation Status Transitions
# NOT_APPLICABLE -> PENDING (only on COMPLETED)
# PENDING -> ALLOCATED, REJECTED (from Invoice Service)
# ALLOCATED -> terminal (no reversals in Stage 1)
# REJECTED -> terminal (manual refund in PayPal dashboard)
ALLOWED_ALLOCATION_TRANSITIONS: dict[str, set[str]] = {
    "NOT_APPLICABLE": {"PENDING"},
    "PENDING": {"ALLOCATED", "REJECTED"},
    "ALLOCATED": set(),
    "REJECTED": set(),
}


async def transition_payment(
    db: AsyncSession,
    payment: Payment,
    new_status: str,
    actor_type: str,
    actor_id: Optional[str] = None,
    new_allocation_status: Optional[str] = None,
    status_reason: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Payment:
    """Single state machine function for all payment transitions.
    
    Every transition enforces strict allowed transitions and writes an audit row
    in the same transaction.
    """
    current_status = payment.status
    current_allocation = payment.allocation_status

    # Terminal states can never transition to anything
    if current_status in TERMINAL_STATUSES:
        raise ConflictException(
            code="INVALID_STATE_TRANSITION",
            message=f"Payment is in terminal state '{current_status}' and cannot be transitioned.",
            details={"current_status": current_status, "target_status": new_status},
        )

    # Validate Payment Status Transition
    if new_status != current_status:
        allowed = ALLOWED_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            raise ConflictException(
                code="INVALID_STATE_TRANSITION",
                message=f"Cannot transition payment from {current_status} to {new_status}.",
                details={"current_status": current_status, "target_status": new_status},
            )
    else:
        # Same status is only allowed when allocation_status is changing
        if not new_allocation_status or new_allocation_status == current_allocation:
            raise ConflictException(
                code="INVALID_STATE_TRANSITION",
                message=f"Payment is already in state '{current_status}'.",
                details={"current_status": current_status, "target_status": new_status},
            )

    # Validate Allocation Status Transition if provided
    if new_allocation_status and new_allocation_status != current_allocation:
        allowed_alloc = ALLOWED_ALLOCATION_TRANSITIONS.get(current_allocation, set())
        if new_allocation_status not in allowed_alloc:
            raise ConflictException(
                code="INVALID_STATE_TRANSITION",
                message=f"Cannot transition allocation_status from {current_allocation} to {new_allocation_status}.",
                details={"current_allocation": current_allocation, "target_allocation": new_allocation_status},
            )

    before_state = {
        "status": current_status,
        "allocation_status": current_allocation,
        "status_reason": payment.status_reason,
        "updated_at": payment.updated_at.isoformat() if payment.updated_at else None,
    }

    # Apply State Changes
    payment.status = new_status
    if new_allocation_status:
        payment.allocation_status = new_allocation_status
    if status_reason is not None:
        payment.status_reason = status_reason
    payment.updated_at = datetime.now(timezone.utc)

    after_state = {
        "status": payment.status,
        "allocation_status": payment.allocation_status,
        "status_reason": payment.status_reason,
        "updated_at": payment.updated_at.isoformat(),
    }

    # Record Audit Row in same transaction
    audit_row = AuditLog(
        audit_id=uuid.uuid4(),
        business_id=payment.business_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=f"PAYMENT_STATUS_TRANSITION_{new_status}",
        entity_type="payment",
        entity_id=payment.payment_id,
        before=before_state,
        after=after_state,
        request_id=request_id,
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(audit_row)

    return payment
