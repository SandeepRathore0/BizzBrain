from datetime import date
import io
import uuid
import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from app.clients.user_service_client import BusinessDetails, user_service_client
from app.core.storage import storage_service
from app.models.models import AuditLog, Expense


@pytest.fixture(autouse=True)
def setup_receipt_env():
    storage_service.enable_in_memory_mode()

    async def fake_get_business(business_id: uuid.UUID, request_id: str):
        return BusinessDetails(
            business_id=business_id,
            base_currency="USD",
            timezone="UTC",
            is_active=True,
        )

    with patch.object(user_service_client, "get_business_details", new=AsyncMock(side_effect=fake_get_business)):
        yield


@pytest.mark.asyncio
async def test_upload_pdf_receipt_success(client: AsyncClient, create_token, db_session):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(business_id)}

    # Record expense
    resp_exp = await client.post(
        "/api/v1/expenses",
        json={"description": "Flight ticket", "amount": "350.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert resp_exp.status_code == 201
    expense_id = resp_exp.json()["data"]["expense_id"]

    # Upload PDF
    pdf_content = b"%PDF-1.4 sample content for receipt testing"
    files = {"file": ("receipt.pdf", io.BytesIO(pdf_content), "application/pdf")}

    resp = await client.post(f"/api/v1/expenses/{expense_id}/receipt", files=files, headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["content_type"] == "application/pdf"
    assert data["size_bytes"] == len(pdf_content)
    assert data["expense_id"] == expense_id
    assert data["file_key"].endswith(".pdf")

    # Verify expense record has updated receipt_file_key
    exp_stmt = select(Expense).where(Expense.expense_id == uuid.UUID(expense_id))
    exp_result = await db_session.execute(exp_stmt)
    expense_row = exp_result.scalar_one()
    assert expense_row.receipt_file_key == data["file_key"]

    # Verify audit log
    audit_stmt = select(AuditLog).where(
        AuditLog.entity_id == uuid.UUID(data["receipt_id"]),
        AuditLog.action == "RECEIPT_UPLOADED",
    )
    audit_result = await db_session.execute(audit_stmt)
    audit_entry = audit_result.scalar_one_or_none()
    assert audit_entry is not None
    assert audit_entry.actor_id == str(user_id)
    assert audit_entry.after["content_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_upload_png_and_jpeg_receipts(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "ADMIN"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(business_id)}

    # PNG test
    resp_exp1 = await client.post(
        "/api/v1/expenses",
        json={"description": "Gas station", "amount": "40.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    exp1_id = resp_exp1.json()["data"]["expense_id"]

    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    files_png = {"file": ("receipt.png", io.BytesIO(png_bytes), "image/png")}
    resp_png = await client.post(f"/api/v1/expenses/{exp1_id}/receipt", files=files_png, headers=headers)
    assert resp_png.status_code == 201
    assert resp_png.json()["data"]["content_type"] == "image/png"

    # JPEG test
    resp_exp2 = await client.post(
        "/api/v1/expenses",
        json={"description": "Hardware", "amount": "85.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    exp2_id = resp_exp2.json()["data"]["expense_id"]

    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    files_jpeg = {"file": ("receipt.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")}
    resp_jpeg = await client.post(f"/api/v1/expenses/{exp2_id}/receipt", files=files_jpeg, headers=headers)
    assert resp_jpeg.status_code == 201
    assert resp_jpeg.json()["data"]["content_type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_upload_disguised_file_sniffing_rejection(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(business_id)}

    resp_exp = await client.post(
        "/api/v1/expenses",
        json={"description": "Office desks", "amount": "500.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    exp_id = resp_exp.json()["data"]["expense_id"]

    # File named invoice.pdf and claiming application/pdf, but actually plain text
    fake_pdf = b"Hello, this is just plain text, not a real PDF binary."
    files = {"file": ("invoice.pdf", io.BytesIO(fake_pdf), "application/pdf")}

    resp = await client.post(f"/api/v1/expenses/{exp_id}/receipt", files=files, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_FILE_TYPE"


@pytest.mark.asyncio
async def test_upload_file_too_large_rejection(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(business_id)}

    resp_exp = await client.post(
        "/api/v1/expenses",
        json={"description": "Conference", "amount": "600.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    exp_id = resp_exp.json()["data"]["expense_id"]

    # 5 MB + 1 byte
    oversized_pdf = b"%PDF-1.4" + (b"0" * (5 * 1024 * 1024 + 1))
    files = {"file": ("big.pdf", io.BytesIO(oversized_pdf), "application/pdf")}

    resp = await client.post(f"/api/v1/expenses/{exp_id}/receipt", files=files, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_upload_to_void_expense_conflict(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(business_id)}

    resp_exp = await client.post(
        "/api/v1/expenses",
        json={"description": "To Void", "amount": "10.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    exp_id = resp_exp.json()["data"]["expense_id"]

    # Void expense
    with patch.object(user_service_client, "check_live_authz", new=AsyncMock(return_value=True)):
        await client.post(
            f"/api/v1/expenses/{exp_id}/void",
            json={"void_reason": "Cancelled"},
            headers=headers,
        )

    # Attempt to upload receipt -> 409 Conflict
    pdf_content = b"%PDF-1.4 valid content"
    files = {"file": ("receipt.pdf", io.BytesIO(pdf_content), "application/pdf")}
    resp = await client.post(f"/api/v1/expenses/{exp_id}/receipt", files=files, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CANNOT_EDIT_VOID"


@pytest.mark.asyncio
async def test_get_receipt_download_url(client: AsyncClient, create_token):
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    token = create_token(user_id=user_id, memberships={str(business_id): "OWNER"})
    headers = {"Authorization": f"Bearer {token}", "X-Business-ID": str(business_id)}

    resp_exp = await client.post(
        "/api/v1/expenses",
        json={"description": "Taxi", "amount": "22.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    exp_id = resp_exp.json()["data"]["expense_id"]

    # Before upload -> 404
    resp_get_none = await client.get(f"/api/v1/expenses/{exp_id}/receipt", headers=headers)
    assert resp_get_none.status_code == 404

    # Upload
    pdf_content = b"%PDF-1.4 taxi receipt"
    files = {"file": ("taxi.pdf", io.BytesIO(pdf_content), "application/pdf")}
    await client.post(f"/api/v1/expenses/{exp_id}/receipt", files=files, headers=headers)

    # After upload -> 200 with presigned url
    resp_get = await client.get(f"/api/v1/expenses/{exp_id}/receipt", headers=headers)
    assert resp_get.status_code == 200
    data = resp_get.json()["data"]
    assert "url" in data
    assert data["expires_in_seconds"] == 900


@pytest.mark.asyncio
async def test_receipt_employee_and_tenant_isolation(client: AsyncClient, create_token):
    bus1_id = uuid.uuid4()
    bus2_id = uuid.uuid4()
    emp1_id = uuid.uuid4()
    emp2_id = uuid.uuid4()

    token_emp1 = create_token(user_id=emp1_id, memberships={str(bus1_id): "EMPLOYEE"})
    token_emp2 = create_token(user_id=emp2_id, memberships={str(bus1_id): "EMPLOYEE"})
    token_bus2 = create_token(user_id=uuid.uuid4(), memberships={str(bus2_id): "OWNER"})

    headers_emp1 = {"Authorization": f"Bearer {token_emp1}", "X-Business-ID": str(bus1_id)}
    headers_emp2 = {"Authorization": f"Bearer {token_emp2}", "X-Business-ID": str(bus1_id)}
    headers_bus2 = {"Authorization": f"Bearer {token_bus2}", "X-Business-ID": str(bus2_id)}

    # Emp1 records expense
    resp_exp = await client.post(
        "/api/v1/expenses",
        json={"description": "Emp1 dinner", "amount": "30.00", "currency": "USD", "expense_date": date.today().isoformat()},
        headers={**headers_emp1, "Idempotency-Key": str(uuid.uuid4())},
    )
    exp_id = resp_exp.json()["data"]["expense_id"]

    pdf_content = b"%PDF-1.4 receipt content"
    files = {"file": ("receipt.pdf", io.BytesIO(pdf_content), "application/pdf")}

    # Emp2 tries to upload receipt to Emp1's expense -> 404
    resp_emp2_up = await client.post(f"/api/v1/expenses/{exp_id}/receipt", files=files, headers=headers_emp2)
    assert resp_emp2_up.status_code == 404

    # Emp1 uploads receipt successfully
    resp_emp1_up = await client.post(f"/api/v1/expenses/{exp_id}/receipt", files=files, headers=headers_emp1)
    assert resp_emp1_up.status_code == 201

    # Emp2 tries to get download URL -> 404
    resp_emp2_get = await client.get(f"/api/v1/expenses/{exp_id}/receipt", headers=headers_emp2)
    assert resp_emp2_get.status_code == 404

    # Bus2 (foreign tenant) tries to get download URL -> 404
    resp_bus2_get = await client.get(f"/api/v1/expenses/{exp_id}/receipt", headers=headers_bus2)
    assert resp_bus2_get.status_code == 404
