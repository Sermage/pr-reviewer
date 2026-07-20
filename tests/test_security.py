import hashlib
import hmac

from app.security import verify_signature


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature():
    body = b'{"action":"opened"}'
    secret = "s3cr3t"
    assert verify_signature(body, _sign(body, secret), secret) is True


def test_tampered_body_rejected():
    secret = "s3cr3t"
    sig = _sign(b"original", secret)
    assert verify_signature(b"tampered", sig, secret) is False


def test_missing_header_rejected():
    assert verify_signature(b"x", None, "s3cr3t") is False


def test_empty_secret_disables_check():
    # Local/mock mode: no secret means we don't enforce.
    assert verify_signature(b"x", None, "") is True
