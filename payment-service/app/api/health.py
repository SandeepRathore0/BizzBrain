from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import ServiceUnavailableException

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check(request: Request):
    req_id = getattr(request.state, "request_id", "")
    return {
        "success": True,
        "data": {"status": "healthy"},
        "request_id": req_id,
    }


@router.get("/ready")
async def readiness_check(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    req_id = getattr(request.state, "request_id", "")
    settings = get_settings()

    # 1. Check DB connectivity
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        raise ServiceUnavailableException(
            code="DATABASE_UNAVAILABLE",
            message=f"Database connectivity check failed: {e}",
        )

    # 2. Check PayPal credentials presence (do not call PayPal)
    if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
        raise ServiceUnavailableException(
            code="PAYPAL_CREDENTIALS_MISSING",
            message="PayPal credentials are not configured.",
        )

    return {
        "success": True,
        "data": {
            "status": "ready",
            "database": "connected",
            "paypal_configured": True,
        },
        "request_id": req_id,
    }
