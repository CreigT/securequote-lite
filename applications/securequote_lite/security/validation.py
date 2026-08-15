"""Validation for SecureQuote Lite external input and uploads."""

import re
from dataclasses import dataclass
from pathlib import PurePath

from fastapi import UploadFile

from applications.securequote_lite.config import MAX_UPLOAD_BYTES

PHOTO_TYPES = {"image/jpeg": {".jpg", ".jpeg"}, "image/png": {".png"}, "image/webp": {".webp"}}
DOCUMENT_TYPES = {
    "application/pdf": {".pdf"},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
}
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class SecureQuoteValidationError(ValueError):
    """Safe validation failure suitable for a client response."""


@dataclass(frozen=True)
class ValidatedUpload:
    category: str
    content_type: str
    size_bytes: int


def sanitize_text(value: str) -> str:
    return CONTROL_CHARACTERS.sub("", value).strip()


async def validate_upload(upload: UploadFile | None, category: str) -> ValidatedUpload | None:
    if upload is None or not upload.filename:
        return None
    allowed = PHOTO_TYPES if category == "photo" else DOCUMENT_TYPES
    content_type = (upload.content_type or "").lower()
    suffix = PurePath(upload.filename).suffix.lower()
    if content_type not in allowed or suffix not in allowed[content_type]:
        raise SecureQuoteValidationError(f"Unsupported {category} upload type")
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    await upload.close()
    if not content:
        raise SecureQuoteValidationError(f"{category.title()} upload is empty")
    if len(content) > MAX_UPLOAD_BYTES:
        raise SecureQuoteValidationError(f"{category.title()} upload exceeds the size limit")
    return ValidatedUpload(category=category, content_type=content_type, size_bytes=len(content))
