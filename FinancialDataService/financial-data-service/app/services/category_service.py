from typing import List
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.errors import ConflictException, NotFoundException
from app.core.permissions import AuthContext
from app.models.models import ExpenseCategory
from app.repositories.audit_repository import AuditRepository
from app.repositories.category_repository import CategoryRepository
from app.schemas.category import CategoryCreate, CategoryResponse, CategoryUpdate


class CategoryService:
    @staticmethod
    async def create_category(
        session: AsyncSession,
        auth_context: AuthContext,
        data: CategoryCreate,
        request_id: str,
    ) -> CategoryResponse:
        existing = await CategoryRepository.get_by_name(
            session=session,
            business_id=auth_context.business_id,
            name=data.category_name,
        )
        if existing:
            raise ConflictException(
                code="DUPLICATE_CATEGORY",
                message=f"Category '{data.category_name}' already exists in this business",
            )

        category = ExpenseCategory(
            category_id=uuid.uuid4(),
            business_id=auth_context.business_id,
            category_name=data.category_name.strip(),
            is_active=True,
        )
        await CategoryRepository.create(session, category)

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="CATEGORY_CREATED",
            entity_type="category",
            entity_id=category.category_id,
            request_id=request_id,
            before=None,
            after={"category_name": category.category_name, "is_active": True},
        )

        await session.commit()
        await session.refresh(category)
        return CategoryResponse.model_validate(category)

    @staticmethod
    async def list_categories(
        session: AsyncSession,
        auth_context: AuthContext,
    ) -> List[CategoryResponse]:
        # Lazy idempotent seeding on first access
        await CategoryRepository.seed_default_categories_if_empty(
            session=session,
            business_id=auth_context.business_id,
        )
        await session.commit()

        items = await CategoryRepository.list_categories(
            session=session,
            business_id=auth_context.business_id,
        )
        return [CategoryResponse.model_validate(item) for item in items]

    @staticmethod
    async def update_category(
        session: AsyncSession,
        auth_context: AuthContext,
        category_id: uuid.UUID,
        data: CategoryUpdate,
        request_id: str,
    ) -> CategoryResponse:
        category = await CategoryRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            category_id=category_id,
        )
        if not category:
            raise NotFoundException("Category not found")

        before_data = {
            "category_name": category.category_name,
            "is_active": category.is_active,
        }

        if data.category_name and data.category_name.strip().lower() != category.category_name.lower():
            existing = await CategoryRepository.get_by_name(
                session=session,
                business_id=auth_context.business_id,
                name=data.category_name,
            )
            if existing:
                raise ConflictException(
                    code="DUPLICATE_CATEGORY",
                    message=f"Category '{data.category_name}' already exists in this business",
                )
            category.category_name = data.category_name.strip()

        if data.is_active is not None:
            category.is_active = data.is_active

        after_data = {
            "category_name": category.category_name,
            "is_active": category.is_active,
        }

        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="CATEGORY_UPDATED",
            entity_type="category",
            entity_id=category.category_id,
            request_id=request_id,
            before=before_data,
            after=after_data,
        )

        await session.commit()
        await session.refresh(category)
        return CategoryResponse.model_validate(category)
