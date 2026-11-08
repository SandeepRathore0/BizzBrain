from datetime import datetime, timezone
from typing import Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.errors import ConflictException, ForbiddenException, NotFoundException, ValidationException
from app.core.pagination import PaginatedResponse, decode_cursor, encode_cursor
from app.core.permissions import AuthContext
from app.models.models import Customer
from app.repositories.audit_repository import AuditRepository
from app.repositories.customer_repository import CustomerRepository
from app.schemas.customer import CustomerCreate, CustomerResponse, CustomerUpdate


class CustomerService:
    @staticmethod
    async def create_customer(
        session: AsyncSession,
        auth_context: AuthContext,
        data: CustomerCreate,
        request_id: str,
    ) -> CustomerResponse:
        # Check for duplicate active customer
        existing = await CustomerRepository.find_active_by_name_and_email(
            session=session,
            business_id=auth_context.business_id,
            name=data.name,
            email=data.email,
        )
        if existing:
            raise ConflictException(
                code="DUPLICATE_CUSTOMER",
                message="An active customer with the same name and email already exists",
            )

        customer = Customer(
            customer_id=uuid.uuid4(),
            business_id=auth_context.business_id,
            name=data.name.strip(),
            email=data.email.strip() if data.email else None,
            phone=data.phone.strip() if data.phone else None,
            billing_address=data.billing_address,
            tax_id=data.tax_id.strip() if data.tax_id else None,
            is_active=True,
            created_by=auth_context.user_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        await CustomerRepository.create(session, customer)

        after_data = {
            "customer_id": str(customer.customer_id),
            "name": customer.name,
            "email": customer.email,
            "is_active": customer.is_active,
        }

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="CUSTOMER_CREATED",
            entity_type="customer",
            entity_id=customer.customer_id,
            request_id=request_id,
            before=None,
            after=after_data,
        )

        await session.commit()
        await session.refresh(customer)
        return CustomerResponse.model_validate(customer)

    @staticmethod
    async def get_customer(
        session: AsyncSession,
        auth_context: AuthContext,
        customer_id: uuid.UUID,
    ) -> CustomerResponse:
        customer = await CustomerRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            customer_id=customer_id,
        )
        if not customer:
            raise NotFoundException("Customer not found")
        return CustomerResponse.model_validate(customer)

    @staticmethod
    async def list_customers(
        session: AsyncSession,
        auth_context: AuthContext,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> PaginatedResponse[CustomerResponse]:
        limit = min(max(1, limit), 100)

        cursor_created_at: Optional[datetime] = None
        cursor_id: Optional[uuid.UUID] = None
        if cursor:
            try:
                cursor_created_at, cursor_id = decode_cursor(cursor)
            except ValueError as e:
                raise ValidationException(
                    code="INVALID_CURSOR",
                    message="Invalid pagination cursor format",
                ) from e

        items, has_next = await CustomerRepository.list_customers(
            session=session,
            business_id=auth_context.business_id,
            search=search,
            is_active=is_active,
            limit=limit,
            cursor_created_at=cursor_created_at,
            cursor_id=cursor_id,
        )

        next_cursor = None
        if has_next and items:
            last_item = items[-1]
            next_cursor = encode_cursor(last_item.created_at, last_item.customer_id)

        return PaginatedResponse(
            items=[CustomerResponse.model_validate(item) for item in items],
            next_cursor=next_cursor,
            limit=limit,
        )

    @staticmethod
    async def update_customer(
        session: AsyncSession,
        auth_context: AuthContext,
        customer_id: uuid.UUID,
        data: CustomerUpdate,
        request_id: str,
    ) -> CustomerResponse:
        if auth_context.is_employee:
            raise ForbiddenException("Employees are not permitted to edit customers")

        customer = await CustomerRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            customer_id=customer_id,
        )
        if not customer:
            raise NotFoundException("Customer not found")

        before_data = {
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "billing_address": customer.billing_address,
            "tax_id": customer.tax_id,
        }

        customer.name = data.name.strip()
        customer.email = data.email.strip() if data.email else None
        customer.phone = data.phone.strip() if data.phone else None
        customer.billing_address = data.billing_address
        customer.tax_id = data.tax_id.strip() if data.tax_id else None
        customer.updated_at = datetime.now(timezone.utc)

        after_data = {
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "billing_address": customer.billing_address,
            "tax_id": customer.tax_id,
        }

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="CUSTOMER_UPDATED",
            entity_type="customer",
            entity_id=customer.customer_id,
            request_id=request_id,
            before=before_data,
            after=after_data,
        )

        await session.commit()
        await session.refresh(customer)
        return CustomerResponse.model_validate(customer)

    @staticmethod
    async def deactivate_customer(
        session: AsyncSession,
        auth_context: AuthContext,
        customer_id: uuid.UUID,
        request_id: str,
    ) -> CustomerResponse:
        if auth_context.is_employee:
            raise ForbiddenException("Employees are not permitted to deactivate customers")

        customer = await CustomerRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            customer_id=customer_id,
        )
        if not customer:
            raise NotFoundException("Customer not found")

        before_data = {"is_active": customer.is_active}
        customer.is_active = False
        customer.updated_at = datetime.now(timezone.utc)
        after_data = {"is_active": False}

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="CUSTOMER_DEACTIVATED",
            entity_type="customer",
            entity_id=customer.customer_id,
            request_id=request_id,
            before=before_data,
            after=after_data,
        )

        await session.commit()
        await session.refresh(customer)
        return CustomerResponse.model_validate(customer)
