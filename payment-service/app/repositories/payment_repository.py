import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditLog
from app.models.payment import Payment
from app.models.provider_reference import PaymentProviderReference


class PaymentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_payment_by_id(
        self,
        business_id: uuid.UUID,
        payment_id: uuid.UUID,
        for_update: bool = False,
    ) -> Optional[Payment]:
        """Fetch payment scoped to business_id. Returns None if belongs to another business."""
        stmt = (
            select(Payment)
            .where(
                Payment.business_id == business_id,
                Payment.payment_id == payment_id,
            )
            .options(selectinload(Payment.provider_reference))
        )
        if for_update:
            stmt = stmt.with_for_update()

        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def expire_stale_created_payments(
        self,
        business_id: uuid.UUID,
        invoice_id: uuid.UUID,
        now: Optional[datetime] = None,
        request_id: Optional[str] = None,
    ) -> int:
        """Expire stale CREATED payments whose expires_at has passed for this invoice."""
        current_time = now or datetime.now(timezone.utc)
        stmt = (
            select(Payment)
            .where(
                Payment.business_id == business_id,
                Payment.invoice_id == invoice_id,
                Payment.status == "CREATED",
                Payment.expires_at <= current_time,
            )
            .with_for_update()
        )
        result = await self.db.execute(stmt)
        stale_payments = result.scalars().all()

        for p in stale_payments:
            p.status = "EXPIRED"
            p.status_reason = "EXPIRED_BEFORE_APPROVAL"
            p.updated_at = current_time

            audit = AuditLog(
                audit_id=uuid.uuid4(),
                business_id=business_id,
                actor_type="SYSTEM",
                actor_id=None,
                action="PAYMENT_STATUS_TRANSITION_EXPIRED",
                entity_type="payment",
                entity_id=p.payment_id,
                before={"status": "CREATED"},
                after={"status": "EXPIRED", "reason": "EXPIRED_BEFORE_APPROVAL"},
                request_id=request_id,
                occurred_at=current_time,
            )
            self.db.add(audit)

        return len(stale_payments)

    async def create_payment(
        self,
        business_id: uuid.UUID,
        invoice_id: uuid.UUID,
        invoice_number: Optional[str],
        amount: Decimal,
        currency: str,
        idempotency_key: uuid.UUID,
        initiated_by_type: str,
        initiated_by_id: Optional[uuid.UUID],
        expires_at: datetime,
        request_id: Optional[str] = None,
    ) -> Payment:
        """Create payment row in CREATED status and record audit entry."""
        now = datetime.now(timezone.utc)
        payment = Payment(
            payment_id=uuid.uuid4(),
            business_id=business_id,
            invoice_id=invoice_id,
            invoice_number=invoice_number,
            amount=amount,
            currency=currency,
            provider="PAYPAL",
            status="CREATED",
            allocation_status="NOT_APPLICABLE",
            idempotency_key=idempotency_key,
            initiated_by_type=initiated_by_type,
            initiated_by_id=initiated_by_id,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        self.db.add(payment)

        audit = AuditLog(
            audit_id=uuid.uuid4(),
            business_id=business_id,
            actor_type=initiated_by_type,
            actor_id=str(initiated_by_id) if initiated_by_id else None,
            action="PAYMENT_CREATED",
            entity_type="payment",
            entity_id=payment.payment_id,
            before=None,
            after={
                "status": "CREATED",
                "amount": str(amount),
                "currency": currency,
                "invoice_id": str(invoice_id),
            },
            request_id=request_id,
            occurred_at=now,
        )
        self.db.add(audit)
        return payment

    async def save_provider_reference(
        self,
        payment_id: uuid.UUID,
        provider_order_id: str,
        paypal_request_id: uuid.UUID,
        capture_request_id: uuid.UUID,
        provider_status: Optional[str] = "CREATED",
        raw_last_response: Optional[dict] = None,
    ) -> PaymentProviderReference:
        ref = PaymentProviderReference(
            id=uuid.uuid4(),
            payment_id=payment_id,
            provider_order_id=provider_order_id,
            provider_capture_id=None,
            provider_status=provider_status,
            paypal_request_id=paypal_request_id,
            capture_request_id=capture_request_id,
            raw_last_response=raw_last_response,
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(ref)
        return ref

    async def list_payments(
        self,
        business_id: uuid.UUID,
        invoice_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        limit: int = 25,
        cursor: Optional[datetime] = None,
    ) -> Sequence[Payment]:
        """Cursor pagination for payments scoped to business_id, newest first."""
        stmt = (
            select(Payment)
            .where(Payment.business_id == business_id)
            .order_by(desc(Payment.created_at), desc(Payment.payment_id))
            .limit(limit + 1)
        )

        if invoice_id:
            stmt = stmt.where(Payment.invoice_id == invoice_id)
        if status:
            stmt = stmt.where(Payment.status == status)
        if from_date:
            stmt = stmt.where(Payment.created_at >= from_date)
        if to_date:
            stmt = stmt.where(Payment.created_at <= to_date)
        if cursor:
            stmt = stmt.where(Payment.created_at < cursor)

        result = await self.db.execute(stmt)
        return result.scalars().all()
