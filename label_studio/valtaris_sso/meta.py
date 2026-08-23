"""Task-meta stamping for the Valtaris bridge (Phase 5 #3).

Every task's per-annotation webhook must carry the fields the Portal reads from
``task.meta`` (app/api/webhooks/label-studio): ``valtaris_user_id`` (the opaque
Portal User.id — the pay attribution), ``item_count`` (units of work in the
task; Portal defaults to 1), and ``is_gold`` (gold tasks route to qualification
scoring, not pay).

GUARANTEED path: stamp meta at import/assignment time (``stamp_task_meta`` /
``stamp_tasks_bulk``) so the task already carries attribution before it is ever
annotated. SAFETY NET: ``backfill_valtaris_user_id`` runs on annotation save
(see signals.py) and derives ``valtaris_user_id`` from the completing worker
(``annotation.completed_by`` -> ValtarisIdentity) when import didn't set it —
covering pooled tasks. It mutates the in-memory ``annotation.task`` and persists
it, so the enrichment is visible both to the outgoing webhook payload and to the
Portal's reconcile poll.
"""

import logging

logger = logging.getLogger(__name__)

META_KEYS = ("valtaris_user_id", "item_count", "is_gold")


def stamp_task_meta(task, valtaris_user_id=None, item_count=None, is_gold=None, save=True):
    """Set the bridge meta fields on a task (idempotent). Returns the task."""
    meta = dict(task.meta or {})
    if valtaris_user_id is not None:
        meta["valtaris_user_id"] = str(valtaris_user_id)
    if item_count is not None:
        meta["item_count"] = int(item_count)
    if is_gold is not None:
        meta["is_gold"] = bool(is_gold)
    meta.setdefault("item_count", 1)
    task.meta = meta
    if save:
        task.save(update_fields=["meta"])
    return task


def stamp_tasks_bulk(tasks, valtaris_user_id=None, item_count=None, is_gold=None):
    """Stamp a list/queryset of tasks and bulk-update their meta. Returns count."""
    updated = []
    for task in tasks:
        stamp_task_meta(task, valtaris_user_id, item_count, is_gold, save=False)
        updated.append(task)
    if updated:
        type(updated[0]).objects.bulk_update(updated, ["meta"])
    return len(updated)


def backfill_valtaris_user_id(annotation):
    """Ensure ``annotation.task.meta`` carries valtaris_user_id/item_count/is_gold.

    Best-effort; never raises to the caller (annotation save must not break).
    Returns True if the task meta was changed. Mutates the in-memory task so the
    outgoing webhook payload (which serializes ``annotation.task``) sees it.
    """
    task = getattr(annotation, "task", None)
    if task is None:
        return False
    meta = dict(task.meta or {})
    changed = False

    if not meta.get("valtaris_user_id") and getattr(annotation, "completed_by_id", None):
        from .models import ValtarisIdentity

        ident = ValtarisIdentity.objects.filter(user_id=annotation.completed_by_id).first()
        if ident:
            meta["valtaris_user_id"] = ident.portal_user_id
            changed = True

    if "item_count" not in meta:
        meta["item_count"] = 1
        changed = True

    # A ground_truth annotation marks the task as gold if not already declared.
    if getattr(annotation, "ground_truth", False) and not meta.get("is_gold"):
        meta["is_gold"] = True
        changed = True

    if changed:
        task.meta = meta
        task.save(update_fields=["meta"])
    return changed
