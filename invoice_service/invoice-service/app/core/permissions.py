from typing import Callable, Dict, Set
from fastapi import Depends
from app.core.auth import UserContext, get_current_user
from app.core.errors import ForbiddenException
from app.core.logging import logger

# Role -> Set of permission codes
ROLE_PERMISSIONS: Dict[str, Set[str]] = {
    "OWNER": {
        "dashboard.view",
        "invoices.create",
        "invoices.edit_draft",
        "invoices.issue",
        "invoices.void",
        "invoices.view",
        "settings.manage",
        "reconciliation.manage",
    },
    "ADMIN": {
        "dashboard.view",
        "invoices.create",
        "invoices.edit_draft",
        "invoices.issue",
        "invoices.void",
        "invoices.view",
        "settings.manage",
        "reconciliation.manage",
    },
    "EMPLOYEE": {
        "dashboard.view",
        "invoices.create",
        "invoices.edit_draft",  # Employee allowed only if draft is their own (enforced at service level)
        "invoices.view",
    },
}


def has_permission(role: str, permission_code: str) -> bool:
    allowed_permissions = ROLE_PERMISSIONS.get(role.upper(), set())
    return permission_code in allowed_permissions


def require_permission(permission_code: str) -> Callable:
    """FastAPI dependency to require a specific permission code based on token role."""
    async def _permission_dependency(user: UserContext = Depends(get_current_user)) -> UserContext:
        if not has_permission(user.role, permission_code):
            logger.warning(
                "permission_denied",
                user_id=str(user.user_id),
                business_id=str(user.business_id),
                role=user.role,
                required_permission=permission_code,
            )
            raise ForbiddenException(f"Role '{user.role}' lacks required permission: {permission_code}")
        return user

    return _permission_dependency
