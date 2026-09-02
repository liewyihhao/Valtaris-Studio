"""Shared config reads for the Valtaris bridge (env, overridable via settings)."""

import os


def cfg(name, default=None):
    try:
        from django.conf import settings

        if hasattr(settings, name):
            return getattr(settings, name)
    except Exception:
        pass
    return os.environ.get(name, default)


def portal_base():
    return (cfg("VALTARIS_PORTAL_BASE_URL", "http://localhost:3011") or "").rstrip("/")


def webhook_secret():
    return cfg("LABEL_STUDIO_WEBHOOK_SECRET", "") or ""


def portal_webhook_url():
    return f"{portal_base()}/api/webhooks/label-studio"


def service_account_key():
    return cfg("VALTARIS_SERVICE_ACCOUNT_KEY", "") or ""


def portal_work_summary_url():
    return f"{portal_base()}/api/integration/work-summary"


def portal_review_url():
    return f"{portal_base()}/api/integration/review"


def portal_login_url():
    """Where to send anyone who tries to reach the Studio login/signup pages."""
    return cfg("VALTARIS_PORTAL_LOGIN_URL", "") or f"{portal_base()}/login"


def portal_only_login_enabled():
    """When on, direct Studio login/signup is disabled — access is SSO-only.
    Default ON for the Valtaris fork; set VALTARIS_PORTAL_ONLY_LOGIN=false to allow
    native Studio login (e.g. for local admin/debug)."""
    return str(cfg("VALTARIS_PORTAL_ONLY_LOGIN", "true")).lower() in ("1", "true", "yes", "on")
