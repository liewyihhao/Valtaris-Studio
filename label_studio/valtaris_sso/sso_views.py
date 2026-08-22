"""Valtaris Studio SSO — trust login tokens minted by the Valtaris Portal.

The Portal is the single identity provider. It only mints a token for an
annotator who passed the eligibility gate (approved + passed exam + agreements
+ tax + KYC + not suspended), so a failed-exam user never reaches Studio.

Endpoints:
  GET  /sso/login                — consume a Portal token, log the user in
  POST /api/valtaris/set-active  — Portal-driven access control (secret-gated)

Bridge invariants honored here (see docs/label-studio-bridge-design.md):
  * Identity maps on the opaque Portal ``User.id`` only, never email/name.
  * Studio never auto-reactivates a blocked worker — reactivation is manual on
    the Portal, which then pushes set-active(true). A ``is_active=False`` user is
    refused login even if they somehow present a fresh token.
"""

import hmac
import json
import os
import time

from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt

from .models import ValtarisIdentity
from .sso_jwt import verify_jwt

User = get_user_model()

SSO_SECRET = os.environ.get('STUDIO_SSO_SECRET', '')
# The Portal pushes set-active with X-Valtaris-Secret == STUDIO_SSO_SECRET.
# VALTARIS_REVOKE_SECRET is an optional override (e.g. to rotate the revoke
# channel independently of SSO); when unset it falls back to the SSO secret.
REVOKE_SECRET = os.environ.get('VALTARIS_REVOKE_SECRET', '') or SSO_SECRET


def _secret_ok(presented: str) -> bool:
    """Constant-time check against the SSO secret (and the optional override)."""
    if not presented:
        return False
    ok = False
    for candidate in {SSO_SECRET, REVOKE_SECRET}:
        if candidate and hmac.compare_digest(presented, candidate):
            ok = True
    return ok


@transaction.atomic
def _get_or_create_user(email, portal_user_id):
    """Resolve the Studio user for a Portal identity.

    Lookup is keyed on ``portal_user_id`` (the bridge invariant). Email is only
    used to create the local SSO-only user record on first login. We do NOT
    force ``is_active`` on an existing user — its value is owned by the Portal
    via set-active, so re-login must never silently restore a blocked worker.
    """
    identity = (
        ValtarisIdentity.objects.select_related('user')
        .filter(portal_user_id=portal_user_id)
        .first()
    )
    if identity is not None:
        return identity.user

    # First login for this Portal id: create the SSO-only Studio user.
    user, _created = User.objects.get_or_create(email=email, defaults={'username': email})
    user.set_unusable_password()  # SSO-only; there is no Studio password
    user.save()
    ValtarisIdentity.objects.create(user=user, portal_user_id=portal_user_id)

    # Label Studio users must belong to an Organization. In Community Edition
    # there is a single org — attach on first login.
    try:
        from organizations.models import Organization

        if getattr(user, 'active_organization_id', None) is None:
            org = Organization.objects.first()
            if org is not None:
                org.add_user(user)
                user.active_organization = org
                user.save(update_fields=['active_organization'])
    except Exception:
        # Don't block login if org wiring differs on this instance; surface in logs.
        pass

    return user


def sso_login(request):
    token = request.GET.get('token', '')
    if not SSO_SECRET:
        return HttpResponseForbidden('SSO not configured')
    claims = verify_jwt(token, SSO_SECRET)
    # Require both the opaque Portal id (sub) and an email to provision the user.
    if not claims or not claims.get('sub') or not claims.get('email'):
        return HttpResponseForbidden('Invalid or expired SSO token')

    user = _get_or_create_user(claims['email'], str(claims['sub']))
    if not user.is_active:
        # Access has been revoked on the Portal; do NOT auto-reactivate.
        return HttpResponseForbidden('Access revoked — contact Valtaris')
    # LS configures multiple auth backends, so name the one to log in with.
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    # LS's InactivitySessionTimeoutMiddleWare logs out any session missing
    # 'last_login' (treats it as expired) — set it exactly as LS's own login does.
    request.session['last_login'] = time.time()
    return redirect(request.GET.get('next') or '/projects/')


@csrf_exempt
def set_active(request):
    """Portal-driven access control.

    Body: {"valtaris_user_id": "<Portal User.id>", "active": true|false}
    Header: X-Valtaris-Secret: <STUDIO_SSO_SECRET>

    On deactivate we both flip ``is_active`` (blocks new task routing + re-login)
    AND rotate the session auth hash so any LIVE session is invalidated on its
    next request — the fork uses signed-cookie sessions, so there is no server
    session row to delete; rotating the password field changes
    ``get_session_auth_hash()``, which AuthenticationMiddleware verifies per
    request and flushes on mismatch. ``is_active=False`` also makes
    ModelBackend.get_user() return None for that session. Reactivation is manual
    (a human restores on the Portal, which pushes active=true).

    Returns 200 ok, 401 bad secret, 404 unknown user, 405 non-POST, 400 bad body.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST only'}, status=405)
    if not SSO_SECRET:
        return JsonResponse({'error': 'not configured'}, status=401)
    if not _secret_ok(request.headers.get('X-Valtaris-Secret', '')):
        return JsonResponse({'error': 'unauthorized'}, status=401)
    try:
        body = json.loads(request.body or '{}')
    except Exception:
        return JsonResponse({'error': 'bad json'}, status=400)

    portal_user_id = body.get('valtaris_user_id')
    if not portal_user_id:
        return JsonResponse({'error': 'valtaris_user_id required'}, status=400)
    active = bool(body.get('active', False))

    identity = (
        ValtarisIdentity.objects.select_related('user')
        .filter(portal_user_id=str(portal_user_id))
        .first()
    )
    if identity is None:
        return JsonResponse({'error': 'unknown user'}, status=404)

    user = identity.user
    user.is_active = active
    if not active:
        # Kill any live signed-cookie session by rotating the auth hash.
        user.set_unusable_password()
    user.save()

    return JsonResponse({'ok': True, 'valtaris_user_id': str(portal_user_id), 'active': active})
