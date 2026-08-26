from django.conf import settings
from django.db import models


class ValtarisIdentity(models.Model):
    """Maps a Studio user to the opaque Valtaris Portal ``User.id`` (a cuid).

    The bridge invariant: identity is matched ONLY on this opaque id, never on
    email/name. Email is used solely as the required field to create the local
    SSO-only Studio user record on first login; all lookups for access control
    (set-active) and work attribution key on ``portal_user_id``.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="valtaris_identity",
    )
    portal_user_id = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "valtaris_sso"
        verbose_name = "Valtaris identity"
        verbose_name_plural = "Valtaris identities"

    def __str__(self):
        return f"{self.portal_user_id} -> user {self.user_id}"


class ValtarisProjectConfig(models.Model):
    """Per-project Valtaris gating requirement (Studio Project has no meta field).

    Tags a Studio project with the Portal track it serves and the minimum tier a
    worker must hold on that track to pull tasks. Consumed by the standing gate
    (gate.worker_gate) and by work-summary taskType. A project with no config is
    NOT bridge-gated (open to normal LS access).
    """

    project = models.OneToOneField(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="valtaris_config",
    )
    track_slug = models.CharField(max_length=128, db_index=True)
    min_tier = models.CharField(max_length=32, default="T1_associate")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "valtaris_sso"

    def __str__(self):
        return f"project {self.project_id}: {self.track_slug} >= {self.min_tier}"
