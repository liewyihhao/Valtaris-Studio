"""URL patterns for the Valtaris Studio SSO app. Include from the fork's root
urls.py (see README.md)."""

from django.urls import path

from . import sso_views

urlpatterns = [
    path("sso/login", sso_views.sso_login, name="valtaris_sso_login"),
    path("api/valtaris/set-active", sso_views.set_active, name="valtaris_set_active"),
]
