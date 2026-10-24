from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ["EmailMessage", "RecipientList"]

RecipientList = Iterable[str]


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """Normalized representation of an email to be sent.

    Attributes
    ----------
    subject: Subject line.
    body_text: Plain‑text body (required).
    sender: ``From`` address.
    recipients: Iterable of ``To`` addresses.
    body_html: Optional HTML version of the body.
    """

    subject: str
    body_text: str
    sender: str
    recipients: RecipientList
    body_html: str | None = None
