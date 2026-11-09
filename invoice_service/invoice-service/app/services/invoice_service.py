from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import secrets
from typing import List, Optional
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession
from app.clients.financial_data_client import financial_data_client
from app.clients.user_service_client import user_service_client
from app.core.auth import UserContext
from app.core.errors import (
    FieldNotEditableException,
    ForbiddenException,
    InvalidStateTransitionException,
    InvoiceHasPaymentsException,
    NotFoundException,
    ValidationException,
)
from app.core.money import format_money
from app.models.invoice import Invoice, InvoiceItem
from app.repositories.audit_repo import record_audit_log
from app.repositories.invoice_repo import (
    create_invoice,
    delete_draft_items,
    get_invoice_allocations,
    get_invoice_by_id,
    list_invoices as repo_list_invoices,
)
from app.repositories.settings_repo import get_or_create_settings
from app.repositories.tax_rate_repo import get_active_tax_rate_by_rate
from app.schemas.invoice import (
    InvoiceBalanceResponse,
    InvoiceCreate,
    InvoiceIssueResponse,
    InvoiceResponse,
    InvoiceUpdate,
    PaginatedInvoicesResponse,
    PaymentAllocationResponse,
    PayLinkResponse,
)
from app.services.calculation import (
    ItemCalculationInput,
    calculate_invoice_totals,
)
from app.services.numbering import allocate_invoice_number
from app.services.state_machine import (
    InvoiceStatus,
    TransitionTrigger,
    transition_invoice_status,
)


async def _validate_tax_rates(
    session: AsyncSession, business_id: UUID, items: List[ItemCalculationInput]
) -> None:
    """Ensures each item's tax_rate matches an active configured tax rate of the business, or 0."""
    for item in items:
        if item.tax_rate == Decimal("0.0000") or item.tax_rate == Decimal("0"):
            continue
        configured = await get_active_tax_rate_by_rate(session, business_id, item.tax_rate)
        if not configured:
            raise ValidationException(
                f"Line item {item.position}: tax rate {item.tax_rate:.4f} is not configured or not active for this business"
            )


def is_invoice_overdue(invoice: Invoice, now_utc: Optional[datetime] = None) -> bool:
    """Computes whether an invoice is overdue:
    due_date < business_local_date AND outstanding_balance > 0 AND status IN ('ISSUED', 'PARTIALLY_PAID').
    Uses the snapshotted business_timezone if available (or UTC).
    """
    if invoice.status not in (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID):
        return False
    if (invoice.total_amount - invoice.amount_paid) <= Decimal("0.00"):
        return False

    tz_str = invoice.business_timezone or "UTC"
    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = timezone.utc

    now = now_utc or datetime.now(timezone.utc)
    local_date = now.astimezone(tz).date()
    return invoice.due_date < local_date


class InvoiceService:
    @staticmethod
    async def create_draft(
        session: AsyncSession,
        user: UserContext,
        payload: InvoiceCreate,
        request_id: Optional[str] = None,
    ) -> InvoiceResponse:
        # 1. Validate business base currency (Contract C5)
        metadata = await user_service_client.get_business_metadata(user.business_id)
        base_currency = metadata.get("base_currency", "USD").upper()
        if payload.currency != base_currency:
            raise ValidationException(
                f"Invoice currency '{payload.currency}' must match business base currency '{base_currency}'"
            )

        # 2. Prepare items and validate tax rates
        item_inputs = [
            ItemCalculationInput(
                position=idx + 1,
                description=it.description,
                quantity=it.quantity,
                unit_price=it.unit_price,
                tax_rate=it.tax_rate,
            )
            for idx, it in enumerate(payload.items)
        ]
        await _validate_tax_rates(session, user.business_id, item_inputs)

        # 3. Calculate line items and totals
        calculated = calculate_invoice_totals(item_inputs)

        # 4. Validate min_installment_amount if provided
        if payload.min_installment_amount is not None:
            if payload.min_installment_amount > calculated.total_amount:
                raise ValidationException("min_installment_amount cannot exceed total_amount")

        # 5. Build Invoice model
        new_invoice_id = uuid4()
        invoice = Invoice(
            invoice_id=new_invoice_id,
            business_id=user.business_id,
            customer_id=payload.customer_id,
            status=InvoiceStatus.DRAFT,
            due_date=payload.due_date,
            currency=payload.currency,
            subtotal=calculated.subtotal,
            tax_amount=calculated.tax_amount,
            total_amount=calculated.total_amount,
            amount_paid=Decimal("0.0000"),
            notes=payload.notes,
            min_installment_amount=payload.min_installment_amount,
            created_by=user.user_id,
        )

        invoice_items = [
            InvoiceItem(
                item_id=uuid4(),
                invoice_id=new_invoice_id,
                position=it.position,
                description=it.description,
                quantity=it.quantity,
                unit_price=it.unit_price,
                tax_rate=it.tax_rate,
                line_subtotal=it.line_subtotal,
                line_tax=it.line_tax,
                line_total=it.line_total,
            )
            for it in calculated.items
        ]
        invoice.items = invoice_items

        saved = await create_invoice(session, user.business_id, invoice)

        # 6. Audit log
        await record_audit_log(
            session=session,
            business_id=user.business_id,
            actor_type="USER",
            actor_id=str(user.user_id),
            action="INVOICE_CREATED",
            entity_type="INVOICE",
            entity_id=saved.invoice_id,
            before=None,
            after={
                "customer_id": str(saved.customer_id),
                "total_amount": str(saved.total_amount),
                "currency": saved.currency,
                "items_count": len(saved.items),
            },
            request_id=request_id,
        )

        return InvoiceResponse.from_orm(saved, is_overdue=False)

    @staticmethod
    async def get_invoice(
        session: AsyncSession,
        business_id: UUID,
        invoice_id: UUID,
    ) -> InvoiceResponse:
        inv = await get_invoice_by_id(session, business_id, invoice_id)
        if not inv:
            raise NotFoundException("Invoice not found")
        overdue = is_invoice_overdue(inv)
        return InvoiceResponse.from_orm(inv, is_overdue=overdue)

    @staticmethod
    async def update_invoice(
        session: AsyncSession,
        user: UserContext,
        invoice_id: UUID,
        payload: InvoiceUpdate,
        request_id: Optional[str] = None,
    ) -> InvoiceResponse:
        inv = await get_invoice_by_id(session, user.business_id, invoice_id, for_update=True)
        if not inv:
            raise NotFoundException("Invoice not found")

        if user.role == "EMPLOYEE":
            if inv.created_by != user.user_id:
                raise ForbiddenException("Employees can only edit invoices created by themselves")

        before_audit = {
            "status": inv.status,
            "total_amount": str(inv.total_amount),
            "notes": inv.notes,
            "due_date": str(inv.due_date),
        }

        if inv.status in (InvoiceStatus.PARTIALLY_PAID, InvoiceStatus.PAID, InvoiceStatus.VOID):
            raise FieldNotEditableException(f"Cannot edit invoice in terminal or payment state '{inv.status}'")

        if inv.status == InvoiceStatus.ISSUED:
            if any([
                payload.customer_id is not None,
                payload.due_date is not None,
                payload.currency is not None,
                payload.items is not None,
                payload.min_installment_amount is not None,
            ]):
                raise FieldNotEditableException("Only 'notes' can be edited on an issued invoice")

            if payload.notes is not None:
                inv.notes = payload.notes
        elif inv.status == InvoiceStatus.DRAFT:
            if payload.customer_id is not None:
                inv.customer_id = payload.customer_id
            if payload.due_date is not None:
                inv.due_date = payload.due_date
            if payload.notes is not None:
                inv.notes = payload.notes

            if payload.currency is not None:
                metadata = await user_service_client.get_business_metadata(user.business_id)
                base_currency = metadata.get("base_currency", "USD").upper()
                if payload.currency != base_currency:
                    raise ValidationException(
                        f"Invoice currency '{payload.currency}' must match business base currency '{base_currency}'"
                    )
                inv.currency = payload.currency

            if payload.items is not None:
                item_inputs = [
                    ItemCalculationInput(
                        position=idx + 1,
                        description=it.description,
                        quantity=it.quantity,
                        unit_price=it.unit_price,
                        tax_rate=it.tax_rate,
                    )
                    for idx, it in enumerate(payload.items)
                ]
                await _validate_tax_rates(session, user.business_id, item_inputs)
                calculated = calculate_invoice_totals(item_inputs)

                await delete_draft_items(session, user.business_id, invoice_id)
                new_items = [
                    InvoiceItem(
                        item_id=uuid4(),
                        invoice_id=invoice_id,
                        position=it.position,
                        description=it.description,
                        quantity=it.quantity,
                        unit_price=it.unit_price,
                        tax_rate=it.tax_rate,
                        line_subtotal=it.line_subtotal,
                        line_tax=it.line_tax,
                        line_total=it.line_total,
                    )
                    for it in calculated.items
                ]
                inv.items = new_items
                inv.subtotal = calculated.subtotal
                inv.tax_amount = calculated.tax_amount
                inv.total_amount = calculated.total_amount

            if payload.min_installment_amount is not None:
                if payload.min_installment_amount > inv.total_amount:
                    raise ValidationException("min_installment_amount cannot exceed total_amount")
                inv.min_installment_amount = payload.min_installment_amount

        await session.flush()

        after_audit = {
            "status": inv.status,
            "total_amount": str(inv.total_amount),
            "notes": inv.notes,
            "due_date": str(inv.due_date),
        }
        await record_audit_log(
            session=session,
            business_id=user.business_id,
            actor_type="USER",
            actor_id=str(user.user_id),
            action="INVOICE_UPDATED",
            entity_type="INVOICE",
            entity_id=inv.invoice_id,
            before=before_audit,
            after=after_audit,
            request_id=request_id,
        )

        overdue = is_invoice_overdue(inv)
        return InvoiceResponse.from_orm(inv, is_overdue=overdue)

    @staticmethod
    async def issue_invoice(
        session: AsyncSession,
        user: UserContext,
        invoice_id: UUID,
        request_id: Optional[str] = None,
    ) -> InvoiceIssueResponse:
        """Issues a draft invoice atomically:
        - Locks invoice row FOR UPDATE
        - Enforces DRAFT status
        - Enforces >= 1 line items
        - Performs live authz check with User Service (Contract C5)
        - Validates customer with Financial Data Service (Contract C1)
        - Captures customer and seller snapshots
        - Validates due_date >= local issue_date
        - Assigns gap-free invoice number
        - Generates pay token (hashed with SHA-256 in DB, raw returned once)
        - Transitions state machine to ISSUED
        - Writes audit log
        """
        # 1. Acquire row lock
        inv = await get_invoice_by_id(session, user.business_id, invoice_id, for_update=True)
        if not inv:
            raise NotFoundException("Invoice not found")

        # 2. State transition pre-check (must be DRAFT; do not mutate status until all fields ready)
        if inv.status != InvoiceStatus.DRAFT:
            raise InvalidStateTransitionException(
                f"Cannot issue invoice in status '{inv.status}'. Only DRAFT invoices can be issued."
            )

        # 3. Must have at least 1 item
        if not inv.items:
            raise ValidationException("Cannot issue an invoice with no line items")

        # 4. Live authz check with User Service
        authorized = await user_service_client.check_live_authz(
            user.user_id, user.business_id, "invoices.issue"
        )
        if not authorized:
            raise ForbiddenException("User is not authorized to issue invoices")

        # 5. Financial Data Service customer validation (Contract C1)
        customer = await financial_data_client.get_customer(
            business_id=user.business_id, customer_id=inv.customer_id
        )
        if not customer:
            raise ValidationException("Customer not found in Financial Data Service")
        if str(customer.get("business_id")) != str(user.business_id):
            raise ValidationException("Customer belongs to another business")
        if not customer.get("is_active", False):
            raise ValidationException("Cannot issue invoice for inactive customer")

        # 6. Fetch business timezone and calculate local issue date
        metadata = await user_service_client.get_business_metadata(user.business_id)
        tz_str = metadata.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = timezone.utc

        now_utc = datetime.now(timezone.utc)
        local_issue_date = now_utc.astimezone(tz).date()

        if inv.due_date < local_issue_date:
            raise ValidationException(
                f"Due date ({inv.due_date}) cannot be before issue date ({local_issue_date})"
            )

        # 7. Recalculate totals in transaction to ensure absolute integrity
        item_inputs = [
            ItemCalculationInput(
                position=it.position,
                description=it.description,
                quantity=it.quantity,
                unit_price=it.unit_price,
                tax_rate=it.tax_rate,
            )
            for it in sorted(inv.items, key=lambda x: x.position)
        ]
        calculated = calculate_invoice_totals(item_inputs)
        inv.subtotal = calculated.subtotal
        inv.tax_amount = calculated.tax_amount
        inv.total_amount = calculated.total_amount

        # 8. Build customer and seller snapshots
        customer_snapshot = {
            "customer_id": str(customer.get("customer_id", inv.customer_id)),
            "name": customer.get("name"),
            "email": customer.get("email"),
            "billing_address": customer.get("billing_address"),
            "tax_id": customer.get("tax_id"),
        }
        settings = await get_or_create_settings(session, user.business_id)
        seller_snapshot = {
            "seller_name": settings.seller_name,
            "seller_address": settings.seller_address,
            "seller_tax_id": settings.seller_tax_id,
        }

        # 9. Assign invoice number
        prefix = settings.invoice_prefix or "INV-"
        invoice_number = await allocate_invoice_number(session, user.business_id, prefix)

        # 10. Generate pay token
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        # 11. Update invoice fields
        inv.status = InvoiceStatus.ISSUED
        inv.invoice_number = invoice_number
        inv.issue_date = local_issue_date
        inv.business_timezone = tz_str
        inv.customer_snapshot = customer_snapshot
        inv.seller_snapshot = seller_snapshot
        inv.pay_token_hash = token_hash
        inv.issued_at = now_utc

        await session.flush()

        # 12. Audit log (Never log the raw pay token or hash)
        await record_audit_log(
            session=session,
            business_id=user.business_id,
            actor_type="USER",
            actor_id=str(user.user_id),
            action="INVOICE_ISSUED",
            entity_type="INVOICE",
            entity_id=inv.invoice_id,
            before={"status": InvoiceStatus.DRAFT},
            after={
                "status": InvoiceStatus.ISSUED,
                "invoice_number": invoice_number,
                "total_amount": str(inv.total_amount),
                "issue_date": str(local_issue_date),
            },
            request_id=request_id,
        )

        return InvoiceIssueResponse.from_orm_with_token(inv, pay_token=raw_token, is_overdue=False)

    @staticmethod
    async def void_invoice(
        session: AsyncSession,
        user: UserContext,
        invoice_id: UUID,
        void_reason: str,
        request_id: Optional[str] = None,
    ) -> InvoiceResponse:
        """Voids an invoice atomically:
        - Locks invoice row FOR UPDATE
        - Performs live authz check for invoices.void with User Service
        - Validates that invoice has no existing payments/allocations
        - Transitions status to VOID
        - Sets voided_at, voided_by, void_reason
        - Records audit log
        """
        # 1. Acquire row lock
        inv = await get_invoice_by_id(session, user.business_id, invoice_id, for_update=True)
        if not inv:
            raise NotFoundException("Invoice not found")

        # 2. Live authz check with User Service
        authorized = await user_service_client.check_live_authz(
            user.user_id, user.business_id, "invoices.void"
        )
        if not authorized:
            raise ForbiddenException("User is not authorized to void invoices")

        # 3. Check for existing payments/allocations
        has_payments = (inv.amount_paid > Decimal("0.00")) or (bool(inv.allocations))
        old_status = inv.status

        # 4. Set void fields before status mutation
        now_utc = datetime.now(timezone.utc)
        inv.voided_at = now_utc
        inv.voided_by = user.user_id
        inv.void_reason = void_reason

        # 5. State transition via state machine
        transition_invoice_status(
            inv, InvoiceStatus.VOID, TransitionTrigger.VOID, has_allocations=has_payments
        )

        await session.flush()

        # 6. Audit log
        await record_audit_log(
            session=session,
            business_id=user.business_id,
            actor_type="USER",
            actor_id=str(user.user_id),
            action="INVOICE_VOIDED",
            entity_type="INVOICE",
            entity_id=inv.invoice_id,
            before={"status": old_status},
            after={
                "status": InvoiceStatus.VOID,
                "void_reason": void_reason,
                "voided_by": str(user.user_id),
            },
            request_id=request_id,
        )

        return InvoiceResponse.from_orm(inv, is_overdue=False)

    @staticmethod
    async def regenerate_pay_link(
        session: AsyncSession,
        user: UserContext,
        invoice_id: UUID,
        request_id: Optional[str] = None,
    ) -> PayLinkResponse:
        """Regenerates pay link for ISSUED or PARTIALLY_PAID invoices:
        - Overwrites pay_token_hash with new token hash (old token immediately invalid)
        - Writes audit log
        - Returns raw pay token ONCE
        """
        inv = await get_invoice_by_id(session, user.business_id, invoice_id, for_update=True)
        if not inv:
            raise NotFoundException("Invoice not found")

        if inv.status not in (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID):
            raise ValidationException(
                f"Cannot generate pay link for invoice in status '{inv.status}'. Only ISSUED or PARTIALLY_PAID invoices are payable."
            )

        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        inv.pay_token_hash = token_hash

        await session.flush()

        await record_audit_log(
            session=session,
            business_id=user.business_id,
            actor_type="USER",
            actor_id=str(user.user_id),
            action="PAY_LINK_REGENERATED",
            entity_type="INVOICE",
            entity_id=inv.invoice_id,
            before={"status": inv.status},
            after={"status": inv.status, "pay_link_rotated": True},
            request_id=request_id,
        )

        return PayLinkResponse(invoice_id=inv.invoice_id, pay_token=raw_token)

    @staticmethod
    async def list_invoices(
        session: AsyncSession,
        business_id: UUID,
        limit: int = 25,
        cursor: Optional[str] = None,
        status: Optional[str] = None,
        customer_id: Optional[UUID] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        overdue_only: bool = False,
        search: Optional[str] = None,
    ) -> PaginatedInvoicesResponse:
        """Cursor-paginated list of invoices for the business."""
        metadata = await user_service_client.get_business_metadata(business_id)
        tz_str = metadata.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = timezone.utc
        local_date = datetime.now(tz).date()

        invoices, next_cursor = await repo_list_invoices(
            session=session,
            business_id=business_id,
            limit=limit,
            cursor=cursor,
            status=status,
            customer_id=customer_id,
            from_date=from_date,
            to_date=to_date,
            overdue_only=overdue_only,
            business_local_date=local_date,
            search=search,
        )

        items = [
            InvoiceResponse.from_orm(inv, is_overdue=is_invoice_overdue(inv))
            for inv in invoices
        ]
        return PaginatedInvoicesResponse(items=items, next_cursor=next_cursor)

    @staticmethod
    async def get_invoice_balance(
        session: AsyncSession,
        business_id: UUID,
        invoice_id: UUID,
    ) -> InvoiceBalanceResponse:
        inv = await get_invoice_by_id(session, business_id, invoice_id)
        if not inv:
            raise NotFoundException("Invoice not found")

        outstanding = max(Decimal("0.00"), inv.total_amount - inv.amount_paid)
        overdue = is_invoice_overdue(inv)

        return InvoiceBalanceResponse(
            invoice_id=inv.invoice_id,
            business_id=inv.business_id,
            currency=inv.currency,
            total_amount=format_money(inv.total_amount),
            amount_paid=format_money(inv.amount_paid),
            outstanding_balance=format_money(outstanding),
            status=inv.status,
            is_overdue=overdue,
        )

    @staticmethod
    async def get_invoice_allocations(
        session: AsyncSession,
        business_id: UUID,
        invoice_id: UUID,
    ) -> List[PaymentAllocationResponse]:
        inv = await get_invoice_by_id(session, business_id, invoice_id)
        if not inv:
            raise NotFoundException("Invoice not found")

        allocs = await get_invoice_allocations(session, business_id, invoice_id)
        return [PaymentAllocationResponse.from_orm(a) for a in allocs]
