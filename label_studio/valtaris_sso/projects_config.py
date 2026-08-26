"""Project → track/tier requirement helpers (Phase 5 provisioning)."""

DEFAULT_MIN_TIER = "T1_associate"


def set_project_requirement(project, track_slug, min_tier=DEFAULT_MIN_TIER):
    """Tag a project with the track it serves and the minimum tier to pull it."""
    from .models import ValtarisProjectConfig

    cfg, _created = ValtarisProjectConfig.objects.update_or_create(
        project=project,
        defaults={"track_slug": track_slug, "min_tier": min_tier or DEFAULT_MIN_TIER},
    )
    return cfg


def get_project_requirement(project):
    """Return (track_slug, min_tier) for a project, or (None, DEFAULT_MIN_TIER) if
    the project is not Valtaris-gated."""
    from .models import ValtarisProjectConfig

    cfg = ValtarisProjectConfig.objects.filter(project=project).first()
    if cfg is None:
        return (None, DEFAULT_MIN_TIER)
    return (cfg.track_slug, cfg.min_tier or DEFAULT_MIN_TIER)
