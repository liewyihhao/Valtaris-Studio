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
