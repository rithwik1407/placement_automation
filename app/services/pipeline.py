"""The end-to-end flow for one Gmail message:

    dedup claim -> sender allowlist + authentication -> fetch body -> Claude -> WhatsApp

Nothing here replies to emails, submits forms or applies to jobs.
"""

import json
import logging

from app.config import get_settings
from app.gmail.email_reader import fetch_message, fetch_metadata, parse_message
from app.services import deduplication
from app.services.sender_verification import verify_sender
from app.whatsapp.notifier import send_alert

log = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.5


def default_classifier(email):
    if get_settings().classifier == "claude":
        from app.classifier.claude_service import classify_email

        return classify_email(email)
    from app.classifier.rules import classify_email_rules

    return classify_email_rules(email)


def process_message(service, message_id: str, *, classifier=None, notifier=None, force: bool = False) -> str:
    """Process one message and return its final status.

    force=True skips the duplicate check (used to re-send recent alerts for a sample check)."""
    classifier = classifier or default_classifier
    notifier = notifier or send_alert
    details = get_settings().store_email_details
    if not force and not deduplication.claim(message_id):
        return "duplicate"

    try:
        meta = parse_message(fetch_metadata(service, message_id))
        verdict = verify_sender(meta.from_header, meta.auth_results)
        if not verdict.allowed:
            # Only the sender address and reason are stored; the email itself is never read.
            deduplication.finish(message_id, "skipped_sender", sender=verdict.address, error=verdict.reason)
            return "skipped_sender"

        email = fetch_message(service, message_id)
        result = classifier(email)
        common = dict(
            sender=verdict.address,
            subject=email.subject if details else None,
            category=result.category,
            confidence=result.confidence,
            classification_json=result.model_dump_json() if details else None,
        )
        if not result.is_placement_related or result.category == "NOT_PLACEMENT":
            deduplication.finish(message_id, "not_placement", **common)
            return "not_placement"
        if result.confidence < MIN_CONFIDENCE:
            # Low confidence from an official sender: still alert rather than risk missing it.
            log.warning("Low-confidence classification for %s (%.2f); alerting anyway", message_id, result.confidence)

        wa_id = notifier(result, email)
        deduplication.finish(message_id, "alerted", whatsapp_message_id=wa_id, error=None, **common)
        log.info("Alert sent for %s (%s)", message_id, result.category)
        return "alerted"
    except Exception as e:  # recorded, never silent; retried by the sync job
        log.error("Failed to process %s: %s", message_id, type(e).__name__)
        deduplication.finish(message_id, "failed", error=f"{type(e).__name__}: {e}"[:2000])
        return "failed"


def process_many(service, message_ids: list[str], **kw) -> dict[str, str]:
    return {mid: process_message(service, mid, **kw) for mid in message_ids}


def summarize(results: dict[str, str]) -> str:
    counts: dict[str, int] = {}
    for status in results.values():
        counts[status] = counts.get(status, 0) + 1
    return json.dumps(counts)
