from datetime import datetime, timezone
from typing import Optional
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.errors import ForbiddenException, NotFoundException, ValidationException
from app.core.pagination import PaginatedResponse, decode_cursor, encode_cursor
from app.core.permissions import AuthContext
from app.models.models import Supplier
from app.repositories.audit_repository import AuditRepository
from app.repositories.supplier_repository import SupplierRepository
from app.schemas.supplier import SupplierCreate, SupplierResponse, SupplierUpdate


class SupplierService:
    @staticmethod
    async def create_supplier(
        session: AsyncSession,
        auth_context: AuthContext,
        data: SupplierCreate,
        request_id: str,
    ) -> SupplierResponse:
        if auth_context.is_employee:
            raise ForbiddenException("Employees are not permitted to create suppliers")

        supplier = Supplier(
            supplier_id=uuid.uuid4(),
            business_id=auth_context.business_id,
            name=data.name.strip(),
            email=data.email.strip() if data.email else None,
            phone=data.phone.strip() if data.phone else None,
            address=data.address,
            is_active=True,
            created_by=auth_context.user_id,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        await SupplierRepository.create(session, supplier)

        after_data = {
            "supplier_id": str(supplier.supplier_id),
            "name": supplier.name,
            "email": supplier.email,
            "is_active": supplier.is_active,
        }

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="SUPPLIER_CREATED",
            entity_type="supplier",
            entity_id=supplier.supplier_id,
            request_id=request_id,
            before=None,
            after=after_data,
        )

        await session.commit()
        await session.refresh(supplier)
        return SupplierResponse.model_validate(supplier)

    @staticmethod
    async def get_supplier(
        session: AsyncSession,
        auth_context: AuthContext,
        supplier_id: uuid.UUID,
    ) -> SupplierResponse:
        supplier = await SupplierRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            supplier_id=supplier_id,
        )
        if not supplier:
            raise NotFoundException("Supplier not found")
        return SupplierResponse.model_validate(supplier)

    @staticmethod
    async def list_suppliers(
        session: AsyncSession,
        auth_context: AuthContext,
        search: Optional[str] = None,
        is_active: Optional[bool] = None,
        limit: int = 25,
        cursor: Optional[str] = None,
    ) -> PaginatedResponse[SupplierResponse]:
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

        items, has_next = await SupplierRepository.list_suppliers(
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
            next_cursor = encode_cursor(last_item.created_at, last_item.supplier_id)

        return PaginatedResponse(
            items=[SupplierResponse.model_validate(item) for item in items],
            next_cursor=next_cursor,
            limit=limit,
        )

    @staticmethod
    async def update_supplier(
        session: AsyncSession,
        auth_context: AuthContext,
        supplier_id: uuid.UUID,
        data: SupplierUpdate,
        request_id: str,
    ) -> SupplierResponse:
        if auth_context.is_employee:
            raise ForbiddenException("Employees are not permitted to edit suppliers")

        supplier = await SupplierRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            supplier_id=supplier_id,
        )
        if not supplier:
            raise NotFoundException("Supplier not found")

        before_data = {
            "name": supplier.name,
            "email": supplier.email,
            "phone": supplier.phone,
            "address": supplier.address,
        }

        supplier.name = data.name.strip()
        supplier.email = data.email.strip() if data.email else None
        supplier.phone = data.phone.strip() if data.phone else None
        supplier.address = data.address
        supplier.updated_at = datetime.now(timezone.utc)

        after_data = {
            "name": supplier.name,
            "email": supplier.email,
            "phone": supplier.phone,
            "address": supplier.address,
        }

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="SUPPLIER_UPDATED",
            entity_type="supplier",
            entity_id=supplier.supplier_id,
            request_id=request_id,
            before=before_data,
            after=after_data,
        )

        await session.commit()
        await session.refresh(supplier)
        return SupplierResponse.model_validate(supplier)

    @staticmethod
    async def deactivate_supplier(
        session: AsyncSession,
        auth_context: AuthContext,
        supplier_id: uuid.UUID,
        request_id: str,
    ) -> SupplierResponse:
        if auth_context.is_employee:
            raise ForbiddenException("Employees are not permitted to deactivate suppliers")

        supplier = await SupplierRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            supplier_id=supplier_id,
        )
        if not supplier:
            raise NotFoundException("Supplier not found")

        before_data = {"is_active": supplier.is_active}
        supplier.is_active = False
        supplier.updated_at = datetime.now(timezone.utc)
        after_data = {"is_active": False}

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="SUPPLIER_DEACTIVATED",
            entity_type="supplier",
            entity_id=supplier.supplier_id,
            request_id=request_id,
            before=before_data,
            after=after_data,
        )

        await session.commit()
        await session.refresh(supplier)
        return SupplierResponse.model_validate(supplier)
