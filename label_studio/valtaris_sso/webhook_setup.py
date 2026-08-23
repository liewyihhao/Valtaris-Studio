"""Register the Portal per-annotation webhook (Phase 5 #3).

Creates/updates an ORG-level Label Studio webhook that fires
ANNOTATION_CREATED / ANNOTATION_UPDATED to the Portal's ingest endpoint with the
shared secret header. Org-level (project=None) so it applies to every project;
``send_for_all_actions=False`` + explicit actions so it fires ONLY on annotation
events (not project/task noise). Idempotent by (organization, url).

NOTE (runbook): Label Studio delivers webhooks via ``ssrf_safe_post``, which
blocks private/loopback targets by default. A Portal at localhost or an internal
address must be allowlisted for delivery to succeed outside a permissive dev
setup — see the SSRF settings in core.settings.
"""

import logging

from .bridge_config import portal_webhook_url, webhook_secret

logger = logging.getLogger(__name__)

WEBHOOK_ACTIONS = ["ANNOTATION_CREATED", "ANNOTATION_UPDATED"]
SECRET_HEADER = "X-Valtaris-Webhook-Secret"


def ensure_portal_webhook(organization, url=None, secret=None):
    """Create or update the org's Portal webhook. Returns (webhook, created)."""
    from webhooks.models import Webhook

    url = url or portal_webhook_url()
    secret = secret if secret is not None else webhook_secret()
    if not secret:
        raise ValueError("LABEL_STUDIO_WEBHOOK_SECRET is not configured")

    webhook, created = Webhook.objects.get_or_create(
        organization=organization,
        url=url,
        project=None,
        defaults={
            "send_payload": True,
            "send_for_all_actions": False,
            "headers": {SECRET_HEADER: secret},
            "is_active": True,
        },
    )
    # Keep configuration in sync on re-run (secret rotation, re-enable).
    webhook.send_payload = True
    webhook.send_for_all_actions = False
    webhook.headers = {SECRET_HEADER: secret}
    webhook.is_active = True
    webhook.save()
    webhook.set_actions(WEBHOOK_ACTIONS)
    logger.info(
        "Valtaris Portal webhook %s for org %s -> %s (actions=%s)",
        "created" if created else "updated",
        getattr(organization, "id", organization),
        url,
        WEBHOOK_ACTIONS,
    )
    return webhook, created
