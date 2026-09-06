# Valtaris Studio — Deployment

Studio is the annotation/labelling + qualification-exam engine. It is **not** the
source of truth for identity, tier, or pay — that's the Portal. Deploy on
`studio.<yourdomain>`, reverse-proxied by Caddy, SSO-only, never surfaced as a
standalone "Label Studio" product. The bridge internals + ops live in
`label_studio/valtaris_sso/RUNBOOK.md`; this file is the deploy path.

## Status vs the prep checklist (what's already done on `develop`)

| Checklist item | State |
|---|---|
| Rebrand (dark theme, VALTARIS logo/favicon, Montserrat, neutralized tips) | ✅ done |
| SSO handoff (Portal→Studio) | ✅ built + verified (`valtaris_sso`, custom HS256 verify — does NOT rely on LS `jwt_auth` for external tokens, which was the open question) |
| Disable public signup / direct login | ✅ done (`VALTARIS_PORTAL_ONLY_LOGIN` middleware redirects login+signup to the Portal; also set `DISABLE_SIGNUP_WITHOUT_LINK=true`) |
| User provisioning | ✅ auto on first SSO login (`ValtarisIdentity`); Portal pushes membership via provisioning |
| Per-annotation webhook + task meta, work-summary, review (C3), standing gate, dashboard | ✅ done + tested (47 tests) |
| **Pin to a tagged upstream stable release** | ⛔ OPEN — still on the cloned commit. Decision + rebase needed (see below). |
| Strip remaining `labelstud.io`/Slack/GitHub links from in-app chrome (Home "Resources", help menu) | ⚠️ partial — login/tips done; the SPA "Resources" panel still links out. Needs a frontend pass + rebuild. |
| Gold-task qualification flow end-to-end | ⚠️ mechanism ready (`is_gold` meta + webhook); confirm on a real project (smoke test). |

## 1. Pin to a stable release (do first, it's a decision)
The fork is on the commit it was cloned at, not a tagged release. For production,
rebase the Valtaris work onto the latest upstream **tagged stable** tag, then pull
upstream deliberately. This is a real rebase (possible conflicts) — do it on a
branch, run `manage.py test valtaris_sso` + a smoke test, then fast-forward
`develop`. Keep rebrand commits isolated from functional ones so future
security-patch merges stay clean.

## 2. Access-control constraint (shapes the deployment)
Community Edition has **no per-project RBAC and no task assignment** — every user
on an instance can see all projects. The isolation boundary is the **instance**
(or invite-gated project group), not an in-app permission:
- One instance behind SSO is fine for a single client/track at launch.
- A second client or a materially different-sensitivity track ⇒ its own instance
  / isolated project group. Plan for it now; don't retrofit under pressure.
- The standing gate (`VALTARIS_ENFORCE_STANDING_GATE`) narrows *which task pools*
  a worker may pull by track/tier, but it is not a substitute for instance
  isolation between clients.

## 3. Environment
Copy `studio.env.example` → `studio.env`, fill secrets (generate a fresh
`SECRET_KEY`), and keep it out of git. Bridge secrets/keys must match the Portal's
env exactly. Note the LS env names: `POSTGRE_*` (not `POSTGRES_*`),
`DISABLE_SIGNUP_WITHOUT_LINK`, `REDIS_LOCATION`, `LABEL_STUDIO_HOST`.

## 4. Run
```bash
cp studio.env.example studio.env      # fill in secrets
docker compose --env-file studio.env up -d --build
docker compose exec studio python label_studio/manage.py valtaris_register_webhook
```
Studio's own Postgres + Redis are in this compose — a **separate database from the
Portal's**. The entrypoint runs migrations on start.

## 5. Storage
Launch on the local `studio_media` volume, **actively monitor disk** (media grows
fast). Migrate to S3-compatible object storage (Cloudflare R2 / DO Spaces) via LS
cloud storage before local disk becomes impractical. Plan the migration now.

## 6. Own node
Studio is the most I/O-heavy service under load. Colocate for launch; move Studio
to its own node **first** (before Portal/website) on sustained CPU/disk pressure
or when a second client/track forces a second instance.

## 7. DNS & routing (Caddy)
`studio.<yourdomain>` → this stack's `studio:8080`. Set `LABEL_STUDIO_HOST`
accordingly and `USE_X_FORWARDED_HOST=true` behind the proxy. Access is SSO-only:
the Portal's "Open Studio" must link to `https://studio.<yourdomain>/sso/login?token=…`
(the login/signup pages redirect back to the Portal by design).

## 8. Smoke test (after deploy)
- Public signup blocked: hit `/user/signup/` and `/user/login/` → both **302 → Portal login**.
- SSO: a Portal-minted token at `/sso/login?token=…` → **302 → /projects/**, session created.
- Bridge: `valtaris_register_webhook` created an active webhook; annotate a task →
  `ANNOTATION_CREATED` reaches the Portal (pilot on one low-stakes project first).
- Studio→Portal auth (standing / work-summary / review) returns non-401/403 with
  the `vlt_…` key set.
- `annotator_evaluation_enabled` + a `ground_truth=True` task serves the gold task
  first to a test annotator.
- No page or UI element says "Label Studio" (see the partial item above).

## 9. Open risks
- **Pinning (item 1)** not yet done — running unpinned upstream is not for prod.
- **No native RBAC** ⇒ cross-client isolation is operational discipline (instance
  segmentation + invite gating), and a mistake here is a data-exposure risk, not
  just a bug.
- **JWT/SSO**: resolved — SSO uses our own HS256 verify in `valtaris_sso`, not LS's
  `jwt_auth`; safe to announce single-login once deployed behind the proxy.
- Residual DRF/JWT access tokens outlive `is_active` until expiry (see RUNBOOK §5);
  keep access-token TTL short.
