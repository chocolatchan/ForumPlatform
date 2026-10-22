"""Image validation and storage utilities for Content Service."""

import io
import re
import uuid
import warnings
from pathlib import Path

from PIL import Image, ImageOps

ALLOWED_MIME_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

# 2 MB limit in bytes
MAX_FILE_SIZE_BYTES = 2097152

# Dimension and pixel-count safety limits (prevents decompression bombs)
MAX_IMAGE_WIDTH = 8192
MAX_IMAGE_HEIGHT = 8192
MAX_IMAGE_PIXELS = 16_000_000  # 16 Megapixels

# Pillow raises DecompressionBombWarning above this many pixels, and
# DecompressionBombError above twice this many. Both are treated as rejection
# conditions in validate_and_normalize_image().
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

# Server-generated safe filename pattern: 32 hex chars + one approved extension
SAFE_OBJECT_KEY_PATTERN = re.compile(r"^uploads/images/[a-f0-9]{32}\.(jpg|png|webp)$")


def sniff_image_type(header: bytes) -> str | None:
    """Detect image MIME type from binary header bytes.

    Returns MIME type string if valid JPEG, PNG, or WebP; otherwise None.
    Client-supplied filenames and MIME types are never consulted.
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


def validate_and_normalize_image(data: bytes) -> tuple[bool, str, bytes, int, int]:
    """Fully decode compressed pixel data and re-encode into clean raster bytes.

    The complete compressed bitstream is decoded with Pillow. Signatures alone
    are never accepted: corrupt/truncated IDAT, entropy-coded JPEG data, or WebP
    payloads raise during ``img.load()`` and are rejected.

    Enforces:
    1. Signature magic bytes and decoded-format agreement.
    2. Complete decompression decoding without corrupt bitstreams.
    3. Maximum dimension limits (width <= 8192, height <= 8192).
    4. Decompression bomb limits (pixels <= 16M).
    5. Byte-size limit before and after normalization.
    6. Clean re-encoding to strip EXIF, GPS tags, comments, and polyglot payloads.

    Returns:
        (is_valid, mime_type_or_error, normalized_bytes, width, height)
    """
    if len(data) < 12:
        return False, "File is too small to be a valid image", b"", 0, 0

    if len(data) > MAX_FILE_SIZE_BYTES:
        return (
            False,
            f"Image exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES} bytes",
            b"",
            0,
            0,
        )

    sniffed_mime = sniff_image_type(data[:12])
    if not sniffed_mime:
        return False, "Unsupported media type: magic bytes mismatch", b"", 0, 0

    # Structural pre-checks keep obviously truncated containers cheap to reject.
    if sniffed_mime == "image/png" and b"IEND" not in data[-12:]:
        return False, "PNG truncated or missing IEND chunk", b"", 0, 0
    if sniffed_mime == "image/jpeg" and not data.endswith(b"\xff\xd9"):
        return False, "JPEG truncated or missing EOI marker", b"", 0, 0

    expected_formats = {
        "image/jpeg": ("JPEG",),
        "image/png": ("PNG",),
        "image/webp": ("WEBP",),
    }

    try:
        with warnings.catch_warnings():
            # A decompression bomb warning is a rejection, not a log line.
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            bio = io.BytesIO(data)
            with Image.open(bio) as img:
                fmt = (img.format or "").upper()
                if fmt not in expected_formats.get(sniffed_mime, ()):
                    return (
                        False,
                        f"MIME signature {sniffed_mime} does not match decoded format {fmt}",
                        b"",
                        0,
                        0,
                    )

                declared_width, declared_height = img.size
                if declared_width <= 0 or declared_height <= 0:
                    return False, "Invalid image dimensions", b"", 0, 0
                if declared_width > MAX_IMAGE_WIDTH or declared_height > MAX_IMAGE_HEIGHT:
                    return (
                        False,
                        f"Image dimensions ({declared_width}x{declared_height}) exceed "
                        f"maximum allowed {MAX_IMAGE_WIDTH}x{MAX_IMAGE_HEIGHT}",
                        b"",
                        0,
                        0,
                    )
                if declared_width * declared_height > MAX_IMAGE_PIXELS:
                    return (
                        False,
                        f"Image pixel count ({declared_width * declared_height}) exceeds "
                        f"maximum limit of {MAX_IMAGE_PIXELS}",
                        b"",
                        0,
                        0,
                    )

                # Full compressed bitstream decode. This is the operation that
                # catches plausible headers with invalid pixel data.
                img.load()

                # Apply EXIF orientation before stripping metadata so the
                # normalized image still looks the way the author saw it.
                transposed = ImageOps.exif_transpose(img)
                width, height = transposed.size
                if width * height > MAX_IMAGE_PIXELS:
                    return (
                        False,
                        f"Image pixel count ({width * height}) exceeds maximum limit "
                        f"of {MAX_IMAGE_PIXELS}",
                        b"",
                        0,
                        0,
                    )

                out_buf = io.BytesIO()
                if sniffed_mime == "image/jpeg":
                    clean_img = transposed if transposed.mode == "RGB" else transposed.convert("RGB")
                    clean_img.save(out_buf, format="JPEG", quality=90, optimize=True)
                elif sniffed_mime == "image/png":
                    if transposed.mode in ("RGBA", "LA"):
                        clean_img = transposed if transposed.mode == "RGBA" else transposed.convert("RGBA")
                    else:
                        clean_img = transposed if transposed.mode == "RGB" else transposed.convert("RGB")
                    clean_img.save(out_buf, format="PNG", optimize=True)
                elif sniffed_mime == "image/webp":
                    clean_img = (
                        transposed.convert("RGBA")
                        if "A" in transposed.mode
                        else transposed.convert("RGB")
                    )
                    clean_img.save(out_buf, format="WEBP", quality=90)
                else:  # pragma: no cover - guarded by expected_formats above
                    return False, "Unsupported output format", b"", 0, 0

                normalized_bytes = out_buf.getvalue()

        if not normalized_bytes:
            return False, "Image decoding produced an empty payload", b"", 0, 0
        # Re-encoding a highly compressible input can grow the output. The
        # database check constraint and storage contract both cap at 2 MiB.
        if len(normalized_bytes) > MAX_FILE_SIZE_BYTES:
            return (
                False,
                f"Normalized image exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES} bytes",
                b"",
                0,
                0,
            )

        return True, sniffed_mime, normalized_bytes, width, height

    except Image.DecompressionBombError as dbe:
        return False, f"Decompression bomb rejected: {dbe}", b"", 0, 0
    except Image.DecompressionBombWarning as dbw:
        return False, f"Decompression bomb rejected: {dbw}", b"", 0, 0
    except Exception as exc:
        return False, f"Image decoding failed: {exc}", b"", 0, 0


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
