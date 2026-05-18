"""
AgriMesh V4.0 — Security & Privacy (§21)
Argon2id password hashing, rate limiting, consent management.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections import defaultdict

from loguru import logger

# ─── Password Hashing ─────────────────────────────────────────────────

_password_hasher = None


def _get_password_hasher():
    global _password_hasher
    if _password_hasher is None:
        try:
            from argon2 import PasswordHasher
            _password_hasher = PasswordHasher()
        except Exception as exc:
            logger.warning(f"argon2-cffi unavailable, using PBKDF2 fallback: {exc}")
            _password_hasher = False
    return _password_hasher

def hash_password(password: str) -> str:
    """Hash passwords with Argon2id when available, PBKDF2 as a compatibility fallback."""
    hasher = _get_password_hasher()
    if hasher:
        return hasher.hash(password)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        b"agrimesh_salt_v4",
        310000,
        dklen=128,
    ).hex()
    return f"pbkdf2_sha256$310000${digest}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored hash."""
    if hashed.startswith("$argon2"):
        hasher = _get_password_hasher()
        if not hasher:
            return False
        try:
            return hasher.verify(hashed, password)
        except Exception:
            return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        b"agrimesh_salt_v4",
        310000,
        dklen=128,
    ).hex()
    legacy_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        b"agrimesh_salt_v4",
        65536,
        dklen=128,
    ).hex()
    return hmac.compare_digest(f"pbkdf2_sha256$310000${digest}", hashed) or hmac.compare_digest(legacy_digest, hashed)


# ─── Rate Limiter ─────────────────────────────────────────────────────

class RateLimiter:
    """
    Simple in-memory rate limiter (Redis-backed in production).
    Tracks requests per window per key.
    """

    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        """Check if request is allowed under rate limit."""
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean expired entries
        self._buckets[key] = [t for t in self._buckets[key] if t > cutoff]

        if len(self._buckets[key]) >= self.max_requests:
            return False

        self._buckets[key].append(now)
        return True

    def remaining(self, key: str) -> int:
        """Number of remaining requests in current window."""
        now = time.time()
        cutoff = now - self.window_seconds
        self._buckets[key] = [t for t in self._buckets[key] if t > cutoff]
        return max(0, self.max_requests - len(self._buckets[key]))

    def reset(self, key: str):
        """Reset rate limit for a key."""
        self._buckets.pop(key, None)


# ─── Singletons ────────────────────────────────────────────────────────

_auth_limiter = RateLimiter(max_requests=10, window_seconds=60)   # Login attempts
_api_limiter = RateLimiter(max_requests=60, window_seconds=60)    # API calls
_agent_limiter = RateLimiter(max_requests=20, window_seconds=60)  # Agent queries


def get_auth_limiter() -> RateLimiter:
    return _auth_limiter

def get_api_limiter() -> RateLimiter:
    return _api_limiter

def get_agent_limiter() -> RateLimiter:
    return _agent_limiter


# ─── Dashboard Share Tokens ───────────────────────────────────────────

def _token_secret() -> bytes:
    """HMAC key for short-lived farmer dashboard share tokens.

    Falls back to a per-process random key when no API key is configured —
    tokens minted in that case won't survive a restart, which is fine for
    local dev; production deploys must set AGRIMESH_API_KEY.
    """
    from app.config import settings

    raw = (settings.api_key or "").encode("utf-8")
    if raw:
        return raw
    global _ephemeral_token_secret
    try:
        return _ephemeral_token_secret  # type: ignore[name-defined]
    except NameError:
        import secrets as _secrets

        _ephemeral_token_secret = _secrets.token_bytes(32)
        logger.warning("AGRIMESH_API_KEY unset — dashboard tokens use ephemeral secret")
        return _ephemeral_token_secret


def _b64u_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64u_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def issue_dashboard_token(farmer_id: str, ttl_seconds: int = 24 * 3600) -> str:
    """Mint a signed token that grants read access to one farmer's dashboard."""
    payload = {"fid": farmer_id, "exp": int(time.time()) + int(ttl_seconds)}
    body = _b64u_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(_token_secret(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64u_encode(sig)}"


def verify_dashboard_token(token: str) -> str | None:
    """Return the farmer_id encoded in `token`, or None if invalid/expired."""
    try:
        body, sig_b64 = token.split(".", 1)
        body_bytes = _b64u_decode(body)
        sig = _b64u_decode(sig_b64)
        if _b64u_encode(body_bytes) != body or _b64u_encode(sig) != sig_b64:
            return None
        expected = hmac.new(_token_secret(), body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(body_bytes)
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        fid = payload.get("fid")
        return fid if isinstance(fid, str) and fid else None
    except Exception:
        return None


# ─── Consent & Privacy ────────────────────────────────────────────────

class PrivacyManager:
    """
    §21: Location privacy, identity protection, consent management.
    Coordinates and names NEVER leave Scale 1 without consent.
    """

    @staticmethod
    def anonymize_farmer(farmer_id: str, village: str, district: str) -> dict:
        """Strip PII for community-level sharing (Scale 2+)."""
        return {
            "farmer_ref": f"FARMER_{hashlib.sha256(farmer_id.encode()).hexdigest()[:8]}",
            "village": village if village else "unknown",
            "district": district,
            # Coordinates stripped
        }

    @staticmethod
    def mask_coordinates(lat: float | None, lng: float | None, scale: int) -> dict | None:
        """
        Scale 1: exact coordinates (only to field owner)
        Scale 2: rounded to 0.01° (~1km)
        Scale 3+: district-level only (no coordinates)
        """
        if lat is None or lng is None:
            return None
        if scale == 1:
            return {"lat": lat, "lng": lng}
        if scale == 2:
            return {"lat": round(lat, 2), "lng": round(lng, 2)}
        return None  # Scale 3+: no coordinates

    @staticmethod
    def sanitize_for_sharing(data: dict, scale: int = 2) -> dict:
        """Remove PII from data before sharing at given scale."""
        sensitive_keys = ["name", "phone", "hashed_password", "exact_location"]
        result = {k: v for k, v in data.items() if k not in sensitive_keys}
        if "lat" in data and "lng" in data:
            coords = PrivacyManager.mask_coordinates(data.get("lat"), data.get("lng"), scale)
            if coords:
                result.update(coords)
        return result
