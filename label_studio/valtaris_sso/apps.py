from django.apps import AppConfig


class ValtarisSsoConfig(AppConfig):
    name = "valtaris_sso"
    verbose_name = "Valtaris Portal bridge"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Connect the annotation post-save backfill (task.meta.valtaris_user_id).
        from . import signals

        signals.connect()
