from django.apps import AppConfig


class ValtarisSsoConfig(AppConfig):
    name = "valtaris_sso"
    verbose_name = "Valtaris Portal bridge"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Connect the annotation post-save backfill (task.meta.valtaris_user_id).
        from . import signals

        signals.connect()

        # Install the standing gate on next-task (no-op unless
        # VALTARIS_ENFORCE_STANDING_GATE is enabled).
        try:
            from .gate import install_next_task_gate

            install_next_task_gate()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Valtaris: failed to install standing gate")
