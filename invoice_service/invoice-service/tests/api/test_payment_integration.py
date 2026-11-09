from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from app.clients.financial_data_client import financial_data_client
from app.clients.user_service_client import user_service_client
from app.core.database import async_session_factory
from app.main import app
from app.models.invoice import AuditLog, Invoice, PaymentAllocation, ProcessedEvent, ReconciliationException
from tests.jwt_test_helpers import create_test_jwt, create_test_service_token, register_test_jwt_keys

register_test_jwt_keys()


@pytest.mark.asyncio
async def test_payable_details_contract_c2():
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    cust_id = uuid4()

    token_user = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})
    token_payment_service = create_test_service_token(client_id="payment-service")
    token_unauthorized_service = create_test_service_token(client_id="unknown-service")

    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)
    financial_data_client.set_cached_customer(
        cust_id,
        {"customer_id": str(cust_id), "business_id": biz_str, "name": "Payable Cust", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create a draft invoice
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "250.00", "tax_rate": "0.0000"}],
            },
        )
        inv_id = draft_resp.json()["data"]["invoice_id"]

        # 1. Security: Reject user token on internal route (401 or 403)
        user_rej_resp = await client.get(
            f"/internal/v1/invoices/{inv_id}/payable-details?business_id={biz_str}",
            headers={"Authorization": f"Bearer {token_user}"},
        )
        assert user_rej_resp.status_code in (401, 403)

        # 2. Security: Reject un-allowlisted service caller (403)
        caller_rej_resp = await client.get(
            f"/internal/v1/invoices/{inv_id}/payable-details?business_id={biz_str}",
            headers={"Authorization": f"Bearer {token_unauthorized_service}"},
        )
        assert caller_rej_resp.status_code == 403

        # 3. For DRAFT invoice: payable is FALSE
        draft_details = await client.get(
            f"/internal/v1/invoices/{inv_id}/payable-details?business_id={biz_str}",
            headers={"Authorization": f"Bearer {token_payment_service}"},
        )
        assert draft_details.status_code == 200
        assert draft_details.json()["data"]["payable"] is False
        assert draft_details.json()["data"]["status"] == "DRAFT"

        # 4. Issue the invoice
        await client.post(
            f"/api/v1/invoices/{inv_id}/issue",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str},
        )

        # 5. For ISSUED invoice: payable is TRUE
        issued_details = await client.get(
            f"/internal/v1/invoices/{inv_id}/payable-details?business_id={biz_str}",
            headers={"Authorization": f"Bearer {token_payment_service}"},
        )
        assert issued_details.status_code == 200
        data = issued_details.json()["data"]
        assert data["payable"] is True
        assert data["status"] == "ISSUED"
        assert data["total_amount"] == "250.00"
        assert data["amount_paid"] == "0.00"
        assert data["outstanding_balance"] == "250.00"
        assert data["currency"] == "USD"

        # 6. Cross-tenant isolation on internal route: wrong business_id returns 404
        foreign_details = await client.get(
            f"/internal/v1/invoices/{inv_id}/payable-details?business_id={str(uuid4())}",
            headers={"Authorization": f"Bearer {token_payment_service}"},
        )
        assert foreign_details.status_code == 404


@pytest.mark.asyncio
async def test_resolve_pay_token_contract_c4():
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    cust_id = uuid4()

    token_user = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})
    token_payment_service = create_test_service_token(client_id="payment-service")

    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)
    financial_data_client.set_cached_customer(
        cust_id,
        {"customer_id": str(cust_id), "business_id": biz_str, "name": "Token Cust", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create and issue invoice
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "100.00", "tax_rate": "0.0000"}],
            },
        )
        inv_id = draft_resp.json()["data"]["invoice_id"]

        issue_resp = await client.post(
            f"/api/v1/invoices/{inv_id}/issue",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str},
        )
        raw_token = issue_resp.json()["data"]["pay_token"]

        # 1. Resolve valid token -> returns invoice_id and business_id
        resolve_resp = await client.post(
            "/internal/v1/invoices/resolve-pay-token",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={"token": raw_token},
        )
        assert resolve_resp.status_code == 200
        assert resolve_resp.json()["data"]["invoice_id"] == inv_id
        assert resolve_resp.json()["data"]["business_id"] == biz_str

        # 2. Unknown / random token returns 404
        fake_resolve = await client.post(
            "/internal/v1/invoices/resolve-pay-token",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={"token": "completely-invalid-nonexistent-token-1234567890"},
        )
        assert fake_resolve.status_code == 404

        # 3. Regenerate pay link: old token must return 404, new token must resolve
        regen_resp = await client.post(
            f"/api/v1/invoices/{inv_id}/pay-link",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str},
        )
        new_token = regen_resp.json()["data"]["pay_token"]
        assert new_token != raw_token

        # Old token is now invalid (404)
        old_resolve = await client.post(
            "/internal/v1/invoices/resolve-pay-token",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={"token": raw_token},
        )
        assert old_resolve.status_code == 404

        # New token resolves cleanly
        new_resolve = await client.post(
            "/internal/v1/invoices/resolve-pay-token",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={"token": new_token},
        )
        assert new_resolve.status_code == 200
        assert new_resolve.json()["data"]["invoice_id"] == inv_id


@pytest.mark.asyncio
async def test_payment_events_allocation_lifecycle_contract_c3():
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    cust_id = uuid4()

    token_user = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})
    token_payment_service = create_test_service_token(client_id="payment-service")

    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)
    financial_data_client.set_cached_customer(
        cust_id,
        {"customer_id": str(cust_id), "business_id": biz_str, "name": "Payment Cust", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create and issue an invoice for $100.00
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Service Item", "quantity": "1.0000", "unit_price": "100.00", "tax_rate": "0.0000"}],
            },
        )
        inv_id = draft_resp.json()["data"]["invoice_id"]
        await client.post(
            f"/api/v1/invoices/{inv_id}/issue",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str},
        )

        # 1. Partial Payment ($40.00) -> Transitions to PARTIALLY_PAID
        event1_id = uuid4()
        payment1_id = uuid4()
        now_str = datetime.now(timezone.utc).isoformat()

        pay1_resp = await client.post(
            "/internal/v1/payment-events",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={
                "event_id": str(event1_id),
                "event_type": "PaymentCaptured",
                "event_version": 1,
                "occurred_at": now_str,
                "request_id": str(uuid4()),
                "business_id": biz_str,
                "payment_id": str(payment1_id),
                "invoice_id": inv_id,
                "amount": "40.00",
                "currency": "USD",
                "provider_capture_id": "CAP-001",
            },
        )
        assert pay1_resp.status_code == 200
        data1 = pay1_resp.json()["data"]
        assert data1["outcome"] == "APPLIED"
        assert data1["status"] == "PARTIALLY_PAID"
        assert data1["outstanding_balance"] == "60.00"

        # 2. Inbox Deduplication: Re-submitting the exact same event returns DUPLICATE_IGNORED
        dup_resp = await client.post(
            "/internal/v1/payment-events",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={
                "event_id": str(event1_id),
                "event_type": "PaymentCaptured",
                "event_version": 1,
                "occurred_at": now_str,
                "request_id": str(uuid4()),
                "business_id": biz_str,
                "payment_id": str(payment1_id),
                "invoice_id": inv_id,
                "amount": "40.00",
                "currency": "USD",
            },
        )
        assert dup_resp.status_code == 200
        assert dup_resp.json()["data"]["outcome"] == "DUPLICATE_IGNORED"

        # 3. Final Payment ($60.00) -> Transitions to PAID (balance zero)
        event2_id = uuid4()
        payment2_id = uuid4()
        pay2_resp = await client.post(
            "/internal/v1/payment-events",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={
                "event_id": str(event2_id),
                "event_type": "PaymentCaptured",
                "event_version": 1,
                "occurred_at": now_str,
                "request_id": str(uuid4()),
                "business_id": biz_str,
                "payment_id": str(payment2_id),
                "invoice_id": inv_id,
                "amount": "60.00",
                "currency": "USD",
                "provider_capture_id": "CAP-002",
            },
        )
        assert pay2_resp.status_code == 200
        data2 = pay2_resp.json()["data"]
        assert data2["outcome"] == "APPLIED"
        assert data2["status"] == "PAID"
        assert data2["outstanding_balance"] == "0.00"

        # 4. Verify Invoice Details from Public API
        inv_resp = await client.get(
            f"/api/v1/invoices/{inv_id}",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str},
        )
        inv_data = inv_resp.json()["data"]
        assert inv_data["status"] == "PAID"
        assert inv_data["amount_paid"] == "100.00"
        assert inv_data["outstanding_balance"] == "0.00"

        # 5. Verify Allocations Subresource shows both allocations
        alloc_resp = await client.get(
            f"/api/v1/invoices/{inv_id}/allocations",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str},
        )
        allocs = alloc_resp.json()["data"]
        assert len(allocs) == 2
        amounts = [a["allocated_amount"] for a in allocs]
        assert "40.00" in amounts
        assert "60.00" in amounts


@pytest.mark.asyncio
async def test_payment_events_reconciliation_exceptions():
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    cust_id = uuid4()

    token_user = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})
    token_payment_service = create_test_service_token(client_id="payment-service")

    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)
    financial_data_client.set_cached_customer(
        cust_id,
        {"customer_id": str(cust_id), "business_id": biz_str, "name": "Recon Cust", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create and issue an invoice for $50.00 USD
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Item", "quantity": "1.0000", "unit_price": "50.00", "tax_rate": "0.0000"}],
            },
        )
        inv_id = draft_resp.json()["data"]["invoice_id"]
        await client.post(
            f"/api/v1/invoices/{inv_id}/issue",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str},
        )
        now_str = datetime.now(timezone.utc).isoformat()

        # Case 1: INVOICE_NOT_FOUND
        nf_resp = await client.post(
            "/internal/v1/payment-events",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={
                "event_id": str(uuid4()),
                "event_type": "PaymentCaptured",
                "event_version": 1,
                "occurred_at": now_str,
                "request_id": str(uuid4()),
                "business_id": biz_str,
                "payment_id": str(uuid4()),
                "invoice_id": str(uuid4()),  # Non-existent
                "amount": "50.00",
                "currency": "USD",
            },
        )
        assert nf_resp.status_code == 200
        assert nf_resp.json()["data"]["outcome"] == "REJECTED"
        assert nf_resp.json()["data"]["reason_code"] == "INVOICE_NOT_FOUND"

        # Case 2: CURRENCY_MISMATCH
        curr_resp = await client.post(
            "/internal/v1/payment-events",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={
                "event_id": str(uuid4()),
                "event_type": "PaymentCaptured",
                "event_version": 1,
                "occurred_at": now_str,
                "request_id": str(uuid4()),
                "business_id": biz_str,
                "payment_id": str(uuid4()),
                "invoice_id": inv_id,
                "amount": "50.00",
                "currency": "EUR",  # Invoice is USD
            },
        )
        assert curr_resp.status_code == 200
        assert curr_resp.json()["data"]["outcome"] == "REJECTED"
        assert curr_resp.json()["data"]["reason_code"] == "CURRENCY_MISMATCH"

        # Case 3: BUSINESS_MISMATCH
        foreign_biz_str = str(uuid4())
        biz_resp = await client.post(
            "/internal/v1/payment-events",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={
                "event_id": str(uuid4()),
                "event_type": "PaymentCaptured",
                "event_version": 1,
                "occurred_at": now_str,
                "request_id": str(uuid4()),
                "business_id": foreign_biz_str,
                "payment_id": str(uuid4()),
                "invoice_id": inv_id,
                "amount": "50.00",
                "currency": "USD",
            },
        )
        assert biz_resp.status_code == 200
        assert biz_resp.json()["data"]["outcome"] == "REJECTED"
        assert biz_resp.json()["data"]["reason_code"] == "BUSINESS_MISMATCH"

        # Case 4: AMOUNT_EXCEEDS_BALANCE ($50 balance, paying $50.01)
        exceed_resp = await client.post(
            "/internal/v1/payment-events",
            headers={"Authorization": f"Bearer {token_payment_service}"},
            json={
                "event_id": str(uuid4()),
                "event_type": "PaymentCaptured",
                "event_version": 1,
                "occurred_at": now_str,
                "request_id": str(uuid4()),
                "business_id": biz_str,
                "payment_id": str(uuid4()),
                "invoice_id": inv_id,
                "amount": "50.0100",
                "currency": "USD",
            },
        )
        assert exceed_resp.status_code == 200
        assert exceed_resp.json()["data"]["outcome"] == "REJECTED"
        assert exceed_resp.json()["data"]["reason_code"] == "AMOUNT_EXCEEDS_BALANCE"

        # Verify DB reconciliation_exceptions rows exist and have status 'OPEN'
        async with async_session_factory() as session:
            stmt = select(ReconciliationException).where(
                ReconciliationException.business_id == biz_id
            )
            res = await session.execute(stmt)
            exceptions = list(res.scalars().all())
            reason_codes = {e.reason_code for e in exceptions}
            assert "INVOICE_NOT_FOUND" in reason_codes
            assert "CURRENCY_MISMATCH" in reason_codes
            assert "AMOUNT_EXCEEDS_BALANCE" in reason_codes
            for e in exceptions:
                assert e.status == "OPEN"



@pytest.mark.asyncio
async def test_concurrent_payment_allocations_atomic():
    biz_id = uuid4()
    biz_str = str(biz_id)
    owner_id = uuid4()
    cust_id = uuid4()

    token_user = create_test_jwt(user_id=owner_id, memberships={biz_str: "OWNER"})
    token_payment_service = create_test_service_token(client_id="payment-service")

    user_service_client.set_cached_business_metadata(biz_id, currency="USD", timezone="UTC")
    user_service_client.set_authz_override(owner_id, biz_id, "invoices.issue", True)
    financial_data_client.set_cached_customer(
        cust_id,
        {"customer_id": str(cust_id), "business_id": biz_str, "name": "Concurrent Cust", "is_active": True},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create and issue an invoice for $200.00
        due = (date.today() + timedelta(days=30)).isoformat()
        draft_resp = await client.post(
            "/api/v1/invoices",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str, "Idempotency-Key": str(uuid4())},
            json={
                "customer_id": str(cust_id),
                "due_date": due,
                "currency": "USD",
                "items": [{"description": "Bulk Item", "quantity": "1.0000", "unit_price": "200.00", "tax_rate": "0.0000"}],
            },
        )
        inv_id = draft_resp.json()["data"]["invoice_id"]
        await client.post(
            f"/api/v1/invoices/{inv_id}/issue",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str},
        )
        now_str = datetime.now(timezone.utc).isoformat()

        # Execute 2 concurrent $50 payments
        async def _send_payment(amt: str):
            return await client.post(
                "/internal/v1/payment-events",
                headers={"Authorization": f"Bearer {token_payment_service}"},
                json={
                    "event_id": str(uuid4()),
                    "event_type": "PaymentCaptured",
                    "event_version": 1,
                    "occurred_at": now_str,
                    "request_id": str(uuid4()),
                    "business_id": biz_str,
                    "payment_id": str(uuid4()),
                    "invoice_id": inv_id,
                    "amount": amt,
                    "currency": "USD",
                },
            )

        import asyncio
        resp1, resp2 = await asyncio.gather(_send_payment("50.00"), _send_payment("50.00"))

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["data"]["outcome"] == "APPLIED"
        assert resp2.json()["data"]["outcome"] == "APPLIED"

        # Check final balance: exactly $100.00 paid, $100.00 remaining, PARTIALLY_PAID
        inv_resp = await client.get(
            f"/api/v1/invoices/{inv_id}",
            headers={"Authorization": f"Bearer {token_user}", "X-Business-ID": biz_str},
        )
        inv_data = inv_resp.json()["data"]
        assert inv_data["amount_paid"] == "100.00"
        assert inv_data["outstanding_balance"] == "100.00"
        assert inv_data["status"] == "PARTIALLY_PAID"
