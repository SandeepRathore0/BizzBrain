import uuid
from decimal import Decimal
from typing import Any, Optional
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

app = FastAPI(title="Invoice Service Stub")

# In-memory store of invoices and tokens
INVOICES_STORE: dict[str, dict[str, Any]] = {}
PAY_TOKENS_STORE: dict[str, dict[str, Any]] = {}

STUB_CONFIG = {
    "force_status": None,
    "force_delay": 0.0,
    "events_received": [],
    "payment_event_outcome": "APPLIED",  # "APPLIED", "DUPLICATE_IGNORED", "REJECTED"
    "rejection_reason": "INVOICE_ALREADY_SETTLED",
}


@app.post("/test/reset")
def reset_stub():
    INVOICES_STORE.clear()
    PAY_TOKENS_STORE.clear()
    STUB_CONFIG["force_status"] = None
    STUB_CONFIG["force_delay"] = 0.0
    STUB_CONFIG["events_received"].clear()
    STUB_CONFIG["payment_event_outcome"] = "APPLIED"
    return {"status": "reset"}


@app.post("/test/seed_invoice")
def seed_invoice(payload: dict[str, Any]):
    iid = payload["invoice_id"]
    INVOICES_STORE[iid] = payload
    return {"seeded": iid}


@app.post("/test/seed_pay_token")
def seed_pay_token(payload: dict[str, Any]):
    tok = payload["token"]
    PAY_TOKENS_STORE[tok] = payload
    return {"seeded_token": tok}


@app.get("/internal/v1/invoices/{invoice_id}/payable-details")
async def get_payable_details(invoice_id: str, business_id: Optional[str] = None):
    if STUB_CONFIG["force_status"]:
        raise HTTPException(status_code=STUB_CONFIG["force_status"], detail="Simulated error")

    inv = INVOICES_STORE.get(invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if business_id and inv.get("business_id") != business_id:
        raise HTTPException(status_code=404, detail="Invoice not found for this business")

    return {
        "success": True,
        "data": inv,
    }


@app.post("/internal/v1/invoices/resolve-pay-token")
async def resolve_pay_token(payload: dict[str, Any]):
    if STUB_CONFIG["force_status"]:
        raise HTTPException(status_code=STUB_CONFIG["force_status"], detail="Simulated error")

    token = payload.get("token")
    tok_data = PAY_TOKENS_STORE.get(token)
    if not tok_data:
        raise HTTPException(status_code=404, detail="Invalid or expired pay token")

    return {
        "success": True,
        "data": {
            "invoice_id": tok_data["invoice_id"],
            "business_id": tok_data["business_id"],
        },
    }


@app.post("/internal/v1/payment-events")
async def receive_payment_event(request: Request):
    if STUB_CONFIG["force_status"]:
        raise HTTPException(status_code=STUB_CONFIG["force_status"], detail="Simulated error")

    event = await request.json()
    STUB_CONFIG["events_received"].append(event)

    outcome = STUB_CONFIG["payment_event_outcome"]
    if outcome == "APPLIED":
        return {"success": True, "data": {"status": "APPLIED"}}
    elif outcome == "DUPLICATE_IGNORED":
        return {"success": True, "data": {"status": "DUPLICATE_IGNORED"}}
    elif outcome == "REJECTED":
        return {
            "success": False,
            "error": {
                "code": "PAYMENT_REJECTED",
                "message": STUB_CONFIG["rejection_reason"],
            },
        }
    return {"success": True, "data": {"status": outcome}}
