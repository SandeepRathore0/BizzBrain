from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional, Tuple
import uuid
from zoneinfo import ZoneInfo
from sqlalchemy.ext.asyncio import AsyncSession
from app.clients.user_service_client import user_service_client
from app.core.errors import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.core.idempotency import calculate_request_hash
from app.core.money import validate_expense_amount
from app.core.pagination import PaginatedResponse, decode_cursor, encode_cursor
from app.core.permissions import AuthContext
from app.models.models import Expense
from app.repositories.audit_repository import AuditRepository
from app.repositories.category_repository import CategoryRepository
from app.repositories.expense_repository import ExpenseRepository
from app.repositories.idempotency_repository import IdempotencyRepository
from app.repositories.supplier_repository import SupplierRepository
from app.schemas.expense import (
    ExpenseCreate,
    ExpensePatch,
    ExpenseResponse,
    ExpenseVoidRequest,
)


class ExpenseService:
    @staticmethod
    async def record_expense(
        session: AsyncSession,
        auth_context: AuthContext,
        idempotency_key: uuid.UUID,
        data: ExpenseCreate,
        request_id: str,
    ) -> Tuple[dict[str, Any], bool]:
        """
        Record a new expense with idempotency protection.
        Returns: (response_data_dict, is_replayed)
        """
        endpoint = "/api/v1/expenses"
        request_hash = calculate_request_hash(data.model_dump(mode="json"))

        # 1. Check idempotency key
        stored_key = await IdempotencyRepository.get_key(
            session=session,
            business_id=auth_context.business_id,
            key=idempotency_key,
            endpoint=endpoint,
        )
        if stored_key:
            if stored_key.request_hash == request_hash:
                return stored_key.response_body, True
            else:
                raise ValidationException(
                    code="IDEMPOTENCY_KEY_REUSED",
                    message="Idempotency key was previously used with a different request payload",
                )

        # 2. Fetch business details for base_currency and timezone
        business = await user_service_client.get_business_details(
            business_id=auth_context.business_id,
            request_id=request_id,
        )

        # 3. Currency validation
        if data.currency.upper() != business.base_currency.upper():
            raise ValidationException(
                code="CURRENCY_MISMATCH",
                message=f"Expense currency '{data.currency}' does not match business base currency '{business.base_currency}'",
            )

        # 4. Amount precision validation
        validate_expense_amount(data.amount, data.currency)

        # 5. Date validation
        try:
            tz = ZoneInfo(business.timezone)
        except Exception:
            tz = timezone.utc
        today_in_tz = datetime.now(tz).date()

        if data.expense_date > today_in_tz + timedelta(days=1):
            raise ValidationException(
                code="INVALID_DATE",
                message="Expense date cannot be more than 1 day in the future",
            )
        if data.expense_date < date(2000, 1, 1):
            raise ValidationException(
                code="INVALID_DATE",
                message="Expense date cannot be before year 2000",
            )

        # 6. Validate supplier reference if provided
        if data.supplier_id:
            supplier = await SupplierRepository.get_by_id(
                session=session,
                business_id=auth_context.business_id,
                supplier_id=data.supplier_id,
            )
            if not supplier or not supplier.is_active:
                raise ValidationException(
                    code="INVALID_SUPPLIER",
                    message="Referenced supplier does not exist or is inactive",
                )

        # 7. Validate category reference if provided
        if data.category_id:
            category = await CategoryRepository.get_by_id(
                session=session,
                business_id=auth_context.business_id,
                category_id=data.category_id,
            )
            if not category or not category.is_active:
                raise ValidationException(
                    code="INVALID_CATEGORY",
                    message="Referenced category does not exist or is inactive",
                )

        # 8. Create Expense entity
        expense = Expense(
            expense_id=uuid.uuid4(),
            business_id=auth_context.business_id,
            supplier_id=data.supplier_id,
            category_id=data.category_id,
            description=data.description.strip(),
            amount=data.amount,
            currency=data.currency.upper(),
            expense_date=data.expense_date,
            status="POSTED",
            created_by=auth_context.user_id,
            created_at=datetime.now(timezone.utc),
            idempotency_key=idempotency_key,
        )

        await ExpenseRepository.create(session, expense)

        # 9. Audit entry
        after_data = {
            "expense_id": str(expense.expense_id),
            "amount": str(expense.amount),
            "currency": expense.currency,
            "description": expense.description,
            "status": expense.status,
            "expense_date": expense.expense_date.isoformat(),
        }

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="EXPENSE_RECORDED",
            entity_type="expense",
            entity_id=expense.expense_id,
            request_id=request_id,
            before=None,
            after=after_data,
        )

        resp_obj = ExpenseResponse.model_validate(expense)
        response_dict = resp_obj.model_dump(mode="json")

        # 10. Save idempotency record
        await IdempotencyRepository.save_key(
            session=session,
            business_id=auth_context.business_id,
            key=idempotency_key,
            endpoint=endpoint,
            request_hash=request_hash,
            response_status=201,
            response_body=response_dict,
        )

        await session.commit()
        return response_dict, False

    @staticmethod
    async def get_expense(
        session: AsyncSession,
        auth_context: AuthContext,
        expense_id: uuid.UUID,
    ) -> ExpenseResponse:
        expense = await ExpenseRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            expense_id=expense_id,
        )
        if not expense:
            raise NotFoundException("Expense not found")

        # Employee visibility: only own expenses
        if auth_context.is_employee and expense.created_by != auth_context.user_id:
            raise NotFoundException("Expense not found")

        return ExpenseResponse.model_validate(expense)

    @staticmethod
    async def list_expenses(
        session: AsyncSession,
        auth_context: AuthContext,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        category_id: Optional[uuid.UUID] = None,
        supplier_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        min_amount: Optional[Decimal] = None,
        max_amount: Optional[Decimal] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> PaginatedResponse[ExpenseResponse]:
        limit = min(max(1, limit), 100)

        cursor_date: Optional[date] = None
        cursor_id: Optional[uuid.UUID] = None
        if cursor:
            try:
                cursor_dt, cursor_id = decode_cursor(cursor)
                cursor_date = cursor_dt.date()
            except ValueError as e:
                raise ValidationException(
                    code="INVALID_CURSOR",
                    message="Invalid pagination cursor format",
                ) from e

        # Employee restriction at repository query level
        created_by_filter = auth_context.user_id if auth_context.is_employee else None

        items, has_next = await ExpenseRepository.list_expenses(
            session=session,
            business_id=auth_context.business_id,
            created_by=created_by_filter,
            from_date=from_date,
            to_date=to_date,
            category_id=category_id,
            supplier_id=supplier_id,
            status=status,
            min_amount=min_amount,
            max_amount=max_amount,
            limit=limit,
            cursor_date=cursor_date,
            cursor_id=cursor_id,
        )

        next_cursor = None
        if has_next and items:
            last_item = items[-1]
            # Convert date to datetime for cursor encoding
            dt = datetime(
                last_item.expense_date.year,
                last_item.expense_date.month,
                last_item.expense_date.day,
                tzinfo=timezone.utc,
            )
            next_cursor = encode_cursor(dt, last_item.expense_id)

        return PaginatedResponse(
            items=[ExpenseResponse.model_validate(item) for item in items],
            next_cursor=next_cursor,
            limit=limit,
        )

    @staticmethod
    async def patch_expense(
        session: AsyncSession,
        auth_context: AuthContext,
        expense_id: uuid.UUID,
        data: ExpensePatch,
        raw_body_keys: set[str],
        request_id: str,
    ) -> ExpenseResponse:
        # Check forbidden fields: only description and category_id are allowed
        forbidden_fields = raw_body_keys - {"description", "category_id"}
        if forbidden_fields:
            raise ValidationException(
                code="FIELD_NOT_EDITABLE",
                message=f"Only 'description' and 'category_id' are editable on a posted expense. Prohibited fields: {sorted(list(forbidden_fields))}",
            )

        expense = await ExpenseRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            expense_id=expense_id,
        )
        if not expense:
            raise NotFoundException("Expense not found")

        # Employee check: own expenses only
        if auth_context.is_employee and expense.created_by != auth_context.user_id:
            raise NotFoundException("Expense not found")

        # Cannot edit voided expense
        if expense.status == "VOID":
            raise ConflictException(
                code="CANNOT_EDIT_VOID",
                message="Cannot edit an expense that has been voided",
            )

        before_data = {
            "description": expense.description,
            "category_id": str(expense.category_id) if expense.category_id else None,
        }

        if data.description is not None:
            expense.description = data.description.strip()

        if data.category_id is not None:
            category = await CategoryRepository.get_by_id(
                session=session,
                business_id=auth_context.business_id,
                category_id=data.category_id,
            )
            if not category or not category.is_active:
                raise ValidationException(
                    code="INVALID_CATEGORY",
                    message="Referenced category does not exist or is inactive",
                )
            expense.category_id = data.category_id

        after_data = {
            "description": expense.description,
            "category_id": str(expense.category_id) if expense.category_id else None,
        }

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="EXPENSE_UPDATED",
            entity_type="expense",
            entity_id=expense.expense_id,
            request_id=request_id,
            before=before_data,
            after=after_data,
        )

        await session.commit()
        await session.refresh(expense)
        return ExpenseResponse.model_validate(expense)

    @staticmethod
    async def void_expense(
        session: AsyncSession,
        auth_context: AuthContext,
        expense_id: uuid.UUID,
        data: ExpenseVoidRequest,
        request_id: str,
    ) -> ExpenseResponse:
        # Permission check: Employee cannot void
        if auth_context.is_employee:
            raise ForbiddenException("Employees are not permitted to void expenses")

        # Atomic SELECT ... FOR UPDATE inside transaction
        expense = await ExpenseRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            expense_id=expense_id,
            for_update=True,
        )
        if not expense:
            raise NotFoundException("Expense not found")

        if expense.status == "VOID":
            raise ConflictException(
                code="ALREADY_VOID",
                message="Expense has already been voided",
            )

        # Sensitive action: Call User Service live authz check
        is_allowed = await user_service_client.check_live_authz(
            user_id=auth_context.user_id,
            business_id=auth_context.business_id,
            permission="expenses.void",
            request_id=request_id,
        )
        if not is_allowed:
            raise ForbiddenException("Live authorization check denied void permission")

        before_data = {
            "status": expense.status,
            "voided_by": str(expense.voided_by) if expense.voided_by else None,
            "voided_at": expense.voided_at.isoformat() if expense.voided_at else None,
            "void_reason": expense.void_reason,
        }

        now_utc = datetime.now(timezone.utc)
        expense.status = "VOID"
        expense.voided_by = auth_context.user_id
        expense.voided_at = now_utc
        expense.void_reason = data.void_reason.strip()

        after_data = {
            "status": expense.status,
            "voided_by": str(expense.voided_by),
            "voided_at": expense.voided_at.isoformat(),
            "void_reason": expense.void_reason,
        }

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="EXPENSE_VOIDED",
            entity_type="expense",
            entity_id=expense.expense_id,
            request_id=request_id,
            before=before_data,
            after=after_data,
        )

        await session.commit()
        await session.refresh(expense)
        return ExpenseResponse.model_validate(expense)
