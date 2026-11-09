import asyncio
import time
import uuid
from typing import Any, Optional
from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

app = FastAPI(title="Fake PayPal Orders v2 & Webhooks Mock Server")

# In-memory store for orders and state
ORDERS_STORE: dict[str, dict[str, Any]] = {}
CAPTURES_STORE: dict[str, dict[str, Any]] = {}

CALL_COUNTS = {
    "create_order": 0,
    "capture_order": 0,
    "get_order": 0,
    "get_capture": 0,
    "verify_webhook": 0,
}

# Simulation state configuration
CONFIG = {
    "force_status": None,  # e.g., 500, 502, 503
    "force_delay": 0.0,
    "force_capture_status": "COMPLETED",  # "COMPLETED", "DECLINED", "FAILED"
    "force_capture_500": False,
    "force_amount_mismatch": False,
    "force_malformed_capture": False,
    "force_signature_verification_status": "SUCCESS",  # "SUCCESS" or "FAILURE"
}


class TokenResponse(BaseModel):
    scope: str = "https://api-m.paypal.com/v1/payments/.*"
    access_token: str
    token_type: str = "Bearer"
    app_id: str = "APP-80W284485P519543T"
    expires_in: int = 32400
    nonce: str = "fake-nonce"


@app.post("/test/reset")
def reset_state():
    """Reset store and mock behaviors for tests."""
    ORDERS_STORE.clear()
    CAPTURES_STORE.clear()
    for k in CALL_COUNTS:
        CALL_COUNTS[k] = 0
    CONFIG["force_status"] = None
    CONFIG["force_delay"] = 0.0
    CONFIG["force_capture_status"] = "COMPLETED"
    CONFIG["force_capture_500"] = False
    CONFIG["force_amount_mismatch"] = False
    CONFIG["force_malformed_capture"] = False
    CONFIG["force_signature_verification_status"] = "SUCCESS"
    return {"status": "reset"}


@app.get("/test/call_counts")
def get_call_counts():
    return dict(CALL_COUNTS)


@app.post("/test/seed_order")
def seed_order(order: dict[str, Any]):
    oid = order["id"]
    ORDERS_STORE[oid] = order
    return {"seeded": oid}


@app.post("/test/seed_capture")
def seed_capture(capture: dict[str, Any]):
    cid = capture["id"]
    CAPTURES_STORE[cid] = capture
    return {"seeded": cid}


@app.post("/test/config")
def update_config(payload: dict[str, Any]):
    """Update behavior configuration dynamically."""
    CONFIG.update(payload)
    return CONFIG


@app.post("/test/orders/{order_id}/set_status")
def set_order_status(order_id: str, payload: dict[str, Any]):
    """Force an order into APPROVED or another status for testing."""
    if order_id not in ORDERS_STORE:
        raise HTTPException(status_code=404, detail="Order not found")
    ORDERS_STORE[order_id]["status"] = payload.get("status", "APPROVED")
    return ORDERS_STORE[order_id]


@app.post("/v1/oauth2/token")
async def get_token(request: Request):
    if CONFIG["force_delay"] > 0:
        await asyncio.sleep(CONFIG["force_delay"])
    if CONFIG["force_status"]:
        raise HTTPException(status_code=CONFIG["force_status"], detail="Simulated error")

    return TokenResponse(access_token=f"fake_access_token_{uuid.uuid4().hex[:12]}")


@app.post("/v2/checkout/orders")
async def create_order(
    request: Request,
    paypal_request_id: Optional[str] = Header(None, alias="PayPal-Request-Id"),
):
    CALL_COUNTS["create_order"] += 1
    if CONFIG["force_delay"] > 0:
        await asyncio.sleep(CONFIG["force_delay"])
    if CONFIG["force_status"]:
        raise HTTPException(status_code=CONFIG["force_status"], detail="Simulated PayPal create error")

    body = await request.json()
    order_id = f"ORDER-{uuid.uuid4().hex[:12].upper()}"

    purchase_units = body.get("purchase_units", [{}])
    unit = purchase_units[0] if purchase_units else {}
    amount_obj = unit.get("amount", {"currency_code": "USD", "value": "100.00"})

    order_record = {
        "id": order_id,
        "status": "CREATED",
        "intent": body.get("intent", "CAPTURE"),
        "paypal_request_id": paypal_request_id,
        "purchase_units": [
            {
                "reference_id": unit.get("reference_id", "default"),
                "amount": amount_obj,
            }
        ],
        "links": [
            {
                "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}",
                "rel": "approve",
                "method": "GET",
            },
            {
                "href": f"https://api-m.sandbox.paypal.com/v2/checkout/orders/{order_id}/capture",
                "rel": "capture",
                "method": "POST",
            },
        ],
        "create_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    ORDERS_STORE[order_id] = order_record

    return order_record


@app.get("/v2/checkout/orders/{order_id}")
async def get_order(order_id: str):
    CALL_COUNTS["get_order"] += 1
    if CONFIG["force_delay"] > 0:
        await asyncio.sleep(CONFIG["force_delay"])
    if CONFIG["force_status"]:
        raise HTTPException(status_code=CONFIG["force_status"], detail="Simulated PayPal get error")

    order = ORDERS_STORE.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


@app.post("/v2/checkout/orders/{order_id}/capture")
async def capture_order(
    order_id: str,
    request: Request,
    paypal_request_id: Optional[str] = Header(None, alias="PayPal-Request-Id"),
):
    CALL_COUNTS["capture_order"] += 1
    if CONFIG["force_delay"] > 0:
        await asyncio.sleep(CONFIG["force_delay"])
    if CONFIG["force_status"] or CONFIG.get("force_capture_500"):
        raise HTTPException(status_code=500, detail="Simulated PayPal capture error")

    if CONFIG["force_malformed_capture"]:
        return Response(content="INVALID_JSON_RESPONSE", media_type="text/plain")

    order = ORDERS_STORE.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    unit = order["purchase_units"][0]
    expected_amount = unit["amount"]["value"]
    expected_currency = unit["amount"]["currency_code"]

    if CONFIG["force_amount_mismatch"]:
        # Return mismatch amount
        captured_amount = "99999.99"
    else:
        captured_amount = expected_amount

    capture_id = f"CAPTURE-{uuid.uuid4().hex[:12].upper()}"
    status = CONFIG["force_capture_status"]

    capture_obj = {
        "id": capture_id,
        "status": status,
        "amount": {
            "value": captured_amount,
            "currency_code": expected_currency,
        },
        "final_capture": True,
        "create_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "update_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if status == "COMPLETED":
        order["status"] = "COMPLETED"
    elif status == "DECLINED":
        capture_obj["status_details"] = {"reason": "TRANSACTION_REFUSED"}

    response_body = {
        "id": order_id,
        "status": order["status"],
        "purchase_units": [
            {
                "payments": {
                    "captures": [capture_obj]
                }
            }
        ],
    }

    CAPTURES_STORE[capture_id] = capture_obj
    return response_body


@app.get("/v2/payments/captures/{capture_id}")
async def get_capture(capture_id: str):
    CALL_COUNTS["get_capture"] += 1
    if CONFIG["force_delay"] > 0:
        await asyncio.sleep(CONFIG["force_delay"])
    if CONFIG["force_status"]:
        raise HTTPException(status_code=CONFIG["force_status"], detail="Simulated PayPal get capture error")

    cap = CAPTURES_STORE.get(capture_id)
    if not cap:
        raise HTTPException(status_code=404, detail="Capture not found")
    return cap


@app.post("/v1/notifications/verify-webhook-signature")
async def verify_webhook_signature(request: Request):
    CALL_COUNTS["verify_webhook"] += 1
    if CONFIG["force_delay"] > 0:
        await asyncio.sleep(CONFIG["force_delay"])
    if CONFIG["force_status"]:
        raise HTTPException(status_code=CONFIG["force_status"], detail="Simulated webhook verify error")

    return {
        "verification_status": CONFIG["force_signature_verification_status"]
    }
