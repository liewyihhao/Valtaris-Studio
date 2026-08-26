"""Provision a worker's Studio project membership from Portal standing.

    uv run python label_studio/manage.py valtaris_provision_worker --portal-id <User.id>
"""

from django.core.management.base import BaseCommand, CommandError

from valtaris_sso.provisioning import provision_worker


class Command(BaseCommand):
    help = "Sync a worker's Studio project membership from their Portal standing."

    def add_arguments(self, parser):
        parser.add_argument("--portal-id", required=True, help="The opaque Portal User.id.")

    def handle(self, *args, **opts):
        result = provision_worker(opts["portal_id"])
        if not result.get("ok"):
            raise CommandError(f"Not provisioned: {result.get('reason')}")
        self.stdout.write(self.style.SUCCESS(f"Provisioned {opts['portal_id']}: {result}"))
