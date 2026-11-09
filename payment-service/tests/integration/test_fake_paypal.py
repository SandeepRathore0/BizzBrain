import pytest
from httpx import ASGITransport, AsyncClient
from fake_paypal.main import app as fake_paypal_app


@pytest.mark.asyncio
async def test_fake_paypal_flow():
    transport = ASGITransport(app=fake_paypal_app)
    async with AsyncClient(transport=transport, base_url="http://fake-paypal") as client:
        # Reset state
        await client.post("/test/reset")

        # 1. OAuth2 token
        token_res = await client.post("/v1/oauth2/token", data={"grant_type": "client_credentials"})
        assert token_res.status_code == 200
        token_data = token_res.json()
        assert "access_token" in token_data

        # 2. Create Order
        order_payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "amount": {
                        "currency_code": "USD",
                        "value": "150.00",
                    }
                }
            ],
        }
        create_res = await client.post(
            "/v2/checkout/orders",
            json=order_payload,
            headers={"PayPal-Request-Id": "req-123"},
        )
        assert create_res.status_code == 200
        order_data = create_res.json()
        order_id = order_data["id"]
        assert order_data["status"] == "CREATED"

        # 3. Get Order
        get_res = await client.get(f"/v2/checkout/orders/{order_id}")
        assert get_res.status_code == 200
        assert get_res.json()["id"] == order_id

        # 4. Set Order to APPROVED
        set_res = await client.post(f"/test/orders/{order_id}/set_status", json={"status": "APPROVED"})
        assert set_res.status_code == 200
        assert set_res.json()["status"] == "APPROVED"

        # 5. Capture Order
        cap_res = await client.post(
            f"/v2/checkout/orders/{order_id}/capture",
            headers={"PayPal-Request-Id": "cap-req-123"},
        )
        assert cap_res.status_code == 200
        cap_data = cap_res.json()
        assert cap_data["status"] == "COMPLETED"
        capture_obj = cap_data["purchase_units"][0]["payments"]["captures"][0]
        assert capture_obj["status"] == "COMPLETED"
        assert capture_obj["amount"]["value"] == "150.00"

        # 6. Verify webhook signature
        sig_res = await client.post(
            "/v1/notifications/verify-webhook-signature",
            json={"webhook_id": "wh-123"},
        )
        assert sig_res.status_code == 200
        assert sig_res.json()["verification_status"] == "SUCCESS"


@pytest.mark.asyncio
async def test_fake_paypal_simulated_decline_and_mismatch():
    transport = ASGITransport(app=fake_paypal_app)
    async with AsyncClient(transport=transport, base_url="http://fake-paypal") as client:
        await client.post("/test/reset")

        # Create Order
        create_res = await client.post(
            "/v2/checkout/orders",
            json={"purchase_units": [{"amount": {"currency_code": "USD", "value": "100.00"}}]},
        )
        order_id = create_res.json()["id"]

        # Simulate decline
        await client.post("/test/config", json={"force_capture_status": "DECLINED"})
        cap_res = await client.post(f"/v2/checkout/orders/{order_id}/capture")
        assert cap_res.status_code == 200
        cap_obj = cap_res.json()["purchase_units"][0]["payments"]["captures"][0]
        assert cap_obj["status"] == "DECLINED"
