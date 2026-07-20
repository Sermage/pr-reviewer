"""Verification of GitHub webhook payloads (HMAC-SHA256)."""
from __future__ import annotations

import hashlib
import hmac


def verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """Return True iff `signature_header` matches HMAC-SHA256(secret, body).

    GitHub sends the header as ``X-Hub-Signature-256: sha256=<hex>``.
    Comparison is constant-time. An empty secret disables verification
    (useful for local mock runs) but is never the default in production.
    """
    if not secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)
