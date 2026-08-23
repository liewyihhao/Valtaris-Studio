"""Register/refresh the Valtaris Portal per-annotation webhook.

    uv run python label_studio/manage.py valtaris_register_webhook
    uv run python label_studio/manage.py valtaris_register_webhook --org 1
    uv run python label_studio/manage.py valtaris_register_webhook --url https://portal/api/webhooks/label-studio

Idempotent — safe to re-run (e.g. after rotating LABEL_STUDIO_WEBHOOK_SECRET).
"""

from django.core.management.base import BaseCommand, CommandError

from valtaris_sso.bridge_config import portal_webhook_url, webhook_secret
from valtaris_sso.webhook_setup import ensure_portal_webhook


class Command(BaseCommand):
    help = "Create/refresh the Portal ANNOTATION_* webhook (org-level, secret-gated)."

    def add_arguments(self, parser):
        parser.add_argument("--org", type=int, default=None, help="Organization id (default: all).")
        parser.add_argument("--url", type=str, default=None, help="Override the Portal webhook URL.")

    def handle(self, *args, **opts):
        from organizations.models import Organization

        if not webhook_secret():
            raise CommandError("LABEL_STUDIO_WEBHOOK_SECRET is not set — refusing to create an unauthenticated webhook.")

        url = opts["url"] or portal_webhook_url()
        if opts["org"] is not None:
            orgs = Organization.objects.filter(id=opts["org"])
            if not orgs:
                raise CommandError(f"Organization {opts['org']} not found.")
        else:
            orgs = Organization.objects.all()
            if not orgs:
                raise CommandError("No organizations found.")

        for org in orgs:
            _webhook, created = ensure_portal_webhook(org, url=url)
            self.stdout.write(
                self.style.SUCCESS(
                    f"[org {org.id}] webhook {'created' if created else 'updated'} -> {url}"
                )
            )
