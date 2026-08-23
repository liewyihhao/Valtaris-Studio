"""Tests for the Valtaris bridge standing connector + gating (Phase 5 #1).

Pure logic + mocked HTTP — no DB, no network. Run with:
    DJANGO_DB=sqlite DJANGO_SETTINGS_MODULE=core.settings.label_studio \\
      uv run python label_studio/manage.py test valtaris_sso
"""

from unittest import mock

from django.test import SimpleTestCase

from valtaris_sso import standing as S

ACTIVE = {
    "userId": "u1",
    "accountStatus": "active",
    "qualifications": [
        {"trackSlug": "img-bbox", "trackName": "Image BBox", "tier": "T2_skilled", "status": "active"},
        {"trackSlug": "audio", "trackName": "Audio", "tier": "T0_trainee", "status": "active"},
        {"trackSlug": "text", "trackName": "Text", "tier": "T3_specialist", "status": "revoked"},
    ],
    "validatorCapabilities": [
        {"trackSlug": "img-bbox", "status": "active"},
        {"trackSlug": "audio", "status": "paused"},
    ],
}
SUSPENDED = {**ACTIVE, "accountStatus": "suspended"}


class GatingLogicTests(SimpleTestCase):
    def test_tier_order(self):
        self.assertGreater(S.tier_rank("T3_specialist"), S.tier_rank("T2_skilled"))
        self.assertGreater(S.tier_rank("T1_associate"), S.tier_rank("T0_trainee"))
        self.assertEqual(S.tier_rank("bogus"), -1)

    def test_is_assignable(self):
        self.assertTrue(S.is_assignable(ACTIVE))
        self.assertFalse(S.is_assignable(SUSPENDED))
        self.assertFalse(S.is_assignable(None))

    def test_qualified_for(self):
        self.assertTrue(S.qualified_for(ACTIVE, "img-bbox", "T1_associate"))
        self.assertTrue(S.qualified_for(ACTIVE, "img-bbox", "T2_skilled"))
        self.assertFalse(S.qualified_for(ACTIVE, "img-bbox", "T3_specialist"))
        self.assertFalse(S.qualified_for(ACTIVE, "audio", "T1_associate"))  # T0 trainee
        self.assertFalse(S.qualified_for(ACTIVE, "text", "T1_associate"))  # revoked
        self.assertFalse(S.qualified_for(ACTIVE, "nope", "T1_associate"))  # unknown track
        self.assertFalse(S.qualified_for(SUSPENDED, "img-bbox", "T1_associate"))

    def test_can_validate(self):
        self.assertTrue(S.can_validate(ACTIVE, "img-bbox"))
        self.assertFalse(S.can_validate(ACTIVE, "audio"))  # paused
        self.assertFalse(S.can_validate(SUSPENDED, "img-bbox"))

    def test_assignable_tracks(self):
        self.assertEqual(S.assignable_tracks(ACTIVE, "T1_associate"), {"img-bbox"})
        self.assertEqual(S.assignable_tracks(ACTIVE, "T0_trainee"), {"img-bbox", "audio"})
        self.assertEqual(S.assignable_tracks(SUSPENDED), set())


class StandingFetchTests(SimpleTestCase):
    def setUp(self):
        S.clear_cache()
        self.addCleanup(S.clear_cache)

    def test_cache_hit(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("ok", ACTIVE)) as m:
            self.assertEqual(S.get_standing("u1"), ACTIVE)
            self.assertEqual(S.get_standing("u1"), ACTIVE)  # served from cache
            self.assertEqual(m.call_count, 1)

    def test_not_found(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("not_found", None)):
            self.assertIsNone(S.get_standing("ghost"))

    def test_auth_error_raises(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("auth_error", "401")):
            with self.assertRaises(S.StandingUnavailable):
                S.get_standing("u1")

    def test_stale_served_on_outage(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("ok", ACTIVE)):
            S.get_standing("u1")
        with self.settings(VALTARIS_STANDING_CACHE_TTL=0):
            with mock.patch.object(S, "_http_get_standing", return_value=("unavailable", "down")):
                self.assertEqual(S.get_standing("u1"), ACTIVE)  # stale within stale-max

    def test_outage_no_cache_raises(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("unavailable", "down")):
            with self.assertRaises(S.StandingUnavailable):
                S.get_standing("u1")


class WorkerCanPullTests(SimpleTestCase):
    def setUp(self):
        S.clear_cache()
        self.addCleanup(S.clear_cache)

    def test_ok(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("ok", ACTIVE)):
            self.assertEqual(S.worker_can_pull("u1", "img-bbox", "T1_associate"), (True, "ok"))

    def test_not_qualified(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("ok", ACTIVE)):
            allowed, reason = S.worker_can_pull("u1", "img-bbox", "T3_specialist")
            self.assertFalse(allowed)

    def test_suspended(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("ok", SUSPENDED)):
            self.assertEqual(S.worker_can_pull("u1", "img-bbox")[1], "account_suspended")

    def test_unknown_worker(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("not_found", None)):
            self.assertEqual(S.worker_can_pull("ghost", "img-bbox"), (False, "unknown_worker"))

    def test_fail_closed(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("unavailable", "down")):
            with self.settings(VALTARIS_STANDING_FAILURE_MODE="closed"):
                self.assertEqual(S.worker_can_pull("u1", "img-bbox"), (False, "standing_unavailable_fail_closed"))

    def test_fail_open(self):
        with mock.patch.object(S, "_http_get_standing", return_value=("unavailable", "down")):
            with self.settings(VALTARIS_STANDING_FAILURE_MODE="open"):
                self.assertTrue(S.worker_can_pull("u1", "img-bbox")[0])


# --- Phase 5 #3: webhook registration + task-meta stamping (DB-backed) ---
from types import SimpleNamespace  # noqa: E402

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import TestCase  # noqa: E402


def _make_org_project():
    from organizations.models import Organization
    from projects.models import Project

    User = get_user_model()
    user = User.objects.create_user(email="w@valtaris.test", password="p")
    org = Organization.create_organization(created_by=user, title="T")
    project = Project.objects.create(title="P", organization=org, created_by=user)
    return user, org, project


class WebhookSetupTests(TestCase):
    def test_ensure_portal_webhook_idempotent_and_rotates(self):
        from webhooks.models import Webhook

        from valtaris_sso.webhook_setup import ensure_portal_webhook

        _user, org, _project = _make_org_project()
        url = "http://portal.test/api/webhooks/label-studio"

        wh, created = ensure_portal_webhook(org, url=url, secret="sek")
        self.assertTrue(created)
        self.assertEqual(wh.url, url)
        self.assertEqual(wh.headers.get("X-Valtaris-Webhook-Secret"), "sek")
        self.assertFalse(wh.send_for_all_actions)
        self.assertTrue(wh.send_payload)
        self.assertTrue(wh.is_active)
        self.assertEqual(set(wh.get_actions()), {"ANNOTATION_CREATED", "ANNOTATION_UPDATED"})

        # Re-run rotates the secret and does not duplicate.
        wh2, created2 = ensure_portal_webhook(org, url=url, secret="rotated")
        self.assertFalse(created2)
        self.assertEqual(wh2.id, wh.id)
        self.assertEqual(wh2.headers.get("X-Valtaris-Webhook-Secret"), "rotated")
        self.assertEqual(Webhook.objects.filter(organization=org).count(), 1)


class TaskMetaTests(TestCase):
    def test_stamp_task_meta(self):
        from tasks.models import Task

        from valtaris_sso.meta import stamp_task_meta

        _user, _org, project = _make_org_project()
        task = Task.objects.create(project=project, data={})
        stamp_task_meta(task, valtaris_user_id="pid-1", item_count=5, is_gold=False)
        task.refresh_from_db()
        self.assertEqual(task.meta["valtaris_user_id"], "pid-1")
        self.assertEqual(task.meta["item_count"], 5)
        self.assertIs(task.meta["is_gold"], False)

    def test_stamp_defaults_item_count(self):
        from tasks.models import Task

        from valtaris_sso.meta import stamp_task_meta

        _user, _org, project = _make_org_project()
        task = Task.objects.create(project=project, data={})
        stamp_task_meta(task, valtaris_user_id="pid-1")
        task.refresh_from_db()
        self.assertEqual(task.meta["item_count"], 1)

    def test_backfill_from_completing_worker(self):
        from tasks.models import Task

        from valtaris_sso.meta import backfill_valtaris_user_id
        from valtaris_sso.models import ValtarisIdentity

        user, _org, project = _make_org_project()
        ValtarisIdentity.objects.create(user=user, portal_user_id="pid-42")
        task = Task.objects.create(project=project, data={})
        # Lightweight annotation stand-in (avoids triggering unrelated LS signals).
        ann = SimpleNamespace(task=task, completed_by_id=user.id, ground_truth=True, id=1)

        changed = backfill_valtaris_user_id(ann)
        self.assertTrue(changed)
        task.refresh_from_db()
        self.assertEqual(task.meta["valtaris_user_id"], "pid-42")
        self.assertIs(task.meta["is_gold"], True)
        self.assertEqual(task.meta["item_count"], 1)

    def test_backfill_no_mapping_no_userid(self):
        from tasks.models import Task

        from valtaris_sso.meta import backfill_valtaris_user_id

        user, _org, project = _make_org_project()  # no ValtarisIdentity for this user
        task = Task.objects.create(project=project, data={})
        ann = SimpleNamespace(task=task, completed_by_id=user.id, ground_truth=False, id=2)
        backfill_valtaris_user_id(ann)
        task.refresh_from_db()
        self.assertNotIn("valtaris_user_id", task.meta)  # not invented without a mapping
        self.assertEqual(task.meta["item_count"], 1)


# --- Phase 5 #4: work-summary aggregation (DB-backed) ---
import datetime as _dt  # noqa: E402


class WorkSummaryAggregationTests(TestCase):
    def _task(self, project, meta):
        from tasks.models import Task

        return Task.objects.create(project=project, data={}, meta=meta)

    def test_compute_summaries_excludes_gold_cancelled_unattributed(self):
        from tasks.models import Annotation

        from valtaris_sso.aggregation import compute_summaries

        _user, _org, project = _make_org_project()
        t_paid = self._task(project, {"valtaris_user_id": "pA", "item_count": 3})
        t_paid2 = self._task(project, {"valtaris_user_id": "pA", "item_count": 1})
        t_gold = self._task(project, {"valtaris_user_id": "pA", "item_count": 5, "is_gold": True})
        t_unattr = self._task(project, {"item_count": 2})

        # bulk_create skips LS annotation signals; created_at defaults to now.
        Annotation.objects.bulk_create([
            Annotation(task=t_paid, project=project, result=[]),
            Annotation(task=t_paid2, project=project, result=[]),
            Annotation(task=t_gold, project=project, result=[]),
            Annotation(task=t_unattr, project=project, result=[]),
            Annotation(task=t_paid, project=project, result=[], was_cancelled=True),
        ])

        now = _dt.datetime.now(_dt.timezone.utc)
        rows, stats = compute_summaries(now - _dt.timedelta(days=1), now + _dt.timedelta(days=1))

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["userId"], "pA")
        self.assertEqual(row["taskType"], "P")  # project title fallback
        self.assertEqual(row["unitsCompleted"], 4)   # 3 + 1 (gold/cancelled/unattributed excluded)
        self.assertEqual(row["unitsApproved"], 4)    # completed-only policy
        self.assertEqual(row["unitsRejected"], 0)
        self.assertIsNone(row["avgQualityScore"])
        self.assertEqual(stats, {
            "annotations": 5, "counted": 2, "skipped_gold": 1,
            "skipped_cancelled": 1, "unattributed": 1,
        })

    def test_task_type_prefers_track_tag(self):
        from valtaris_sso.aggregation import task_type_for

        class P:
            id = 1
            title = "My Project"
            meta = {"valtaris_track": "img-bbox"}

        self.assertEqual(task_type_for(P()), "img-bbox")
        P.meta = {}
        self.assertEqual(task_type_for(P()), "My Project")

    def test_post_summaries_batches_and_reads_written(self):
        from unittest import mock as _mock

        from valtaris_sso import aggregation as A

        rows = [{"userId": f"u{i}", "periodStart": "s", "periodEnd": "e", "taskType": "t",
                 "unitsCompleted": 1, "unitsApproved": 1, "unitsRejected": 0, "avgQualityScore": None}
                for i in range(1500)]

        resp = _mock.Mock(status_code=200)
        resp.json.return_value = {"ok": True, "written": 1000}
        with self.settings(VALTARIS_SERVICE_ACCOUNT_KEY="vlt_test"):
            with _mock.patch.object(A.requests, "post", return_value=resp) as m:
                result = A.post_summaries(rows)
        self.assertEqual(result["batches"], 2)     # 1500 -> 1000 + 500
        self.assertEqual(m.call_count, 2)
        self.assertEqual(result["written"], 2000)  # mock returns 1000 per batch

    def test_post_summaries_422_unknown_users(self):
        from unittest import mock as _mock

        from valtaris_sso import aggregation as A

        rows = [{"userId": "ghost", "periodStart": "s", "periodEnd": "e", "taskType": "t",
                 "unitsCompleted": 1, "unitsApproved": 1, "unitsRejected": 0, "avgQualityScore": None}]
        resp = _mock.Mock(status_code=422)
        resp.json.return_value = {"error": "Unknown userId(s).", "unknownUserIds": ["ghost"]}
        with self.settings(VALTARIS_SERVICE_ACCOUNT_KEY="vlt_test"):
            with _mock.patch.object(A.requests, "post", return_value=resp):
                result = A.post_summaries(rows)
        self.assertEqual(result["written"], 0)
        self.assertIn("ghost", result["unknown_user_ids"])

    def test_post_summaries_dry_run_and_empty(self):
        from valtaris_sso import aggregation as A

        self.assertEqual(A.post_summaries([])["written"], 0)
        dr = A.post_summaries([{"userId": "u"}], dry_run=True)
        self.assertTrue(dr["dry_run"])
        self.assertEqual(dr["would_post"], 1)
