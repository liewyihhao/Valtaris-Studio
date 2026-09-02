"""Portal-only access: disable direct Studio login.

Valtaris Studio is a work tool reachable ONLY by logging into the Valtaris
Portal, which then hands off an SSO token. No annotator, validator, or admin
should sign in at the Studio login page. This middleware redirects the Studio
login/signup pages (and any unauthenticated attempt to reach them, including a
crafted POST of credentials) to the Portal login, so the ONLY way to obtain a
Studio session is the SSO flow (`/sso/login?token=…`).

Enabled by default for this fork; set VALTARIS_PORTAL_ONLY_LOGIN=false to restore
native Studio login (local admin/debug). Runs after AuthenticationMiddleware so
authenticated users pass straight through.
"""

from django.shortcuts import redirect

from .bridge_config import portal_login_url, portal_only_login_enabled

# Studio's own credential entry points. The SSO callback (/sso/login) and the
# APIs are deliberately NOT here.
_LOGIN_PREFIXES = ("/user/login", "/user/signup")


class PortalOnlyLoginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if portal_only_login_enabled():
            path = request.path or ""
            if path.startswith(_LOGIN_PREFIXES):
                user = getattr(request, "user", None)
                if user is None or not user.is_authenticated:
                    # Bounce credential entry to the Portal; SSO is the only way in.
                    return redirect(portal_login_url())
        return self.get_response(request)
