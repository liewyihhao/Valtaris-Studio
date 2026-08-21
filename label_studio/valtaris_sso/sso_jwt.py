"""Minimal HS256 JWT verify — matches the Valtaris Portal's signer
(lib/portal/jwt.ts). Dependency-free (stdlib only) so it drops into the
Label Studio fork without adding packages. Swap for PyJWT if you prefer."""

import base64
import hashlib
import hmac
import json
import time


def _b64url_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def verify_jwt(token: str, secret: str):
    """Return the claims dict if the token is valid and unexpired, else None."""
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
    except ValueError:
        return None

    signing_input = f"{head_b64}.{body_b64}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        given = _b64url_decode(sig_b64)
    except Exception:
        return None
    if not hmac.compare_digest(expected, given):
        return None

    try:
        claims = json.loads(_b64url_decode(body_b64))
    except Exception:
        return None

    if int(claims.get("exp", 0)) < int(time.time()):
        return None
    return claims
