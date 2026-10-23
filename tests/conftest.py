"""Shared deterministic environment for the isolated test suite."""

import os

TEST_INTERNAL_SERVICE_SECRET = "test-internal-service-secret-key-at-least-32-chars"

os.environ["ENVIRONMENT"] = "test"
os.environ["INTERNAL_SERVICE_SECRET"] = TEST_INTERNAL_SERVICE_SECRET
os.environ["INTERNAL_SERVICE_AUTH_BYPASS"] = "false"
