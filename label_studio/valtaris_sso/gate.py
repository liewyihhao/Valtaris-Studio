"""Live standing gate for task assignment (Phase 5 — wires item #1 into serving).

``worker_gate(user, project)`` decides whether a Studio user may pull a task from
a project, by resolving the user to their Portal id and re-checking standing +
per-track tier (belt-and-suspenders to the set-active push). It gates ONLY
bridge-managed workers on Valtaris-configured projects — local/admin users and
un-tagged projects pass through untouched.

``install_next_task_gate()`` wraps LS's ``get_next_task`` so a denied worker is
served no task (the caller raises NotFound → "no tasks"). It is OPT-IN
(``VALTARIS_ENFORCE_STANDING_GATE``, default False) so default behavior is
unchanged, and fails OPEN on any internal error so a bridge bug can never brick
task serving (the standing check itself fails closed on an undeterminable
standing when enabled).
"""

import logging

from .bridge_config import cfg
from .projects_config import get_project_requirement
from .standing import worker_can_pull

logger = logging.getLogger(__name__)


def enforce_enabled():
    val = cfg("VALTARIS_ENFORCE_STANDING_GATE", False)
    return str(val).lower() in ("1", "true", "yes", "on")


def portal_id_for(user):
    if user is None or not getattr(user, "id", None):
        return None
    from .models import ValtarisIdentity

    ident = ValtarisIdentity.objects.filter(user_id=user.id).first()
    return ident.portal_user_id if ident else None


def worker_gate(user, project):
    """(allowed: bool, reason: str). Non-bridge users and un-gated projects pass."""
    portal_id = portal_id_for(user)
    if not portal_id:
        return (True, "not_bridge_user")
    track_slug, min_tier = get_project_requirement(project)
    if not track_slug:
        return (True, "project_ungated")
    return worker_can_pull(portal_id, track_slug, min_tier)


_original_get_next_task = None


def install_next_task_gate():
    """Patch get_next_task (and its by-name importers) with the standing gate.

    Idempotent; no-op unless VALTARIS_ENFORCE_STANDING_GATE is truthy. Patches the
    source module plus any modules that imported the name, tolerating ones not yet
    loaded.
    """
    global _original_get_next_task
    if not enforce_enabled():
        return False

    import sys

    from projects.functions import next_task as next_task_mod

    if _original_get_next_task is None:
        _original_get_next_task = next_task_mod.get_next_task
    original = _original_get_next_task

    def gated_get_next_task(user, prepared_tasks, project, *args, **kwargs):
        try:
            allowed, reason = worker_gate(user, project)
            if not allowed:
                logger.info(
                    "Valtaris standing gate DENIED user=%s project=%s: %s",
                    getattr(user, "id", "?"), getattr(project, "id", "?"), reason,
                )
                return (None, {"valtaris_gate": reason})
        except Exception:
            # Never let a bridge bug break task serving — fall through.
            logger.exception("Valtaris standing gate error; failing open")
        return original(user, prepared_tasks, project, *args, **kwargs)

    gated_get_next_task._valtaris_gated = True  # marker for idempotency

    # Patch the source module and every module that did `from ... import get_next_task`.
    targets = ["projects.functions.next_task", "projects.api", "data_manager.actions.next_task"]
    patched = []
    for mod_name in targets:
        mod = sys.modules.get(mod_name)
        if mod is None:
            continue
        current = getattr(mod, "get_next_task", None)
        if current is not None and not getattr(current, "_valtaris_gated", False):
            setattr(mod, "get_next_task", gated_get_next_task)
            patched.append(mod_name)
    logger.info("Valtaris standing gate installed on: %s", patched)
    return True
