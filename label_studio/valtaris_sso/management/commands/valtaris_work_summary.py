"""Aggregate and post work-summary rows to the Portal (Phase 5 #4).

    # previous UTC day (nightly default)
    uv run python label_studio/manage.py valtaris_work_summary
    # a specific window
    uv run python label_studio/manage.py valtaris_work_summary \\
        --period-start 2026-08-20T00:00:00Z --period-end 2026-08-21T00:00:00Z
    # compute only, don't POST
    uv run python label_studio/manage.py valtaris_work_summary --dry-run

Idempotent: the Portal upserts on (userId, period, taskType, sourceSystem), so
re-running the same window overwrites rather than duplicates.
"""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from valtaris_sso.aggregation import aggregate_and_post


class Command(BaseCommand):
    help = "Aggregate annotations into work-summary rows and POST them to the Portal."

    def add_arguments(self, parser):
        parser.add_argument("--days-back", type=int, default=1, help="Aggregate the UTC day N days back (default 1).")
        parser.add_argument("--period-start", type=str, default=None, help="ISO datetime (overrides --days-back).")
        parser.add_argument("--period-end", type=str, default=None, help="ISO datetime (overrides --days-back).")
        parser.add_argument("--dry-run", action="store_true", help="Compute + print, do not POST.")

    def handle(self, *args, **opts):
        ps = pe = None
        if opts["period_start"] or opts["period_end"]:
            if not (opts["period_start"] and opts["period_end"]):
                raise CommandError("Provide both --period-start and --period-end, or neither.")
            ps = parse_datetime(opts["period_start"])
            pe = parse_datetime(opts["period_end"])
            if not ps or not pe:
                raise CommandError("Could not parse --period-start/--period-end (use ISO 8601).")
            if ps.tzinfo is None:
                ps = ps.replace(tzinfo=datetime.timezone.utc)
            if pe.tzinfo is None:
                pe = pe.replace(tzinfo=datetime.timezone.utc)

        out = aggregate_and_post(period_start=ps, period_end=pe, days_back=opts["days_back"], dry_run=opts["dry_run"])
        self.stdout.write(
            self.style.SUCCESS(
                f"{out['period_start'].isoformat()}..{out['period_end'].isoformat()} | "
                f"rows={out['rows']} stats={out['stats']} post={out['post']}"
            )
        )
