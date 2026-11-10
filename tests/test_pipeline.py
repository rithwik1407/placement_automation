"""Offline tests: no Gmail, Claude or WhatsApp calls are made."""

import base64
import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["DRY_RUN"] = "true"

from app.classifier.claude_service import NOT_MENTIONED, Classification  # noqa: E402
from app.database.models import ProcessedEmail, SessionLocal, init_db  # noqa: E402
from app.services.pipeline import process_message  # noqa: E402
from app.services.sender_verification import SenderAllowlist, verify_sender  # noqa: E402
from app.whatsapp.notifier import alert_fields, render_text  # noqa: E402

init_db()

ALLOW = SenderAllowlist(addresses={"placement-cell@college-domain.edu"}, domains={"tpo.college-domain.edu"})
GOOD_AUTH = [
    "mx.google.com; dkim=pass header.i=@college-domain.edu header.s=google header.b=abc; "
    "spf=pass (google.com: domain of x designates y) smtp.mailfrom=placement-cell@college-domain.edu; "
    "dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=college-domain.edu"
]
BAD_AUTH = ["mx.google.com; dkim=fail header.i=@evil.com; spf=softfail; dmarc=fail header.from=college-domain.edu"]


# ---------- sender verification ----------

def test_allowlisted_and_authenticated():
    assert verify_sender("Placement Cell <Placement-Cell@College-Domain.edu>", GOOD_AUTH, ALLOW).allowed


def test_display_name_alone_is_not_trusted():
    v = verify_sender("Placement Cell <random@gmail.com>", GOOD_AUTH, ALLOW)
    assert not v.allowed and "allowlist" in v.reason


def test_spoofed_from_fails_authentication():
    v = verify_sender("placement-cell@college-domain.edu", BAD_AUTH, ALLOW)
    assert not v.allowed and "DKIM" in v.reason


def test_domain_allowlist_with_parent_dkim():
    auth = ["mx.google.com; dkim=pass header.d=college-domain.edu header.s=s1"]
    assert verify_sender("officer@tpo.college-domain.edu", auth, ALLOW).allowed


def test_authentication_can_be_disabled():
    lax = SenderAllowlist(addresses=ALLOW.addresses, require_authentication=False)
    assert verify_sender("placement-cell@college-domain.edu", [], lax).allowed


# ---------- pipeline with fake Gmail ----------

def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


class FakeGmail:
    def __init__(self, messages: dict):
        self.messages_by_id = messages
        self.full_fetches: list[str] = []

    def users(self):
        return self

    def messages(self):
        return self

    def get(self, userId, id, format, metadataHeaders=None):
        if format == "full":
            self.full_fetches.append(id)
        msg = self.messages_by_id[id]
        return type("Req", (), {"execute": lambda _self: msg})()


def _msg(mid, sender, subject, body, auth=GOOD_AUTH):
    headers = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]
    headers += [{"name": "Authentication-Results", "value": a} for a in auth]
    return {
        "id": mid,
        "threadId": "t" + mid,
        "payload": {"mimeType": "text/plain", "headers": headers, "body": {"data": _b64(body)}},
    }


def _classification(**over):
    base = dict(
        is_placement_related=True,
        category="PLACEMENT_DRIVE",
        company="Example Technologies",
        role="Software Development Engineer",
        eligibility="B.Tech CSE/IT/ECE, CGPA 7.0+",
        registration_deadline="13 November 2026, 5:00 PM",
        event_date_time="Online assessment 16 November 2026, 10:00 AM",
        venue_or_link=NOT_MENTIONED,
        action_required="Register on the placement portal before the deadline.",
        summary="Example Technologies SDE drive.",
        confidence=0.97,
    )
    base.update(over)
    return Classification(**base)


def test_pipeline_alerts_once_and_dedups(monkeypatch):
    import app.services.pipeline as p
    import app.services.sender_verification as sv

    monkeypatch.setattr(sv, "get_allowlist", lambda: ALLOW)
    gmail = FakeGmail({"m1": _msg("m1", "placement-cell@college-domain.edu", "Drive", "body")})
    sent = []

    def notifier(c, e):
        sent.append((c, e))
        return "wamid.1"

    assert p.process_message(gmail, "m1", classifier=lambda e: _classification(), notifier=notifier) == "alerted"
    assert p.process_message(gmail, "m1", classifier=lambda e: _classification(), notifier=notifier) == "duplicate"
    assert len(sent) == 1
    with SessionLocal() as s:
        assert s.get(ProcessedEmail, "m1").whatsapp_message_id == "wamid.1"


def test_unknown_sender_is_never_read_or_classified(monkeypatch):
    import app.services.sender_verification as sv

    monkeypatch.setattr(sv, "get_allowlist", lambda: ALLOW)
    gmail = FakeGmail({"m2": _msg("m2", "Placement Cell <promo@ads.com>", "Placement offer!", "buy course")})

    def boom(_):
        raise AssertionError("classifier must not run")

    assert process_message(gmail, "m2", classifier=boom, notifier=boom) == "skipped_sender"
    assert gmail.full_fetches == []


def test_not_placement_no_alert(monkeypatch):
    import app.services.sender_verification as sv

    monkeypatch.setattr(sv, "get_allowlist", lambda: ALLOW)
    gmail = FakeGmail({"m3": _msg("m3", "placement-cell@college-domain.edu", "Fest", "fest")})
    c = _classification(is_placement_related=False, category="NOT_PLACEMENT")

    def boom(*_):
        raise AssertionError("must not notify")

    assert process_message(gmail, "m3", classifier=lambda e: c, notifier=boom) == "not_placement"


def test_failure_is_recorded_then_retried(monkeypatch):
    import app.services.sender_verification as sv

    monkeypatch.setattr(sv, "get_allowlist", lambda: ALLOW)
    gmail = FakeGmail({"m4": _msg("m4", "placement-cell@college-domain.edu", "Shortlist", "list")})

    def failing(*_):
        raise RuntimeError("WhatsApp down")

    assert process_message(gmail, "m4", classifier=lambda e: _classification(), notifier=failing) == "failed"
    with SessionLocal() as s:
        assert "WhatsApp down" in s.get(ProcessedEmail, "m4").error
    assert (
        process_message(gmail, "m4", classifier=lambda e: _classification(), notifier=lambda c, e: "ok") == "alerted"
    )


# ---------- WhatsApp formatting ----------

def test_template_params_are_clean():
    from app.gmail.email_reader import EmailMessage

    email = EmailMessage("abc", "t", "x@y", "Subject\nwith newline", "", "")
    fields = alert_fields(_classification(eligibility="line1\n\nline2     spaced"), email)
    assert len(fields) == 9
    assert all("\n" not in f and "     " not in f for f in fields)
    assert fields[0] == "URGENT · NEW PLACEMENT DRIVE"
    assert fields[8].endswith("/abc")
    text = render_text(_classification(), email)
    assert "Company: Example Technologies" in text
