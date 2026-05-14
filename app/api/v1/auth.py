from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status

from app.config import settings

router = APIRouter()

_TOKEN_TTL_SECONDS = 300
_USED_JTI_CAP = 10_000
_RATE_LIMIT_CAPACITY = 6.0
_RATE_LIMIT_REFILL_PER_SECOND = _RATE_LIMIT_CAPACITY / 60.0

_used_jti: OrderedDict[str, float] = OrderedDict()
_ip_buckets: dict[str, tuple[float, float]] = {}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sweep_jti(now: float) -> None:
    expired = [jti for jti, exp_ts in _used_jti.items() if exp_ts <= now]
    for jti in expired:
        _used_jti.pop(jti, None)
    while len(_used_jti) > _USED_JTI_CAP:
        _used_jti.popitem(last=False)


def _consume_ip_token(ip: str, now: float) -> bool:
    tokens, last_seen = _ip_buckets.get(ip, (_RATE_LIMIT_CAPACITY, now))
    elapsed = max(0.0, now - last_seen)
    tokens = min(_RATE_LIMIT_CAPACITY, tokens + elapsed * _RATE_LIMIT_REFILL_PER_SECOND)
    if tokens < 1.0:
        _ip_buckets[ip] = (tokens, now)
        return False
    _ip_buckets[ip] = (tokens - 1.0, now)
    return True


def _secret_matches(provided: str) -> bool:
    if not provided:
        return False
    configured = [
        secret_value
        for secret_value in (
            settings.bot_demo_secret_current,
            settings.bot_demo_secret_previous,
        )
        if secret_value
    ]
    return bool(configured) and any(
        secrets.compare_digest(provided, secret_value) for secret_value in configured
    )


def _sign_bridge_token(identity: str, role: str, now: int, jti: str) -> str:
    header = {"alg": "HS256", "kid": "current"}
    payload = {
        "aud": "agri-pwa",
        "sub": identity,
        "role": role,
        "exp": now + _TOKEN_TTL_SECONDS,
        "jti": jti,
        "single_use": True,
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(
        settings.bot_demo_secret_current.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64url(signature)}"


@router.post("/auth/demo-session")
async def create_demo_session(
    request: Request,
    response: Response,
    role: str = Query(pattern="^(farmer|extension_worker)$"),
    identity: str = Query(min_length=1),
    x_bot_demo_secret: str | None = Header(default=None, alias="X-Bot-Demo-Secret"),
) -> dict[str, object]:
    now_float = time.time()
    _sweep_jti(now_float)

    client_ip = request.client.host if request.client else "unknown"
    if not _consume_ip_token(client_ip, now_float):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demo session rate limit exceeded",
        )

    if not _secret_matches(x_bot_demo_secret or ""):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid demo secret")

    if not settings.enable_demo_sessions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Demo sessions disabled")

    allowed_identities = (
        settings.demo_allowed_farmers
        if role == "farmer"
        else settings.demo_allowed_workers
    )
    if identity not in allowed_identities:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Identity not allowed")

    jti = str(uuid4())
    now = int(now_float)
    _used_jti[jti] = now + _TOKEN_TTL_SECONDS
    _used_jti.move_to_end(jti)
    _sweep_jti(now_float)
    bridge_token = _sign_bridge_token(identity, role, now, jti)
    response.set_cookie(
        "__Host-agri_session",
        bridge_token,
        max_age=_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )

    return {
        "bridge_token": bridge_token,
        "expires_in": _TOKEN_TTL_SECONDS,
    }
