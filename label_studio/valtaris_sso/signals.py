"""Annotation post-save backfill: guarantee task.meta carries the bridge fields.

Connected from apps.ready(). Runs during annotation creation (inside the API
view, before the @api_webhook decorator serializes the response), so the
outgoing per-annotation webhook payload includes valtaris_user_id even for
pooled tasks that weren't stamped at import. Never raises — annotation save must
never break because of the bridge.
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def connect():
    from tasks.models import Annotation

    from .meta import backfill_valtaris_user_id
    from .review import emit_review_for_annotation

    @receiver(post_save, sender=Annotation, dispatch_uid="valtaris_backfill_task_meta")
    def _on_annotation_saved(sender, instance, **kwargs):
        # 1) Ensure the task carries attribution meta (annotator side).
        try:
            backfill_valtaris_user_id(instance)
        except Exception:
            logger.exception("Valtaris: failed to backfill task meta for annotation %s", getattr(instance, "id", "?"))
        # 2) If this annotation is a validator's review, emit the C3 review event.
        #    (No-op for ordinary annotations; best-effort, never raises.)
        emit_review_for_annotation(instance)
