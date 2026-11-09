import uuid
from typing import Callable, Optional, Set
from fastapi import Depends, Header, Request
from pydantic import BaseModel
import structlog
from app.core.auth import UserTokenPayload, verify_user_token
from app.core.errors import (
    BadRequestException,
    ForbiddenException,
    UnauthorizedException,
)

logger = structlog.get_logger()

# Role constants
ROLE_OWNER = "OWNER"
ROLE_ADMIN = "ADMIN"
ROLE_EMPLOYEE = "EMPLOYEE"

VALID_ROLES = {ROLE_OWNER, ROLE_ADMIN, ROLE_EMPLOYEE}

# Permission matrix: Role -> Set of permission codes
ROLE_PERMISSIONS: dict[str, Set[str]] = {
    ROLE_OWNER: {
        "dashboard.view",
        "members.manage",
        "customers.read",
        "customers.write",
        "invoices.create",
        "invoices.edit_draft",
        "invoices.issue",
        "invoices.void",
        "expenses.record",
        "expenses.view",
        "expenses.edit",
        "expenses.void",
        "payments.view",
        "payments.configure",
        "business.delete",
        "suppliers.read",
        "suppliers.write",
        "categories.read",
        "categories.write",
    },
    ROLE_ADMIN: {
        "dashboard.view",
        "members.manage",
        "customers.read",
        "customers.write",
        "invoices.create",
        "invoices.edit_draft",
        "invoices.issue",
        "invoices.void",
        "expenses.record",
        "expenses.view",
        "expenses.edit",
        "expenses.void",
        "payments.view",
        "suppliers.read",
        "suppliers.write",
        "categories.read",
        "categories.write",
    },
    ROLE_EMPLOYEE: {
        "dashboard.view",
        "customers.read",
        "customers.write",  # Note: Create only, checked at service layer for edit/delete
        "invoices.create",
        "invoices.edit_draft",
        "expenses.record",
        "expenses.view",    # Note: Own only, checked in repo/service layer
        "expenses.edit",    # Note: Own only
        "payments.view",
        "suppliers.read",
        "categories.read",
    },
}


class AuthContext(BaseModel):
    user_id: uuid.UUID
    business_id: uuid.UUID
    role: str
    permissions: Set[str]

    @property
    def is_owner(self) -> bool:
        return self.role == ROLE_OWNER

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def is_employee(self) -> bool:
        return self.role == ROLE_EMPLOYEE

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions


async def get_current_user_token(request: Request) -> UserTokenPayload:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise UnauthorizedException("Missing Authorization header")

    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise UnauthorizedException("Invalid Authorization header format, expected 'Bearer <token>'")

    token = parts[1]
    return await verify_user_token(token)


async def get_auth_context(
    request: Request,
    x_business_id: Optional[str] = Header(None, alias="X-Business-ID"),
) -> AuthContext:
    token_payload = await get_current_user_token(request)

    try:
        user_id = uuid.UUID(token_payload.sub)
    except ValueError as e:
        raise UnauthorizedException("Invalid user ID in token 'sub' claim") from e

    memberships = token_payload.memberships or {}

    selected_business_id_str: str
    if x_business_id:
        selected_business_id_str = x_business_id.strip()
    else:
        # If user belongs to exactly one business, default to it
        if len(memberships) == 1:
            selected_business_id_str = next(iter(memberships.keys()))
        elif len(memberships) == 0:
            raise ForbiddenException("User has no active business memberships")
        else:
            raise BadRequestException(
                code="BUSINESS_ID_REQUIRED",
                message="X-Business-ID header is required when user belongs to multiple businesses",
            )

    try:
        selected_business_id = uuid.UUID(selected_business_id_str)
    except ValueError as e:
        raise BadRequestException(code="INVALID_BUSINESS_ID", message="Malformed X-Business-ID header") from e

    if selected_business_id_str not in memberships:
        raise ForbiddenException("User is not an active member of this business")

    role = memberships[selected_business_id_str].upper()
    if role not in VALID_ROLES:
        raise ForbiddenException(f"Invalid membership role '{role}'")

    perms = ROLE_PERMISSIONS.get(role, set())
    return AuthContext(
        user_id=user_id,
        business_id=selected_business_id,
        role=role,
        permissions=perms,
    )


def require_permission(permission_code: str) -> Callable:
    async def permission_dependency(
        auth_context: AuthContext = Depends(get_auth_context),
    ) -> AuthContext:
        if not auth_context.has_permission(permission_code):
            raise ForbiddenException(f"Missing required permission: '{permission_code}'")
        return auth_context

    return permission_dependency
