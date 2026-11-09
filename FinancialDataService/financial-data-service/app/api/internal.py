from typing import Set
import uuid
from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import ServiceTokenPayload, verify_service_token
from app.core.config import settings
from app.core.database import get_db_session
from app.core.errors import ForbiddenException, NotFoundException, UnauthorizedException
from app.repositories.customer_repository import CustomerRepository
from app.schemas.common import SuccessEnvelope
from app.schemas.customer import CustomerResponse

router = APIRouter(prefix="/internal/v1", tags=["Internal"])

ALLOWED_INTERNAL_CALLERS: Set[str] = {
    "invoice-service",
    "user-service",
    "financial-data-service",
}


async def require_internal_service(request: Request) -> ServiceTokenPayload:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise UnauthorizedException("Missing Authorization header on internal route")

    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise UnauthorizedException("Invalid Authorization header format")

    token = parts[1]
    # Expected audience must be this service's name
    service_payload = await verify_service_token(
        token=token,
        expected_audience=settings.SERVICE_NAME,
    )

    if service_payload.sub not in ALLOWED_INTERNAL_CALLERS:
        raise ForbiddenException(f"Caller '{service_payload.sub}' is not authorized to access internal endpoints")

    return service_payload


@router.get(
    "/customers/{customer_id}",
    response_model=SuccessEnvelope[CustomerResponse],
    status_code=status.HTTP_200_OK,
)
async def get_internal_customer(
    customer_id: uuid.UUID,
    request: Request,
    business_id: uuid.UUID = Query(..., description="Business ID parameter (mandatory)"),
    service_token: ServiceTokenPayload = Depends(require_internal_service),
    session: AsyncSession = Depends(get_db_session),
) -> SuccessEnvelope[CustomerResponse]:
    """
    Contract C1: Invoice Service calls Financial Data Service.
    Retrieves customer snapshot and is_active flag.
    """
    request_id = getattr(request.state, "request_id", "unknown")

    customer = await CustomerRepository.get_by_id(
        session=session,
        business_id=business_id,
        customer_id=customer_id,
    )
    if not customer:
        raise NotFoundException("Customer not found")

    return SuccessEnvelope(
        data=CustomerResponse.model_validate(customer),
        request_id=request_id,
    )
