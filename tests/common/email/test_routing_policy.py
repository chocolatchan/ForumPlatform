"""Exact-domain recipient suppression policy tests."""

from __future__ import annotations

import pytest

from src.common.email.policy import (
    find_suppressed_recipients,
    is_suppressed_recipient,
    recipient_domain,
    suppressed_domains,
)


@pytest.mark.parametrize(
    "recipient",
    [
        "user@test.dev",
        "USER@TEST.DEV",
        "user@sub.test.dev",
        "user@deep.sub.test.dev",
        "user@test.dev.",
    ],
)
def test_test_dev_and_subdomains_are_suppressed(recipient: str) -> None:
    assert is_suppressed_recipient(recipient) is True


@pytest.mark.parametrize(
    "recipient",
    [
        "user@not-test.dev",
        "user@mytest.dev",
        "user@test.devil",
        "user@test.dev.example.com",
        "user@example.com",
        "test.dev",
        "user@@test.dev",
        "@test.dev",
        "user@",
    ],
)
def test_lookalikes_and_malformed_values_are_not_suppressed(recipient: str) -> None:
    assert is_suppressed_recipient(recipient) is False


def test_multiple_suppressed_recipients_preserve_order_and_domains_are_deduplicated() -> None:
    recipients = [
        "first@test.dev",
        "allowed@not-test.dev",
        "second@sub.test.dev",
        "third@TEST.DEV",
    ]
    assert find_suppressed_recipients(recipients) == (
        "first@test.dev",
        "second@sub.test.dev",
        "third@TEST.DEV",
    )
    assert suppressed_domains(recipients) == ("sub.test.dev", "test.dev")


def test_recipient_domain_normalization() -> None:
    assert recipient_domain(" User@Example.COM. ") == "example.com"
    assert recipient_domain("not-an-email") is None
