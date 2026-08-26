"""Provisioning: set Studio project membership from Portal standing (Phase 5 #2).

On promotion (or on demand), sync which projects a worker is a member of to what
their standing qualifies them for: a member of every gated project whose track
they hold at the required tier, disabled elsewhere. Membership is a convenience/
visibility layer; the standing gate (gate.worker_gate) is the hard enforcement at
serve time. Never creates accounts — the SSO-only Studio user must already exist
(created at first SSO login); provisioning links membership, not identity.
"""

import logging

from .projects_config import get_project_requirement
from .standing import get_standing, is_assignable, qualified_for

logger = logging.getLogger(__name__)


def user_for_portal_id(portal_user_id):
    from .models import ValtarisIdentity

    ident = ValtarisIdentity.objects.select_related("user").filter(portal_user_id=str(portal_user_id)).first()
    return ident.user if ident else None


def _gated_projects(organization):
    """Projects in the org that carry a Valtaris track requirement."""
    from projects.models import Project

    return Project.objects.filter(organization=organization, valtaris_config__isnull=False).select_related(
        "valtaris_config"
    )


def sync_project_membership(user, standing, organization=None):
    """Ensure ProjectMember rows match the worker's standing. Returns a summary.

    A worker qualified for a gated project's (track, min_tier) is an enabled
    member; otherwise their membership is disabled (not deleted, to preserve
    history). Ungated projects are left untouched.
    """
    from projects.models import ProjectMember

    org = organization or getattr(user, "active_organization", None)
    result = {"enabled": [], "disabled": []}
    if org is None:
        return result

    assignable = is_assignable(standing)
    for project in _gated_projects(org):
        track_slug, min_tier = get_project_requirement(project)
        should_be_member = assignable and qualified_for(standing, track_slug, min_tier)
        membership = ProjectMember.objects.filter(user=user, project=project).first()
        if should_be_member:
            if membership is None:
                ProjectMember.objects.create(user=user, project=project, enabled=True)
                result["enabled"].append(project.id)
            elif not membership.enabled:
                membership.enabled = True
                membership.save(update_fields=["enabled"])
                result["enabled"].append(project.id)
        else:
            if membership is not None and membership.enabled:
                membership.enabled = False
                membership.save(update_fields=["enabled"])
                result["disabled"].append(project.id)
    return result


def provision_worker(portal_user_id, organization=None):
    """Resolve the worker, read standing, and sync project membership.

    Returns {ok, reason?, membership?}. Does not create the Studio user (SSO does)
    and does not reactivate a blocked account (manual on the Portal).
    """
    user = user_for_portal_id(portal_user_id)
    if user is None:
        return {"ok": False, "reason": "no_studio_user_yet"}  # user provisions on first SSO login

    standing = get_standing(str(portal_user_id))
    if standing is None:
        return {"ok": False, "reason": "unknown_worker"}

    org = organization or getattr(user, "active_organization", None)
    membership = sync_project_membership(user, standing, organization=org)
    logger.info("Provisioned worker %s (user %s): %s", portal_user_id, user.id, membership)
    return {"ok": True, "membership": membership, "accountStatus": standing.get("accountStatus")}
