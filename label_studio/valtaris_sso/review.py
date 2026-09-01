"""Validation/review emission (Studio → Portal, contract C3).

Review model (operator-confirmed): a validator adds a SECOND annotation to the
same task, carrying a ``review_decision`` choice (approve|reject|correction).
When such an annotation is created/updated we POST a review event to the Portal
attributed to the validator AND the original annotator:

    POST {PORTAL}/api/integration/review   (Bearer service key, scope review:write)
    { validatorUserId, project, sourceRowId, annotatorUserId,
      decision, reasonCode, reasonDetail }

Detection is by the presence of a ``review_decision`` region in the annotation
result (from_name configurable via VALTARIS_REVIEW_DECISION_FIELD). Because LS's
AnnotationWebhookSerializer serializes the full result, the SAME signal also
reaches the Portal's C2 webhook — so the Portal MUST treat any annotation whose
result carries ``review_decision`` as a review (not a pay annotation). The
Studio-side aggregation (aggregation.py) already excludes review annotations from
the annotated/pay count and credits them as validated instead.

Idempotency: the Portal upserts on (project, sourceRowId, validator); re-sending
on ANNOTATION_UPDATED simply updates the decision (a validator changing their
verdict).
"""

import logging

import requests

from .bridge_config import cfg, portal_review_url, service_account_key

logger = logging.getLogger(__name__)

VALID_DECISIONS = {"approve", "reject", "correction"}


def _decision_field():
    return cfg("VALTARIS_REVIEW_DECISION_FIELD", "review_decision")


def _reason_code_field():
    return cfg("VALTARIS_REVIEW_REASON_CODE_FIELD", "review_reason_code")


def _reason_detail_field():
    return cfg("VALTARIS_REVIEW_REASON_DETAIL_FIELD", "review_reason_detail")


def _result_value(result, from_name):
    """First scalar value for a result region with the given from_name, or None.
    Handles Choices (value.choices) and text/textarea (value.text)."""
    for item in result or []:
        if not isinstance(item, dict) or item.get("from_name") != from_name:
            continue
        val = item.get("value") or {}
        choices = val.get("choices")
        if choices:
            return choices[0]
        text = val.get("text")
        if text:
            return text[0] if isinstance(text, list) else text
    return None


def extract_review(annotation):
    """If this annotation is a review, return {decision, reason_code, reason_detail};
    else None. A review is any annotation whose result carries the decision field
    with a recognized value."""
    result = getattr(annotation, "result", None)
    decision = _result_value(result, _decision_field())
    if decision is None:
        return None
    decision = str(decision).strip().lower()
    if decision not in VALID_DECISIONS:
        logger.warning("Ignoring review with unrecognized decision %r", decision)
        return None
    return {
        "decision": decision,
        "reason_code": _result_value(result, _reason_code_field()),
        "reason_detail": _result_value(result, _reason_detail_field()),
    }


def portal_id_for_user_id(user_id):
    if not user_id:
        return None
    from .models import ValtarisIdentity

    ident = ValtarisIdentity.objects.filter(user_id=user_id).first()
    return ident.portal_user_id if ident else None


def _annotator_portal_id(task, exclude_annotation_id):
    """The original annotator's Portal id: the earliest annotation on the task
    that is NOT itself a review, mapped via the SSO link. Falls back to the task's
    stamped meta.valtaris_user_id."""
    if task is not None:
        for ann in task.annotations.order_by("created_at"):
            if ann.id == exclude_annotation_id or extract_review(ann) is not None:
                continue
            pid = portal_id_for_user_id(ann.completed_by_id)
            if pid:
                return pid
    meta = (getattr(task, "meta", None) or {}) if task else {}
    return meta.get("valtaris_user_id")


def build_review_payload(annotation):
    """Return the C3 payload dict for a review annotation, or None if it is not a
    review or cannot be attributed to a validator."""
    review = extract_review(annotation)
    if review is None:
        return None
    validator_pid = portal_id_for_user_id(getattr(annotation, "completed_by_id", None))
    if not validator_pid:
        logger.warning("Review annotation %s has no mapped validator; skipping", getattr(annotation, "id", "?"))
        return None
    task = getattr(annotation, "task", None)
    meta = (getattr(task, "meta", None) or {}) if task else {}
    return {
        "validatorUserId": validator_pid,
        "project": meta.get("valtaris_project"),
        "sourceRowId": meta.get("source_row_id"),
        "annotatorUserId": _annotator_portal_id(task, getattr(annotation, "id", None)),
        "decision": review["decision"],
        "reasonCode": review["reason_code"],
        "reasonDetail": review["reason_detail"],
    }


def post_review(payload):
    """POST one review event. Returns the HTTP status code, or None on skip."""
    key = service_account_key()
    if not key:
        raise ValueError("VALTARIS_SERVICE_ACCOUNT_KEY is not configured (scope review:write)")
    resp = requests.post(
        portal_review_url(),
        json=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        timeout=float(cfg("VALTARIS_REVIEW_TIMEOUT", 10) or 10),
    )
    if resp.status_code >= 400:
        logger.error("review POST failed %s: %s", resp.status_code, resp.text[:200])
    return resp.status_code


def emit_review_for_annotation(annotation):
    """Detect + POST a review event. Best-effort; never raises to the caller
    (annotation save must not break). Returns the status code or None."""
    try:
        payload = build_review_payload(annotation)
        if payload is None:
            return None
        return post_review(payload)
    except Exception:
        logger.exception("Valtaris: failed to emit review for annotation %s", getattr(annotation, "id", "?"))
        return None
