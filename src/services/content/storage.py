"""Image validation and storage utilities for Content Service."""

import uuid
from pathlib import Path

ALLOWED_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

# 2 MB limit in bytes
MAX_FILE_SIZE_BYTES = 2097152


def sniff_image_type(header: bytes) -> str | None:
    """Detect image MIME type from binary header bytes.
    
    Returns MIME type string if valid JPEG, PNG, or WebP; otherwise None.
    """
    if len(header) < 12:
        return None

    # JPEG: starts with \xff\xd8\xff
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    # PNG: starts with \x89PNG\r\n\x1a\n
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    # WebP: starts with RIFF, then 4 bytes file size, then WEBP
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"

    return None


def ensure_storage_dir(base_dir: str) -> Path:
    """Ensure the target upload directory exists."""
    path = Path(base_dir) / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def generate_object_key(attachment_id: uuid.UUID, mime_type: str) -> str:
    """Generate relative object key for storage."""
    ext = ALLOWED_MIME_TYPES.get(mime_type, ".bin")
    return f"uploads/images/{attachment_id.hex}{ext}"


def get_absolute_file_path(base_dir: str, object_key: str) -> Path:
    """Resolve object key to an absolute path safely without traversal."""
    filename = Path(object_key).name
    return Path(base_dir) / "images" / filename
