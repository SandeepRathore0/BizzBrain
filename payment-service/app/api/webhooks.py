import uuid
from fastapi import APIRouter, Request, Response

from app.schemas.common import APISuccessResponse
from app.services.webhook_service import get_webhook_service

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/paypal", status_code=200)
async def receive_paypal_webhook(request: Request):
    """Receive and process incoming PayPal webhooks with signature verification."""
    raw_body = (await request.body()).decode("utf-8")
    headers = dict(request.headers)
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))

    service = get_webhook_service()
    result = await service.handle_paypal_webhook(
        raw_body=raw_body,
        headers=headers,
        request_id=req_id,
    )

    return {
        "success": True,
        "data": result,
        "request_id": req_id,
    }
