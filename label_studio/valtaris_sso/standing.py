"""Valtaris bridge — worker STANDING read (Studio → Portal).

The Portal is the single source of truth for identity, tier, validator standing,
and account status. Before Studio serves/assigns a task we read the worker's
standing and gate by ``accountStatus`` + per-track tier (design §3.1, §8.1). This
is the belt-and-suspenders companion to the Portal's set-active push (§3.4, §7):
even if a push was missed, a revoked/suspended worker is not served new tasks.

Contract (Portal, built + proven):
    GET {PORTAL_BASE_URL}/api/integration/standing?userId=<Portal User.id>
    Authorization: Bearer <service-account key>   (scope: standing:read)
    200 -> { userId, accountStatus, qualifications:[{trackSlug,trackName,tier,status}],
             validatorCapabilities:[{trackSlug,status}] }
    400 missing userId · 401 missing/invalid/revoked key · 403 wrong scope · 404 unknown worker

Config (env; overridable in Django settings via same names):
    VALTARIS_PORTAL_BASE_URL          default http://localhost:3011
    VALTARIS_SERVICE_ACCOUNT_KEY      the vlt_… key (scope standing:read)
    VALTARIS_STANDING_CACHE_TTL       seconds a fetched standing is fresh (default 60)
    VALTARIS_STANDING_STALE_MAX       seconds a cached standing may be reused during a
                                      Portal OUTAGE before we give up (default 900)
    VALTARIS_STANDING_FAILURE_MODE    'closed' (default) | 'open' — what to do when
                                      standing cannot be determined at all.

SECURITY NOTE: gating access is a compliance control (it enforces sanctions /
fraud / suspension decisions made on the Portal). It therefore fails CLOSED by
default: if we cannot determine standing and have no usable cached value, we
DENY assignment. Setting VALTARIS_STANDING_FAILURE_MODE=open trades that safety
for availability during a Portal outage — a sanctioned worker could then be
served tasks until the Portal recovers. Only do so deliberately.
"""

import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

# Tier ordering (from the Portal's constants.ts).
TIER_ORDER = {
    "T0_trainee": 0,
    "T1_associate": 1,
    "T2_skilled": 2,
    "T3_specialist": 3,
}
# T2+ may validate (Portal VALIDATOR_MIN_TIER).
VALIDATOR_MIN_TIER = "T2_skilled"
# Only an 'active' Portal account may be assigned work.
ASSIGNABLE_ACCOUNT_STATUS = "active"


class StandingUnavailable(Exception):
    """Raised when a worker's standing cannot be determined (fail-closed)."""


def _cfg(name, default=None):
    # Prefer Django settings if present, else environment, else default.
    try:
        from django.conf import settings

        if hasattr(settings, name):
            return getattr(settings, name)
    except Exception:
        pass
    return os.environ.get(name, default)


def _portal_base():
    return (_cfg("VALTARIS_PORTAL_BASE_URL", "http://localhost:3011") or "").rstrip("/")


def _service_key():
    return _cfg("VALTARIS_SERVICE_ACCOUNT_KEY", "") or ""


def _int_cfg(name, default):
    try:
        return int(_cfg(name, default))
    except (TypeError, ValueError):
        return default


def _failure_mode():
    return (_cfg("VALTARIS_STANDING_FAILURE_MODE", "closed") or "closed").lower()


# --- tiny in-process TTL cache -------------------------------------------------
# {portal_user_id: (fetched_at_epoch, standing_dict_or_None)}
_CACHE: dict = {}


def _cache_get(portal_user_id):
    return _CACHE.get(portal_user_id)


def _cache_put(portal_user_id, standing):
    _CACHE[portal_user_id] = (time.time(), standing)


def clear_cache():
    """Test/ops helper — drop all cached standing (e.g. after a set-active push)."""
    _CACHE.clear()


# --- HTTP fetch ----------------------------------------------------------------
def _http_get_standing(portal_user_id):
    """One HTTP call. Returns (kind, payload):
    ('ok', dict) | ('not_found', None) | ('auth_error', msg) | ('unavailable', msg)
    """
    base = _portal_base()
    key = _service_key()
    if not base or not key:
        return ("auth_error", "VALTARIS_PORTAL_BASE_URL or VALTARIS_SERVICE_ACCOUNT_KEY not configured")
    url = f"{base}/api/integration/standing"
    try:
        resp = requests.get(
            url,
            params={"userId": portal_user_id},
            headers={"Authorization": f"Bearer {key}"},
            timeout=float(_cfg("VALTARIS_STANDING_TIMEOUT", 4) or 4),
        )
    except requests.RequestException as e:
        return ("unavailable", f"request error: {e}")

    if resp.status_code == 200:
        try:
            return ("ok", resp.json())
        except ValueError:
            return ("unavailable", "malformed standing JSON")
    if resp.status_code == 404:
        return ("not_found", None)
    if resp.status_code in (401, 403):
        # Configuration/permission problem — never mask as availability blip.
        return ("auth_error", f"{resp.status_code}: {resp.text[:200]}")
    return ("unavailable", f"HTTP {resp.status_code}")


def get_standing(portal_user_id, use_cache=True):
    """Return the worker's standing dict, or None if the worker is unknown (404).

    Raises StandingUnavailable when standing cannot be determined and no usable
    (fresh-or-stale) cached value exists. Uses a short freshness TTL plus a
    longer stale-on-outage window so brief Portal blips don't stall assignment.
    """
    now = time.time()
    ttl = _int_cfg("VALTARIS_STANDING_CACHE_TTL", 60)
    stale_max = _int_cfg("VALTARIS_STANDING_STALE_MAX", 900)

    cached = _cache_get(portal_user_id) if use_cache else None
    if cached and (now - cached[0]) < ttl:
        return cached[1]  # fresh (may be None if worker is known-unknown)

    kind, payload = _http_get_standing(portal_user_id)
    if kind == "ok":
        _cache_put(portal_user_id, payload)
        return payload
    if kind == "not_found":
        _cache_put(portal_user_id, None)
        return None
    if kind == "auth_error":
        # Hard config/permission failure: log loudly, do NOT serve stale.
        logger.error("Valtaris standing auth/config error for %s: %s", portal_user_id, payload)
        raise StandingUnavailable(f"standing auth/config error: {payload}")

    # kind == 'unavailable' (network/5xx/timeout): serve recent stale if we can.
    if cached and (now - cached[0]) < stale_max:
        logger.warning("Valtaris standing unavailable (%s); serving stale for %s", payload, portal_user_id)
        return cached[1]
    logger.warning("Valtaris standing unavailable for %s: %s", portal_user_id, payload)
    raise StandingUnavailable(f"standing unavailable: {payload}")


# --- pure gating logic (operates on a standing dict) ---------------------------
def tier_rank(tier):
    return TIER_ORDER.get(tier, -1)


def is_assignable(standing):
    """True iff the Portal account status permits any task assignment."""
    return bool(standing) and standing.get("accountStatus") == ASSIGNABLE_ACCOUNT_STATUS


def qualified_for(standing, track_slug, min_tier="T1_associate"):
    """True iff the worker holds an ACTIVE qualification on ``track_slug`` at or
    above ``min_tier``. (T0 trainees are excluded from live pools by default.)"""
    if not is_assignable(standing):
        return False
    need = tier_rank(min_tier)
    for q in standing.get("qualifications", []):
        if (
            q.get("trackSlug") == track_slug
            and q.get("status") == "active"
            and tier_rank(q.get("tier")) >= need
        ):
            return True
    return False


def can_validate(standing, track_slug):
    """True iff the worker has an ACTIVE validator capability on ``track_slug``.
    (Tier eligibility for validation is enforced Portal-side; we honor its
    capability verdict and require the account to be assignable.)"""
    if not is_assignable(standing):
        return False
    for v in standing.get("validatorCapabilities", []):
        if v.get("trackSlug") == track_slug and v.get("status") == "active":
            return True
    return False


def assignable_tracks(standing, min_tier="T1_associate"):
    """Set of track slugs the worker may pull annotation tasks from."""
    if not is_assignable(standing):
        return set()
    need = tier_rank(min_tier)
    return {
        q.get("trackSlug")
        for q in standing.get("qualifications", [])
        if q.get("status") == "active" and tier_rank(q.get("tier")) >= need
    }


# --- high-level decision (fetch + gate, with fail-closed policy) ---------------
def worker_can_pull(portal_user_id, track_slug, min_tier="T1_associate"):
    """(allowed: bool, reason: str) — the single call task assignment should use.

    Fails CLOSED (deny) when standing can't be determined, unless
    VALTARIS_STANDING_FAILURE_MODE=open.
    """
    try:
        standing = get_standing(portal_user_id)
    except StandingUnavailable as e:
        if _failure_mode() == "open":
            logger.warning("Standing unavailable, failing OPEN for %s: %s", portal_user_id, e)
            return (True, "standing_unavailable_fail_open")
        return (False, "standing_unavailable_fail_closed")

    if standing is None:
        return (False, "unknown_worker")
    if not is_assignable(standing):
        return (False, f"account_{standing.get('accountStatus')}")
    if not qualified_for(standing, track_slug, min_tier):
        return (False, f"not_qualified_for_{track_slug}_at_{min_tier}")
    return (True, "ok")
