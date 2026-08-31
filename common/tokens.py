"""Device MQTT token (JWT) generation.

The real app.divoom-gz.com InitV2 reply returns the DeviceToken as a JWT of the
form  base64url(header).base64url(payload).base64url(HMAC-SHA256(secret, ...))
with header {"alg":"HS256","typ":"JWT"} and payload {"username":"<mac>"}. The
firmware uses this token as its MQTT password AND appears to validate its JWT
structure for provisioning — a plain string makes it re-run InitV2 forever.

We sign with our own local secret (the device cannot verify Divoom's signature
against a local server anyway); what matters is a well-formed JWT carrying the
right username. The result is deterministic for a given (username, secret) so
the bootstrap (DeviceToken) and the broker password stay in sync.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def device_jwt(username: str, secret: str) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({"username": username}, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"
