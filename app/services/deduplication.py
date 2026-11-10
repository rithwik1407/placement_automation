"""Duplicate prevention keyed on the Gmail message ID.

`claim()` atomically inserts a row for a message. If the row already exists and finished
(alerted / skipped / not placement), the message is not processed again. Failed messages
can be retried a limited number of times, so a temporary WhatsApp or Claude outage does
not silently drop an alert.
"""

from sqlalchemy.exc import IntegrityError

from app.database.models import ProcessedEmail, SessionLocal

FINAL_STATUSES = {"alerted", "skipped_sender", "not_placement", "baseline"}
MAX_ATTEMPTS = 5


def claim(message_id: str) -> bool:
    """Return True if this worker should process the message now."""
    with SessionLocal() as s:
        try:
            s.add(ProcessedEmail(gmail_message_id=message_id, status="processing", attempts=1))
            s.commit()
            return True
        except IntegrityError:
            s.rollback()

        row = s.get(ProcessedEmail, message_id, with_for_update=True)
        if row is None or row.status in FINAL_STATUSES or row.status == "processing":
            # "processing" means another worker has it (or crashed mid-way; the sync job
            # treats rows stuck in processing as failed after a while).
            return False
        if row.attempts >= MAX_ATTEMPTS:
            return False
        row.status = "processing"
        row.attempts += 1
        s.commit()
        return True


def finish(message_id: str, status: str, **fields) -> None:
    with SessionLocal() as s:
        row = s.get(ProcessedEmail, message_id)
        if row is None:
            row = ProcessedEmail(gmail_message_id=message_id)
            s.add(row)
        row.status = status
        for k, v in fields.items():
            setattr(row, k, v)
        s.commit()


def release_stale(minutes: int = 15) -> int:
    """Mark rows stuck in 'processing' (crashed worker) as failed so they get retried."""
    from datetime import timedelta

    from app.database.models import utcnow

    cutoff = utcnow() - timedelta(minutes=minutes)
    with SessionLocal() as s:
        rows = (
            s.query(ProcessedEmail)
            .filter(ProcessedEmail.status == "processing", ProcessedEmail.updated_at < cutoff)
            .all()
        )
        for r in rows:
            r.status = "failed"
            r.error = (r.error or "") + " [stale processing lock released]"
        s.commit()
        return len(rows)


def retryable_ids() -> list[str]:
    with SessionLocal() as s:
        rows = (
            s.query(ProcessedEmail.gmail_message_id)
            .filter(ProcessedEmail.status == "failed", ProcessedEmail.attempts < MAX_ATTEMPTS)
            .all()
        )
        return [r[0] for r in rows]
