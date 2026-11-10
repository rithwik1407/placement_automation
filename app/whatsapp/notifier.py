"""Send placement alerts through the WhatsApp Cloud API.

Two modes:
- "template" (default): sends the approved `placement_alert` template. Works at any time.
- "text": sends a free-form message. Only delivered inside the 24-hour customer-service
  window (after you have messaged the business number), so use it for testing only.

The template must be created in WhatsApp Manager exactly as shown in README.md, with 9
body variables in this order.
"""

import logging
import re
import time

import httpx

from app.classifier.claude_service import Classification
from app.config import get_settings
from app.gmail.email_reader import EmailMessage

log = logging.getLogger(__name__)

MAX_PARAM_CHARS = 300


class WhatsAppError(RuntimeError):
    pass


def _clean_param(value: str) -> str:
    """Template parameters may not contain newlines/tabs or more than 4 consecutive spaces."""
    value = re.sub(r"[\r\n\t]+", " ", value or "").strip()
    value = re.sub(r" {2,}", " ", value)
    if len(value) > MAX_PARAM_CHARS:
        value = value[: MAX_PARAM_CHARS - 1] + "…"
    return value or "-"


def alert_fields(c: Classification, email: EmailMessage) -> list[str]:
    """Values for template variables {{1}}..{{9}}."""
    return [
        _clean_param(f"{c.priority} · {c.category_label.upper()}"),
        _clean_param(c.company),
        _clean_param(c.role),
        _clean_param(c.eligibility),
        _clean_param(c.registration_deadline),
        _clean_param(c.event_date_time),
        _clean_param(c.action_required),
        _clean_param(email.subject),
        _clean_param(email.link),
    ]


def render_text(c: Classification, email: EmailMessage) -> str:
    """Human-readable alert, used for text mode and dry-run logs."""
    f = alert_fields(c, email)
    lines = [
        "*College Placement Alert*",
        "",
        f"*{f[0]}*",
        f"Company: {f[1]}",
        f"Role: {f[2]}",
        f"Eligibility: {f[3]}",
        f"Registration deadline: {f[4]}",
        f"Date / time: {f[5]}",
        f"Next action: {f[6]}",
        "",
        f"Subject: {f[7]}",
        f"Original email (placement cell): {f[8]}",
    ]
    return "\n".join(lines)


def _payload(c: Classification, email: EmailMessage) -> dict:
    s = get_settings()
    base = {"messaging_product": "whatsapp", "to": s.whatsapp_recipient}
    if s.whatsapp_mode == "text":
        return {**base, "type": "text", "text": {"preview_url": False, "body": render_text(c, email)}}
    return {
        **base,
        "type": "template",
        "template": {
            "name": s.whatsapp_template_name,
            "language": {"code": s.whatsapp_template_language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": v} for v in alert_fields(c, email)],
                }
            ],
        },
    }


def send_alert(c: Classification, email: EmailMessage, client: httpx.Client | None = None) -> str:
    """Send the alert and return the WhatsApp message ID. Retries temporary failures."""
    s = get_settings()
    if s.dry_run:
        log.info("[DRY RUN] WhatsApp alert:\n%s", render_text(c, email))
        return "dry-run"
    if not (s.whatsapp_token and s.whatsapp_phone_number_id and s.whatsapp_recipient):
        raise WhatsAppError("WhatsApp is not configured (token, phone number ID, recipient)")

    url = f"https://graph.facebook.com/{s.whatsapp_api_version}/{s.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {s.whatsapp_token}"}
    payload = _payload(c, email)
    own_client = client is None
    client = client or httpx.Client(timeout=20)
    try:
        last_error = ""
        for attempt in range(4):
            try:
                r = client.post(url, json=payload, headers=headers)
            except httpx.TransportError as e:
                last_error = f"network error: {e}"
            else:
                if r.status_code < 300:
                    return r.json()["messages"][0]["id"]
                last_error = f"HTTP {r.status_code}: {r.text[:500]}"
                if r.status_code < 500 and r.status_code != 429:
                    break  # bad request / auth error: retrying will not help
            time.sleep(2**attempt)
        raise WhatsAppError(last_error)
    finally:
        if own_client:
            client.close()
