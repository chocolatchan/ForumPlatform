"""Environment-aware email routing and recipient-domain policy.

The shared adapter is the single transport boundary for application-generated
email. This module keeps the policy decisions small, deterministic, and free of
SMTP or application-settings imports so they can be unit tested directly.

Policy summary
--------------
* ``development`` and ``test`` must use the Mailpit catcher only. Every
  application recipient is accepted because the transport itself is pinned to
  Mailpit by configuration validation.
* ``production`` may use an explicitly configured external SMTP provider, but
  addresses on ``test.dev`` or any of its subdomains are suppressed before the
  transport is invoked.
* Domain matching is exact after a single trailing dot is removed. Substring
  matching is never used, so ``not-test.dev`` is not suppressed.
* ``staging`` remains an accepted, explicitly selected environment for the
  repository's existing staging deployment. It is not development/test and does
  not use the production suppression rule.
"""

from __future__ import annotations

from collections.abc import Iterable

ALLOWED_EMAIL_ENVIRONMENTS: frozenset[str] = frozenset(
    {"development", "test", "staging", "production"}
)
DEVELOPMENT_ENVIRONMENTS: frozenset[str] = frozenset({"development", "test"})
PRODUCTION_ENVIRONMENTS: frozenset[str] = frozenset({"production"})
SUPPRESSED_EMAIL_DOMAINS: frozenset[str] = frozenset({"test.dev"})


def normalize_environment(environment: str) -> str:
    """Return a trimmed, lower-case environment value."""
    return environment.strip().lower()


def recipient_domain(recipient: str) -> str | None:
    """Extract a normalized domain from a bare email address.

    Returns ``None`` when the value does not contain exactly one ``@`` with a
    non-empty local part and domain. Display-name parsing is intentionally not
    performed here: callers pass the application's validated bare addresses.
    """

    normalized = recipient.strip().lower()
    if normalized.count("@") != 1:
        return None
    local_part, domain = normalized.rsplit("@", 1)
    domain = domain.rstrip(".")
    if not local_part or not domain:
        return None
    return domain


def is_suppressed_recipient(recipient: str) -> bool:
    """Return whether a recipient belongs to a suppressed production domain.

    Matching is by exact domain or dot-delimited subdomain. A domain such as
    ``not-test.dev`` or ``test.dev.example.com`` is not suppressed.
    """

    domain = recipient_domain(recipient)
    if domain is None:
        return False
    return any(
        domain == suppressed or domain.endswith(f".{suppressed}")
        for suppressed in SUPPRESSED_EMAIL_DOMAINS
    )


def find_suppressed_recipients(recipients: Iterable[str]) -> tuple[str, ...]:
    """Return all suppressed recipients while preserving their input order."""
    return tuple(recipient for recipient in recipients if is_suppressed_recipient(recipient))


def suppressed_domains(recipients: Iterable[str]) -> tuple[str, ...]:
    """Return the distinct normalized suppressed domains in sorted order."""
    domains = {
        domain
        for recipient in recipients
        if (domain := recipient_domain(recipient)) is not None
        and is_suppressed_recipient(recipient)
    }
    return tuple(sorted(domains))
