import abc
import time
import uuid
from decimal import Decimal
from typing import Any, Optional
import httpx

from app.core.config import get_settings
from app.core.errors import BadGatewayException, ServiceUnavailableException
from app.core.logging import get_logger
from app.core.money import format_amount

logger = get_logger("paypal_client")


class PayPalClientInterface(abc.ABC):
    @abc.abstractmethod
    async def create_order(
        self,
        amount: Decimal,
        currency: str,
        paypal_request_id: uuid.UUID,
        reference_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create PayPal order with intent=CAPTURE and PayPal-Request-Id header."""
        pass

    @abc.abstractmethod
    async def get_order(self, order_id: str) -> dict[str, Any]:
        """Fetch order details from PayPal."""
        pass

    @abc.abstractmethod
    async def capture_order(
        self,
        order_id: str,
        capture_request_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Capture an approved PayPal order with capture PayPal-Request-Id header."""
        pass

    @abc.abstractmethod
    async def get_capture(self, capture_id: str) -> dict[str, Any]:
        """Fetch capture details from PayPal."""
        pass

    @abc.abstractmethod
    async def verify_webhook_signature(
        self,
        headers: dict[str, str],
        raw_body: str,
        webhook_id: str,
    ) -> bool:
        """Verify webhook signature via PayPal verification API."""
        pass


def sanitize_paypal_response(data: dict[str, Any]) -> dict[str, Any]:
    """Scrub sensitive credentials, tokens, or payer details from stored responses."""
    cleaned = data.copy()
    for sensitive_key in ["access_token", "client_secret", "card", "payment_source"]:
        if sensitive_key in cleaned:
            cleaned[sensitive_key] = "[REDACTED]"
    return cleaned


class RealPayPalClient(PayPalClientInterface):
    def __init__(self):
        self.cached_token: Optional[str] = None
        self.token_expires_at: float = 0.0

    async def _get_access_token(self) -> str:
        now = time.time()
        if self.cached_token and now < (self.token_expires_at - 120):
            return self.cached_token

        settings = get_settings()
        url = f"{settings.PAYPAL_BASE_URL}/v1/oauth2/token"
        auth = (settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET)

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(
                    url,
                    data={"grant_type": "client_credentials"},
                    auth=auth,
                )
                if resp.status_code == 200:
                    token_data = resp.json()
                    self.cached_token = token_data["access_token"]
                    self.token_expires_at = now + token_data.get("expires_in", 3600)
                    return self.cached_token
                else:
                    logger.error("paypal_oauth_failed", status_code=resp.status_code)
                    raise BadGatewayException(
                        code="PAYPAL_OAUTH_FAILED",
                        message="Failed to authenticate with PayPal provider.",
                    )
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            logger.error("paypal_oauth_network_error", error=str(exc))
            raise ServiceUnavailableException(
                code="PAYPAL_SERVICE_UNAVAILABLE",
                message=f"Network error connecting to PayPal: {exc}",
            )

    async def create_order(
        self,
        amount: Decimal,
        currency: str,
        paypal_request_id: uuid.UUID,
        reference_id: Optional[str] = None,
    ) -> dict[str, Any]:
        token = await self._get_access_token()
        settings = get_settings()
        url = f"{settings.PAYPAL_BASE_URL}/v2/checkout/orders"
        amount_str = format_amount(amount, currency)

        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": reference_id or "default",
                    "amount": {
                        "currency_code": currency,
                        "value": amount_str,
                    },
                }
            ],
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "PayPal-Request-Id": str(paypal_request_id),
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code in (200, 201):
                    return sanitize_paypal_response(resp.json())
                else:
                    logger.error("paypal_create_order_error", status_code=resp.status_code)
                    raise BadGatewayException(
                        code="PAYPAL_ORDER_CREATION_FAILED",
                        message=f"PayPal rejected order creation with status {resp.status_code}.",
                    )
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            raise ServiceUnavailableException(
                code="PAYPAL_GATEWAY_TIMEOUT",
                message=f"Timeout or network error during PayPal order creation: {exc}",
            )

    async def get_order(self, order_id: str) -> dict[str, Any]:
        token = await self._get_access_token()
        settings = get_settings()
        url = f"{settings.PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}"
        headers = {"Authorization": f"Bearer {token}"}

        # Safe GET with retry logic
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        return sanitize_paypal_response(resp.json())
                    elif resp.status_code == 404:
                        raise BadGatewayException(
                            code="PAYPAL_ORDER_NOT_FOUND",
                            message=f"Order {order_id} not found at PayPal.",
                        )
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                if attempt == 1:
                    raise ServiceUnavailableException(
                        code="PAYPAL_GATEWAY_TIMEOUT",
                        message=f"Failed to fetch order from PayPal: {exc}",
                    )
                time.sleep(0.5)

        raise BadGatewayException(code="PAYPAL_COMMUNICATION_ERROR", message="Failed to get order from PayPal.")

    async def capture_order(
        self,
        order_id: str,
        capture_request_id: uuid.UUID,
    ) -> dict[str, Any]:
        token = await self._get_access_token()
        settings = get_settings()
        url = f"{settings.PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}/capture"

        headers = {
            "Authorization": f"Bearer {token}",
            "PayPal-Request-Id": str(capture_request_id),
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(url, headers=headers)
                if resp.status_code in (200, 201):
                    return sanitize_paypal_response(resp.json())
                else:
                    logger.error("paypal_capture_error", status_code=resp.status_code)
                    raise BadGatewayException(
                        code="PAYPAL_CAPTURE_FAILED",
                        message=f"PayPal rejected capture with status {resp.status_code}.",
                    )
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            # Note: Timeout during capture yields uncertain state (REQUIRES_REVIEW)
            raise ServiceUnavailableException(
                code="PAYPAL_GATEWAY_TIMEOUT",
                message=f"Timeout communicating with PayPal capture: {exc}",
            )

    async def get_capture(self, capture_id: str) -> dict[str, Any]:
        token = await self._get_access_token()
        settings = get_settings()
        url = f"{settings.PAYPAL_BASE_URL}/v2/payments/captures/{capture_id}"
        headers = {"Authorization": f"Bearer {token}"}

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return sanitize_paypal_response(resp.json())
                else:
                    raise BadGatewayException(
                        code="PAYPAL_CAPTURE_NOT_FOUND",
                        message=f"Capture {capture_id} not found at PayPal.",
                    )
        except (httpx.RequestError, httpx.TimeoutException) as exc:
            raise ServiceUnavailableException(
                code="PAYPAL_GATEWAY_TIMEOUT",
                message=f"Network error querying PayPal capture: {exc}",
            )

    async def verify_webhook_signature(
        self,
        headers: dict[str, str],
        raw_body: str,
        webhook_id: str,
    ) -> bool:
        token = await self._get_access_token()
        settings = get_settings()
        url = f"{settings.PAYPAL_BASE_URL}/v1/notifications/verify-webhook-signature"

        verification_payload = {
            "auth_algo": headers.get("paypal-auth-algo") or headers.get("PAYPAL-AUTH-ALGO"),
            "cert_url": headers.get("paypal-cert-url") or headers.get("PAYPAL-CERT-URL"),
            "transmission_id": headers.get("paypal-transmission-id") or headers.get("PAYPAL-TRANSMISSION-ID"),
            "transmission_sig": headers.get("paypal-transmission-sig") or headers.get("PAYPAL-TRANSMISSION-SIG"),
            "transmission_time": headers.get("paypal-transmission-time") or headers.get("PAYPAL-TRANSMISSION-TIME"),
            "webhook_id": webhook_id,
            "webhook_event": httpx.Response(200, text=raw_body).json() if raw_body else {},
        }

        req_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
                resp = await client.post(url, json=verification_payload, headers=req_headers)
                if resp.status_code == 200:
                    status = resp.json().get("verification_status")
                    return status == "SUCCESS"
                return False
        except Exception:
            return False


class FakePayPalClient(PayPalClientInterface):
    """Client that communicates with the standalone Fake PayPal ASGI application."""
    def __init__(self, fake_app=None):
        from fake_paypal.main import app as default_fake_app
        self.app = fake_app or default_fake_app

    def _get_client(self) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app)
        return httpx.AsyncClient(transport=transport, base_url="http://fake-paypal")

    async def create_order(
        self,
        amount: Decimal,
        currency: str,
        paypal_request_id: uuid.UUID,
        reference_id: Optional[str] = None,
    ) -> dict[str, Any]:
        amount_str = format_amount(amount, currency)
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": reference_id or "default",
                    "amount": {
                        "currency_code": currency,
                        "value": amount_str,
                    },
                }
            ],
        }
        headers = {"PayPal-Request-Id": str(paypal_request_id)}
        async with self._get_client() as client:
            resp = await client.post("/v2/checkout/orders", json=payload, headers=headers)
            if resp.status_code in (200, 201):
                return resp.json()
            elif resp.status_code >= 500:
                raise BadGatewayException(
                    code="PAYPAL_GATEWAY_ERROR",
                    message="Fake PayPal simulated gateway failure.",
                )
            else:
                raise BadGatewayException(
                    code="PAYPAL_ORDER_CREATION_FAILED",
                    message=f"PayPal order creation failed: {resp.status_code}",
                )

    async def get_order(self, order_id: str) -> dict[str, Any]:
        async with self._get_client() as client:
            resp = await client.get(f"/v2/checkout/orders/{order_id}")
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                raise BadGatewayException(code="PAYPAL_ORDER_NOT_FOUND", message="Order not found")
            else:
                raise BadGatewayException(code="PAYPAL_GATEWAY_ERROR", message=f"PayPal error: {resp.status_code}")

    async def capture_order(
        self,
        order_id: str,
        capture_request_id: uuid.UUID,
    ) -> dict[str, Any]:
        headers = {"PayPal-Request-Id": str(capture_request_id)}
        async with self._get_client() as client:
            resp = await client.post(f"/v2/checkout/orders/{order_id}/capture", headers=headers)
            if resp.status_code in (200, 201):
                try:
                    return resp.json()
                except Exception:
                    raise BadGatewayException(
                        code="PAYPAL_MALFORMED_RESPONSE",
                        message="PayPal returned non-JSON response.",
                    )
            elif resp.status_code >= 500:
                raise ServiceUnavailableException(
                    code="PAYPAL_GATEWAY_TIMEOUT",
                    message="Simulated PayPal 5xx during capture.",
                )
            else:
                raise BadGatewayException(
                    code="PAYPAL_CAPTURE_FAILED",
                    message=f"PayPal capture failed: {resp.status_code}",
                )

    async def get_capture(self, capture_id: str) -> dict[str, Any]:
        async with self._get_client() as client:
            resp = await client.get(f"/v2/payments/captures/{capture_id}")
            if resp.status_code == 200:
                return resp.json()
            else:
                raise BadGatewayException(code="PAYPAL_CAPTURE_NOT_FOUND", message="Capture not found")

    async def verify_webhook_signature(
        self,
        headers: dict[str, str],
        raw_body: str,
        webhook_id: str,
    ) -> bool:
        async with self._get_client() as client:
            resp = await client.post(
                "/v1/notifications/verify-webhook-signature",
                json={"webhook_id": webhook_id},
            )
            if resp.status_code == 200:
                return resp.json().get("verification_status") == "SUCCESS"
            return False


_paypal_client_instance: Optional[PayPalClientInterface] = None


def get_paypal_client() -> PayPalClientInterface:
    global _paypal_client_instance
    if _paypal_client_instance is None:
        settings = get_settings()
        if settings.ENVIRONMENT in ("test", "development"):
            _paypal_client_instance = FakePayPalClient()
        else:
            _paypal_client_instance = RealPayPalClient()
    return _paypal_client_instance


def set_paypal_client(client: PayPalClientInterface) -> None:
    """Override PayPal client for testing."""
    global _paypal_client_instance
    _paypal_client_instance = client
