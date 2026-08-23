"""Nightly work-summary aggregation (Studio -> Portal, Phase 5 #4).

Aggregates each worker's completed units per task type per period and posts them
to the Portal's reporting mirror:

    POST {PORTAL}/api/integration/work-summary   (Bearer key, scope worksummary:write)
    { "summaries": [ {userId, periodStart, periodEnd, taskType,
                       unitsCompleted, unitsApproved, unitsRejected, avgQualityScore} ] }

RECONCILIATION RULE (design §4): work-summary is a REPORTING MIRROR, never a
second pay source. It is computed from the SAME annotation events the webhook
fires on, so it agrees with the payout-derived count BY CONSTRUCTION:
  * Count non-gold, non-cancelled annotations created in the period, summing
    task.meta.item_count. Gold routes to qualification scoring (not pay) and is
    excluded; cancelled annotations are skips, not work.
  * Attribution keys on task.meta.valtaris_user_id (set at import or backfilled
    from completed_by). Un-attributable annotations are counted + logged, never
    guessed.
Reporting policy (confirmed): COMPLETED-ONLY — Studio makes no pay-approval
claim, so unitsApproved = unitsCompleted, unitsRejected = 0; the Portal's payout
ledger stays the pay authority. avgQualityScore is null unless a gold/consensus
signal is wired.
"""

import datetime
import logging

import requests

from .bridge_config import cfg, portal_work_summary_url, service_account_key

logger = logging.getLogger(__name__)

MAX_ROWS_PER_CALL = 1000


def task_type_for(project):
    """Stable task-type tag for a project. Prefers the Valtaris track tag set by
    provisioning (item #2); falls back to the project title, then id."""
    if project is None:
        return "unknown"
    meta = getattr(project, "meta", None) or {}
    track = meta.get("valtaris_track") if isinstance(meta, dict) else None
    return track or (getattr(project, "title", None) or f"project-{getattr(project, 'id', '?')}")


def _period_defaults(days_back=1):
    """Default period = a full UTC day, `days_back` days before today."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
    end_date = today - datetime.timedelta(days=days_back - 1)
    start_date = end_date - datetime.timedelta(days=1)
    start = datetime.datetime.combine(start_date, datetime.time.min, tzinfo=datetime.timezone.utc)
    end = datetime.datetime.combine(end_date, datetime.time.min, tzinfo=datetime.timezone.utc)
    return start, end


def compute_summaries(period_start, period_end):
    """Aggregate annotations in [period_start, period_end) into work-summary rows.

    Returns (rows, stats) where rows is a list of dicts ready to POST and stats
    reports {annotations, counted, skipped_gold, skipped_cancelled, unattributed}.
    """
    from tasks.models import Annotation

    qs = (
        Annotation.objects.filter(created_at__gte=period_start, created_at__lt=period_end)
        .select_related("task", "project")
    )

    # key: (valtaris_user_id, task_type) -> units
    buckets: dict = {}
    stats = {"annotations": 0, "counted": 0, "skipped_gold": 0, "skipped_cancelled": 0, "unattributed": 0}

    for ann in qs.iterator():
        stats["annotations"] += 1
        if getattr(ann, "was_cancelled", False):
            stats["skipped_cancelled"] += 1
            continue
        task = ann.task
        meta = (getattr(task, "meta", None) or {}) if task else {}
        is_gold = bool(meta.get("is_gold")) or bool(getattr(ann, "ground_truth", False))
        if is_gold:
            stats["skipped_gold"] += 1
            continue
        valtaris_user_id = meta.get("valtaris_user_id")
        if not valtaris_user_id:
            stats["unattributed"] += 1
            continue
        project = ann.project or (task.project if task else None)
        task_type = task_type_for(project)
        try:
            units = int(meta.get("item_count", 1))
        except (TypeError, ValueError):
            units = 1
        buckets[(valtaris_user_id, task_type)] = buckets.get((valtaris_user_id, task_type), 0) + units
        stats["counted"] += 1

    ps = period_start.isoformat()
    pe = period_end.isoformat()
    rows = [
        {
            "userId": uid,
            "periodStart": ps,
            "periodEnd": pe,
            "taskType": task_type,
            "unitsCompleted": units,
            "unitsApproved": units,  # completed-only policy; Portal ledger is pay authority
            "unitsRejected": 0,
            "avgQualityScore": None,
        }
        for (uid, task_type), units in sorted(buckets.items())
    ]
    return rows, stats


def post_summaries(rows, dry_run=False):
    """POST rows to the Portal in batches of <=1000. Returns a result dict."""
    result = {"batches": 0, "written": 0, "unknown_user_ids": [], "errors": []}
    if not rows:
        return result
    if dry_run:
        result["dry_run"] = True
        result["would_post"] = len(rows)
        return result

    url = portal_work_summary_url()
    key = service_account_key()
    if not key:
        raise ValueError("VALTARIS_SERVICE_ACCOUNT_KEY is not configured (scope worksummary:write)")

    for i in range(0, len(rows), MAX_ROWS_PER_CALL):
        batch = rows[i : i + MAX_ROWS_PER_CALL]
        result["batches"] += 1
        try:
            resp = requests.post(
                url,
                json={"summaries": batch},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                timeout=float(cfg("VALTARIS_SUMMARY_TIMEOUT", 30) or 30),
            )
        except requests.RequestException as e:
            result["errors"].append(f"batch {i // MAX_ROWS_PER_CALL}: request error: {e}")
            continue

        if resp.status_code == 200:
            try:
                result["written"] += int(resp.json().get("written", 0))
            except ValueError:
                pass
        elif resp.status_code == 422:
            # Unknown userIds -> nothing written for this batch; surface for ops.
            try:
                result["unknown_user_ids"].extend(resp.json().get("unknownUserIds", []))
            except ValueError:
                pass
            logger.warning("work-summary batch had unknown userIds: %s", result["unknown_user_ids"])
        else:
            msg = f"batch {i // MAX_ROWS_PER_CALL}: HTTP {resp.status_code}: {resp.text[:200]}"
            result["errors"].append(msg)
            logger.error("work-summary %s", msg)

    return result


def aggregate_and_post(period_start=None, period_end=None, days_back=1, dry_run=False):
    if period_start is None or period_end is None:
        period_start, period_end = _period_defaults(days_back)
    rows, stats = compute_summaries(period_start, period_end)
    post_result = post_summaries(rows, dry_run=dry_run)
    logger.info(
        "work-summary %s..%s: %s rows, stats=%s, post=%s",
        period_start.isoformat(), period_end.isoformat(), len(rows), stats, post_result,
    )
    return {"period_start": period_start, "period_end": period_end, "rows": len(rows), "stats": stats, "post": post_result}
