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
