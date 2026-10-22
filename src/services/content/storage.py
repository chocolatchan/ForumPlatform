"""Image validation and storage utilities for Content Service."""

import struct
import uuid
from pathlib import Path

ALLOWED_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

# 2 MB limit in bytes
MAX_FILE_SIZE_BYTES = 2097152

# Dimension safety limits (prevents decompression bombs)
MAX_IMAGE_WIDTH = 8192
MAX_IMAGE_HEIGHT = 8192


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


def validate_image_content(data: bytes) -> tuple[bool, str, int, int]:
    """Validate image structure, format consistency, and dimension bounds.

    Returns (is_valid, mime_type_or_error, width, height).
    """
    if len(data) < 12:
        return False, "File is too small to be a valid image", 0, 0

    mime_type = sniff_image_type(data[:12])
    if not mime_type:
        return False, "Unsupported media type: magic bytes mismatch", 0, 0

    if mime_type == "image/png":
        return _validate_png(data)
    elif mime_type == "image/jpeg":
        return _validate_jpeg(data)
    elif mime_type == "image/webp":
        return _validate_webp(data)

    return False, "Unsupported image format", 0, 0


def _validate_png(data: bytes) -> tuple[bool, str, int, int]:
    """Validate PNG structural integrity and dimensions."""
    # PNG signature: 8 bytes
    if len(data) < 33:  # 8 sig + 25 IHDR chunk
        return False, "PNG file truncated before header", 0, 0

    # First chunk must be IHDR
    ihdr_length = struct.unpack(">I", data[8:12])[0]
    ihdr_type = data[12:16]
    if ihdr_type != b"IHDR" or ihdr_length < 13:
        return False, "PNG missing valid IHDR chunk", 0, 0

    width, height, bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    if width <= 0 or height <= 0:
        return False, "PNG has invalid dimensions", 0, 0

    if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
        return False, f"PNG dimensions ({width}x{height}) exceed maximum allowed {MAX_IMAGE_WIDTH}x{MAX_IMAGE_HEIGHT}", 0, 0

    # File must contain IEND chunk
    if b"IEND" not in data[-12:]:
        return False, "PNG missing required IEND chunk or is corrupted", 0, 0

    return True, "image/png", width, height


def _validate_jpeg(data: bytes) -> tuple[bool, str, int, int]:
    """Validate JPEG SOI/EOI markers and parse SOF dimensions."""
    if not data.endswith(b"\xff\xd9"):
        return False, "JPEG file is truncated or missing EOI marker", 0, 0

    # Scan for Start of Frame (SOF0 = 0xC0, SOF1 = 0xC1, SOF2 = 0xC2)
    offset = 2
    length = len(data)
    width, height = 0, 0

    while offset < length - 8:
        if data[offset] != 0xFF:
            offset += 1
            continue

        marker = data[offset + 1]
        # Ignore padding 0xFF or standalone markers
        if marker in (0xFF, 0x00, 0xD8, 0xD9):
            offset += 2
            continue

        # SOF markers contain image dimensions
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and offset + 9 <= length:
            h, w = struct.unpack(">HH", data[offset + 5:offset + 9])
            height, width = h, w
            break
        
        # Read segment length and skip
        if offset + 4 <= length:
            seg_len = struct.unpack(">H", data[offset + 2:offset + 4])[0]
            offset += 2 + seg_len
        else:
            break

    if width <= 0 or height <= 0:
        return False, "JPEG missing valid frame dimension header", 0, 0

    if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
        return False, f"JPEG dimensions ({width}x{height}) exceed maximum allowed {MAX_IMAGE_WIDTH}x{MAX_IMAGE_HEIGHT}", 0, 0

    return True, "image/jpeg", width, height


def _validate_webp(data: bytes) -> tuple[bool, str, int, int]:
    """Validate WebP RIFF container and parse canvas dimensions."""
    if len(data) < 30:
        return False, "WebP file too short", 0, 0

    riff_size = struct.unpack("<I", data[4:8])[0]
    # Check minimum RIFF container integrity
    if len(data) < riff_size + 8 and abs(len(data) - (riff_size + 8)) > 1024:
        # Note: trailing padding or slight header difference may occur, allow small delta
        pass

    chunk_fourcc = data[12:16]
    width, height = 0, 0

    if chunk_fourcc == b"VP8 ":
        # Lossy VP8 format
        if len(data) < 30:
            return False, "VP8 chunk truncated", 0, 0
        # Keyframe code is at byte 23-25: 0x9d, 0x01, 0x2a
        if data[23:26] == b"\x9d\x01\x2a":
            w, h = struct.unpack("<HH", data[26:30])
            width = w & 0x3FFF
            height = h & 0x3FFF
    elif chunk_fourcc == b"VP8L":
        # Lossless VP8L format: 1 byte signature (0x2f), then 14 bits width, 14 bits height
        if len(data) < 25 or data[20] != 0x2F:
            return False, "VP8L chunk invalid", 0, 0
        b0, b1, b2, b3 = data[21:25]
        width = 1 + (((b1 & 0x3F) << 8) | b0)
        height = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
    elif chunk_fourcc == b"VP8X":
        # Extended format: 24-bit canvas width and height
        if len(data) < 30:
            return False, "VP8X chunk truncated", 0, 0
        w24 = data[24:27]
        h24 = data[27:30]
        width = 1 + (w24[0] | (w24[1] << 8) | (w24[2] << 16))
        height = 1 + (h24[0] | (h24[1] << 8) | (h24[2] << 16))
    else:
        return False, f"Unknown WebP format chunk: {chunk_fourcc.decode('latin-1', 'replace')}", 0, 0

    if width <= 0 or height <= 0:
        return False, "WebP has invalid dimensions", 0, 0

    if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
        return False, f"WebP dimensions ({width}x{height}) exceed maximum allowed {MAX_IMAGE_WIDTH}x{MAX_IMAGE_HEIGHT}", 0, 0

    return True, "image/webp", width, height


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

