from datetime import datetime, timezone
import hashlib
import uuid
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.errors import ConflictException, NotFoundException, ValidationException
from app.core.permissions import AuthContext
from app.core.storage import MAX_FILE_SIZE, sniff_content_type, storage_service
from app.models.models import ExpenseReceipt
from app.repositories.audit_repository import AuditRepository
from app.repositories.expense_repository import ExpenseRepository
from app.repositories.receipt_repository import ReceiptRepository
from app.schemas.receipt import ReceiptDownloadResponse, ReceiptResponse


class ReceiptService:
    @staticmethod
    async def upload_receipt(
        session: AsyncSession,
        auth_context: AuthContext,
        expense_id: uuid.UUID,
        file: UploadFile,
        request_id: str,
    ) -> ReceiptResponse:
        # 1. Fetch expense and verify ownership
        expense = await ExpenseRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            expense_id=expense_id,
        )
        if not expense:
            raise NotFoundException("Expense not found")

        # Employee isolation: only own expenses
        if auth_context.is_employee and expense.created_by != auth_context.user_id:
            raise NotFoundException("Expense not found")

        # Cannot attach receipt to a voided expense
        if expense.status == "VOID":
            raise ConflictException(
                code="CANNOT_EDIT_VOID",
                message="Cannot attach a receipt to a voided expense",
            )

        # 2. Read file content
        content = await file.read()
        size_bytes = len(content)

        if size_bytes == 0:
            raise ValidationException(
                code="EMPTY_FILE",
                message="Uploaded file is empty",
            )

        if size_bytes > MAX_FILE_SIZE:
            raise ValidationException(
                code="FILE_TOO_LARGE",
                message=f"File size exceeds maximum allowed limit of {MAX_FILE_SIZE // (1024 * 1024)} MB",
            )

        # 3. Magic-bytes content type detection
        detected_mime, ext = sniff_content_type(content)
        if not detected_mime or not ext:
            raise ValidationException(
                code="INVALID_FILE_TYPE",
                message="Invalid file format. Allowed types are PDF, PNG, and JPEG verified by content inspection.",
            )

        # 4. SHA-256 calculation
        sha256 = hashlib.sha256(content).hexdigest()

        # 5. Generate secure storage key
        receipt_id = uuid.uuid4()
        file_key = f"receipts/{auth_context.business_id}/{expense_id}/{receipt_id}.{ext}"

        # 6. Upload to private object storage
        await storage_service.upload_file(
            file_key=file_key,
            content=content,
            content_type=detected_mime,
        )

        # 7. Create DB record
        receipt = ExpenseReceipt(
            receipt_id=receipt_id,
            expense_id=expense_id,
            business_id=auth_context.business_id,
            file_key=file_key,
            content_type=detected_mime,
            size_bytes=size_bytes,
            sha256=sha256,
            uploaded_by=auth_context.user_id,
            uploaded_at=datetime.now(timezone.utc),
        )
        await ReceiptRepository.create(session, receipt)

        # Update expense receipt key
        expense.receipt_file_key = file_key

        # 8. Audit log entry
        await AuditRepository.log_action(
            session=session,
            business_id=auth_context.business_id,
            actor_type="USER",
            actor_id=str(auth_context.user_id),
            action="RECEIPT_UPLOADED",
            entity_type="expense_receipt",
            entity_id=receipt.receipt_id,
            request_id=request_id,
            before=None,
            after={
                "expense_id": str(expense_id),
                "file_key": file_key,
                "content_type": detected_mime,
                "size_bytes": size_bytes,
                "sha256": sha256,
            },
        )

        await session.commit()
        await session.refresh(receipt)
        return ReceiptResponse.model_validate(receipt)

    @staticmethod
    async def get_receipt_download_url(
        session: AsyncSession,
        auth_context: AuthContext,
        expense_id: uuid.UUID,
    ) -> ReceiptDownloadResponse:
        # 1. Fetch expense
        expense = await ExpenseRepository.get_by_id(
            session=session,
            business_id=auth_context.business_id,
            expense_id=expense_id,
        )
        if not expense:
            raise NotFoundException("Expense not found")

        # Employee isolation
        if auth_context.is_employee and expense.created_by != auth_context.user_id:
            raise NotFoundException("Expense not found")

        # 2. Fetch receipt
        receipt = await ReceiptRepository.get_by_expense_id(
            session=session,
            business_id=auth_context.business_id,
            expense_id=expense_id,
        )
        if not receipt:
            raise NotFoundException("Receipt not found for this expense")

        # 3. Generate presigned URL
        expires_in = 900  # 15 minutes
        url = await storage_service.generate_presigned_download_url(
            file_key=receipt.file_key,
            expires_in=expires_in,
        )

        return ReceiptDownloadResponse(url=url, expires_in_seconds=expires_in)
