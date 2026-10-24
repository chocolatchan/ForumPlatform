"""Shared email utilities package.

Provides the public entry points for the email adapter and message model.
"""

from .adapter import (
    EmailAdapter,
    EmailAdapterError,
    EmailSuppressedError,
    get_notification_email_adapter,
    get_user_email_adapter,
)
from .models import EmailMessage

__all__ = [
    "EmailMessage",
    "EmailAdapter",
    "EmailAdapterError",
    "EmailSuppressedError",
    "get_user_email_adapter",
    "get_notification_email_adapter",
]
