# Valtaris Studio ↔ Portal Bridge — Operations Runbook

The `valtaris_sso` app is the **Studio side** of the bridge to the Valtaris HR
Portal. The Portal is the single source of truth for identity, tier, validator
standing, trust/fraud/sanctions, and payout. Studio owns task assignment and
label data and **never computes pay or writes tier**.

> **The one rule that protects money:** the per-annotation **webhook** is the
> only channel that moves money and the QA authority for pay. **Work-summary is a
> reporting mirror, never a second pay source** — it is computed from the same
> annotation events the webhook fires on, so the two agree by construction. A
> Studio number must never flip a Portal payout.

---

## 1. Components (all in `label_studio/valtaris_sso/`)

| Piece | File | Direction | Auth |
|---|---|---|---|
| SSO login | `sso_views.sso_login` (`GET /sso/login`) | Portal → Studio | `STUDIO_SSO_SECRET` (HS256 token) |
| Access control | `sso_views.set_active` (`POST /api/valtaris/set-active`) | Portal → Studio | `X-Valtaris-Secret` = `STUDIO_SSO_SECRET` |
| Standing read + gating | `standing.py` (`worker_can_pull`) | Studio → Portal | Bearer service key (`standing:read`) |
| Per-annotation webhook | `webhook_setup.py` + LS native webhooks | Studio → Portal | `X-Valtaris-Webhook-Secret` = `LABEL_STUDIO_WEBHOOK_SECRET` |
| Task meta stamping | `meta.py` + Annotation `post_save` signal | (internal) | — |
| Work-summary aggregation | `aggregation.py` (`valtaris_work_summary`) | Studio → Portal | Bearer service key (`worksummary:write`) |

Identity maps **only** on the opaque Portal `User.id` (`ValtarisIdentity` model);
never on email/name.

---

## 2. Configuration (env; also readable from Django settings)

| Var | Purpose | Notes |
|---|---|---|
| `STUDIO_SSO_SECRET` | verifies SSO token + set-active push | must equal the Portal's `STUDIO_SSO_SECRET` |
| `VALTARIS_REVOKE_SECRET` | optional override for set-active only | defaults to `STUDIO_SSO_SECRET` |
| `LABEL_STUDIO_WEBHOOK_SECRET` | webhook `X-Valtaris-Webhook-Secret` | must equal the Portal's value |
| `VALTARIS_SERVICE_ACCOUNT_KEY` | `vlt_…` key for standing + work-summary | scopes `standing:read` + `worksummary:write` |
| `VALTARIS_PORTAL_BASE_URL` | Portal origin | dev `http://localhost:3011` |
| `VALTARIS_STANDING_FAILURE_MODE` | `closed` (default) / `open` | see §5 |
| `VALTARIS_STANDING_CACHE_TTL` / `_STALE_MAX` / `_TIMEOUT` | standing cache/resilience | secs (60 / 900 / 4) |
| `VALTARIS_SUMMARY_TIMEOUT` | work-summary POST timeout | secs (30) |

Secrets are read at process start (module import) — **restart Studio after
changing any secret/URL env var.**

---

## 3. First-time setup

1. **Create the service account** in the Portal UI at `/admin/integrations`
   (executive login). Name it **exactly `label_studio`** (this becomes the
   `sourceSystem` tag on every WorkSummary row). Scopes: `standing:read` +
   `worksummary:write`. The raw `vlt_…` key is shown **once** — copy it into
   Studio's `VALTARIS_SERVICE_ACCOUNT_KEY`.
2. **Set the shared secrets** (`STUDIO_SSO_SECRET`, `LABEL_STUDIO_WEBHOOK_SECRET`)
   to match the Portal's env exactly.
3. **Register the webhook:**
   ```bash
   uv run python label_studio/manage.py valtaris_register_webhook
   ```
   Creates/refreshes an org-level webhook → `{PORTAL}/api/webhooks/label-studio`
   firing `ANNOTATION_CREATED`/`ANNOTATION_UPDATED` with the secret header.
4. **Allowlist the Portal host for SSRF** (see §6) if the Portal is on a
   loopback/private address — otherwise webhook delivery is silently blocked.
5. **Schedule the nightly aggregation** (cron / scheduler):
   ```bash
   uv run python label_studio/manage.py valtaris_work_summary   # previous UTC day
   ```
6. **Verify** identity mapping + task meta: every task must carry
   `meta.valtaris_user_id`, `item_count`, `is_gold` (stamped at import via
   `meta.stamp_task_meta`; backfilled from `completed_by` at annotation time as a
   safety net).

---

## 4. Key & secret rotation

**Service-account key (`vlt_…`)** — no downtime if done in this order:
1. Portal `/admin/integrations` → create a new `label_studio` key (keep the old
   active). 2. Update Studio `VALTARIS_SERVICE_ACCOUNT_KEY`, restart Studio.
3. Confirm standing + work-summary calls succeed. 4. Revoke the old key in the
   Portal → it 401s immediately.

**Webhook secret (`LABEL_STUDIO_WEBHOOK_SECRET`)** — rotate on both sides close
together (there is one shared value):
1. Set the new value in the Portal env and Studio env. 2. Restart both.
3. Re-run `valtaris_register_webhook` (rewrites the header on the existing
   webhook — idempotent). A mismatch shows as `401` at the Portal webhook route.

**SSO secret (`STUDIO_SSO_SECRET`)** — also gates set-active. Update the Portal
and Studio env together and restart both; in-flight SSO tokens (≈2 min TTL)
minted under the old secret will fail to verify until users re-click "Open
Studio". If you use a separate `VALTARIS_REVOKE_SECRET`, rotate it in step with
the Portal's push secret.

---

## 5. Access control & the "kick a live worker" path

On confirmed-fraud / sanctions / manual-suspend the Portal pushes
`set-active(false)`. Studio then: flips `is_active`, and **rotates the session
auth hash** (`set_unusable_password`) so the live signed-cookie session is
flushed by `AuthenticationMiddleware` on the next request (there is no
server-side session row under `SESSION_ENGINE=signed_cookies`). `is_active=False`
also makes `ModelBackend.get_user()` return `None`.

- **Reactivation is MANUAL:** a human restores on the Portal, which pushes
  `set-active(true)`. Studio never auto-reactivates — `sso_login` refuses an
  inactive user (403) even with a fresh token.
- **Belt-and-suspenders:** even if a push is missed, `worker_can_pull` re-reads
  standing before assignment. It **fails CLOSED** by default (a Portal outage or
  auth error denies assignment) because access gating enforces
  sanctions/fraud/suspension. `VALTARIS_STANDING_FAILURE_MODE=open` trades that
  for availability during an outage — **a sanctioned worker could then be served
  tasks until the Portal recovers.** Change deliberately.
- **Residual:** LS also issues DRF/JWT access tokens (`jwt_auth`) that live until
  expiry regardless of `is_active`; a blocked worker's session dies immediately
  but an already-issued API access token remains valid until it expires (refresh
  fails once blocked). Keep the access-token TTL short.

---

## 6. Incident response

### Webhook not delivering (no payouts appearing)
1. **SSRF block** — LS delivers via `ssrf_safe_post`, which blocks
   loopback/private targets by default. Allowlist the Portal host in the SSRF
   settings (`core.settings`) or place the Portal on an allowed address.
2. **Auto-disable** — a webhook auto-disables after repeated failures
   (`Webhook.consecutive_failures`). Check `is_active`; re-run
   `valtaris_register_webhook` to re-enable + refresh config.
3. **Secret mismatch** → Portal returns `401`. Re-check
   `LABEL_STUDIO_WEBHOOK_SECRET` on both sides (§4).
4. **Missing meta** — if payouts land unattributed, tasks lack
   `meta.valtaris_user_id`. Confirm import-time stamping and that the
   `ValtarisIdentity` mapping exists for the completing worker.

### Drift flag (Portal: payout ledger vs work-summary diverge)
The Portal's nightly `reconcileFromLabelStudio` compares
`sum(WorkSummary.unitsApproved where sourceSystem=label_studio)` to the
payout-derived count and flags drift. Studio-side diagnosis:
1. **Unattributed annotations** — `valtaris_work_summary` logs an `unattributed`
   count; a nonzero value means webhooks fired without `meta.valtaris_user_id`.
   Fix the mapping/stamping, then re-run for the window (idempotent upsert).
2. **Missed webhooks** — re-run the aggregation (safe) and let the Portal's
   reconcile poll re-ingest; the summary and ledger should re-converge.
3. **Never "fix" drift by editing pay from the summary.** The summary reconciles
   *to* the ledger, not the reverse.

### Pay dispute
The per-annotation webhook + the Portal's QA/validator/appeal state machine are
authoritative. A Studio consensus/QA number is display/analytics only and at most
routes *future* work to sampling — it never changes a `Payout` status. Point
disputes at the Portal's appeal flow.

### Re-running aggregation safely
```bash
uv run python label_studio/manage.py valtaris_work_summary \
  --period-start 2026-08-20T00:00:00Z --period-end 2026-08-21T00:00:00Z
```
Idempotent on `(userId, periodStart, periodEnd, taskType, sourceSystem)`; add
`--dry-run` to inspect without posting.

---

## 7. Invariants (do not violate)

1. Studio never computes pay and never writes tier — the Portal is source of truth.
2. Identity maps on the opaque Portal `User.id` only; no PII crosses for matching.
3. One ledger moves money (the webhook); work-summary is a view of it.
4. Reactivation after a compliance/fraud block is manual (a human on the Portal).
5. Gold tasks route to qualification scoring, not pay — excluded from summaries.

---

## 8. Enabling the live standing gate (provisioning)

The standing gate at task-serve is **opt-in** and off by default.

1. **Tag each gated project** with the track it serves + the minimum tier:
   ```python
   from valtaris_sso.projects_config import set_project_requirement
   set_project_requirement(project, "img-bbox", "T2_skilled")
   ```
   Un-tagged projects are not gated (normal LS access). The track tag also
   becomes the work-summary `taskType`.
2. **Enable enforcement:** set `VALTARIS_ENFORCE_STANDING_GATE=true` and restart
   Studio. `apps.ready()` then wraps `get_next_task` so a worker who is not
   `active` + qualified for a project's (track, tier) is served no task
   ("no tasks"). The wrapper fails OPEN on internal errors (never bricks serving);
   the standing check itself fails closed on an undeterminable standing.
3. **Sync membership** (visibility layer; enforcement is the gate):
   ```bash
   uv run python label_studio/manage.py valtaris_provision_worker --portal-id <User.id>
   ```
   Enables the worker on gated projects they qualify for, disables them elsewhere.
   Requires the Studio user to already exist (created on first SSO login) and does
   not reactivate a blocked account (manual on the Portal).

Only bridge-managed users (those with a `ValtarisIdentity`) are gated — local/
admin users pass through.

---

## 9. Dataset flow: validation/review (C3) + annotated/validated counts (C4)

Imported tasks carry `meta.{valtaris_project, source_row_id, is_gold}` (C1); the
annotation webhook (C2) echoes them automatically (the task serializer includes
`meta`). Validation uses the **2nd-annotation-on-same-task** model:

- A validator adds a second annotation whose result carries a `review_decision`
  choice (`approve`|`reject`|`correction`), optionally `review_reason_code` and
  `review_reason_detail`. Configure these `from_name`s in the project labeling
  config (overridable via `VALTARIS_REVIEW_DECISION_FIELD` etc.).
- On save, `review.emit_review_for_annotation` POSTs C3 to
  `{PORTAL}/api/integration/review` (Bearer service key, scope `review:write`),
  attributed to the validator (`completed_by` → Portal id) and the original
  annotator (the task's first non-review annotation). Best-effort; never breaks
  the annotation save. Idempotent per (project, sourceRowId, validator).
- **CROSS-REPO REQUIREMENT:** because the review is an annotation, the C2 webhook
  ALSO fires for it. The Portal MUST treat any annotation whose `result` carries
  `review_decision` as a review (NOT a pay annotation) — the field is present in
  the C2 payload. Otherwise the annotator would be paid a second time.
- **Aggregation (C4):** `compute_summaries` credits non-review annotations as
  `unitsAnnotated` (to the annotator via `meta.valtaris_user_id`) and review
  annotations as `unitsValidated` (to the validator via `completed_by`); gold and
  cancelled excluded. Row field names `unitsAnnotated`/`unitsValidated` must match
  the Portal's extended work-summary schema.

Extra env (optional; defaults shown): `VALTARIS_REVIEW_DECISION_FIELD=review_decision`,
`VALTARIS_REVIEW_REASON_CODE_FIELD=review_reason_code`,
`VALTARIS_REVIEW_REASON_DETAIL_FIELD=review_reason_detail`, `VALTARIS_REVIEW_TIMEOUT=10`.

Studio-side NOT built yet: **dashboards (E)** — per-person progress views for
annotators (native LS project/DM views cover "N of M done") and a custom
validator queue/reviews view.

---

## 10. Portal-only access (no direct Studio login)

Studio is SSO-only: `valtaris_sso.middleware.PortalOnlyLoginMiddleware` redirects
the native login/signup pages (`/user/login*`, `/user/signup*`) to
`VALTARIS_PORTAL_LOGIN_URL` for unauthenticated requests, so no annotator,
validator, or admin can sign in at Studio — the ONLY way to a Studio session is
`/sso/login?token=…` from the Portal. Enabled by default; set
`VALTARIS_PORTAL_ONLY_LOGIN=false` to restore native login for local admin/debug.
Emergency ops without the Portal: `manage.py` (shell/commands) or temporarily
flip the flag off + restart.
