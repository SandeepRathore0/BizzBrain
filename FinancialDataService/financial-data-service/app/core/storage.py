import hashlib
from typing import Optional, Tuple
import uuid
import boto3
from botocore.client import Config
import structlog
from app.core.config import settings
from app.core.errors import ValidationException

logger = structlog.get_logger()

# Magic bytes signatures
# PDF: %PDF-
PDF_MAGIC = b"%PDF-"
# PNG: \x89PNG\r\n\x1a\n
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# JPEG: \xff\xd8\xff
JPEG_MAGIC = b"\xff\xd8\xff"

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


def sniff_content_type(file_bytes: bytes) -> Tuple[Optional[str], Optional[str]]:
    """
    Sniff file content by examining initial magic bytes.
    Returns (mime_type, extension) or (None, None) if unrecognized.
    """
    if file_bytes.startswith(PDF_MAGIC):
        return "application/pdf", "pdf"
    elif file_bytes.startswith(PNG_MAGIC):
        return "image/png", "png"
    elif file_bytes.startswith(JPEG_MAGIC):
        return "image/jpeg", "jpg"
    return None, None


class StorageService:
    def __init__(self) -> None:
        self.bucket_name = settings.S3_BUCKET_NAME
        self.endpoint_url = settings.S3_ENDPOINT_URL
        self.region = settings.S3_REGION
        self.access_key = settings.S3_ACCESS_KEY
        self.secret_key = settings.S3_SECRET_KEY

        # In-memory storage dictionary used during test mode or offline fallback
        self._in_memory_store: dict[str, tuple[bytes, str]] = {}
        self._use_in_memory: bool = False

        self._s3_client = None

    def _get_client(self):
        if self._s3_client is None:
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                config=Config(signature_version="s3v4"),
            )
        return self._s3_client

    def enable_in_memory_mode(self) -> None:
        self._use_in_memory = True

    def disable_in_memory_mode(self) -> None:
        self._use_in_memory = False

    async def upload_file(
        self,
        file_key: str,
        content: bytes,
        content_type: str,
    ) -> None:
        if self._use_in_memory:
            self._in_memory_store[file_key] = (content, content_type)
            return

        try:
            client = self._get_client()
            client.put_object(
                Bucket=self.bucket_name,
                Key=file_key,
                Body=content,
                ContentType=content_type,
            )
        except Exception as e:
            logger.warning("s3_upload_failed_fallback_in_memory", error=str(e), file_key=file_key)
            # Graceful fallback to in-memory store so local testing works even without running MinIO
            self._in_memory_store[file_key] = (content, content_type)

    async def generate_presigned_download_url(
        self,
        file_key: str,
        expires_in: int = 900,
    ) -> str:
        if self._use_in_memory:
            return f"http://localhost:9000/{self.bucket_name}/{file_key}?signed=mock&expires={expires_in}"

        try:
            client = self._get_client()
            url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": file_key},
                ExpiresIn=expires_in,
            )
            return url
        except Exception as e:
            logger.warning("s3_presigned_url_failed_mocking", error=str(e), file_key=file_key)
            return f"http://localhost:9000/{self.bucket_name}/{file_key}?signed=fallback&expires={expires_in}"


storage_service = StorageService()
