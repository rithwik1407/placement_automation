"""Send placement alerts.

Channels (NOTIFY_CHANNEL):
- "callmebot": free WhatsApp messages to your own number via CallMeBot (personal use).
- "telegram": free Telegram bot message.
- "whatsapp_cloud": Meta WhatsApp Cloud API (official, paid per template message).

WhatsApp Cloud API has two modes:
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
    """Human-readable alert. Fields the email doesn't mention are left out."""
    from app.classifier.claude_service import NOT_MENTIONED

    def line(label: str, value: str) -> list[str]:
        value = _clean_param(value)
        return [] if not value or value == "-" or value == NOT_MENTIONED else [f"{label}: {value}"]

    lines = ["*College Placement Alert*", "", f"*{_clean_param(f'{c.priority} · {c.category_label.upper()}')}*"]
    lines += line("Company", c.company)
    lines += line("Role", c.role)
    lines += line("Eligibility", c.eligibility)
    lines += line("Registration deadline", c.registration_deadline)
    lines += line("Date / time", c.event_date_time)
    lines += line("Venue / link", c.venue_or_link)
    lines += line("Next action", c.action_required)
    lines += [""] + line("Summary", c.summary)
    lines += ["", f"Subject: {_clean_param(email.subject)}", f"Original email: {_clean_param(email.link)}"]
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


def _request_with_retries(do_request) -> httpx.Response:
    last_error = ""
    for attempt in range(4):
        try:
            r = do_request()
        except httpx.TransportError as e:
            last_error = f"network error: {type(e).__name__}"
        else:
            if r.status_code < 300:
                return r
            last_error = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code < 500 and r.status_code != 429:
                break  # bad request / auth error: retrying will not help
        time.sleep(2**attempt)
    raise WhatsAppError(last_error)


def send_text(text: str) -> str:
    """Send a plain text message on the configured free channel (also used for test messages)."""
    s = get_settings()
    with httpx.Client(timeout=30) as client:
        if s.notify_channel == "callmebot":
            if not (s.callmebot_phone and s.callmebot_apikey):
                raise WhatsAppError("CallMeBot is not configured (CALLMEBOT_PHONE, CALLMEBOT_APIKEY)")
            r = _request_with_retries(
                lambda: client.get(
                    "https://api.callmebot.com/whatsapp.php",
                    params={"phone": s.callmebot_phone, "text": text, "apikey": s.callmebot_apikey},
                )
            )
            if "error" in r.text.lower()[:300] and "queued" not in r.text.lower():
                raise WhatsAppError(f"CallMeBot rejected the message: {r.text[:200]}")
            return "callmebot"
        if s.notify_channel == "telegram":
            if not (s.telegram_bot_token and s.telegram_chat_id):
                raise WhatsAppError("Telegram is not configured (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")
            r = _request_with_retries(
                lambda: client.post(
                    f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage",
                    json={"chat_id": s.telegram_chat_id, "text": text.replace("*", ""), "disable_web_page_preview": True},
                )
            )
            return str(r.json()["result"]["message_id"])
    raise WhatsAppError(f"send_text does not support channel {s.notify_channel!r}")


def send_alert(c: Classification, email: EmailMessage, client: httpx.Client | None = None) -> str:
    """Send the alert and return a message ID. Retries temporary failures."""
    s = get_settings()
    if s.dry_run:
        log.info("[DRY RUN] alert:\n%s", render_text(c, email))
        return "dry-run"
    if s.notify_channel in ("callmebot", "telegram"):
        return send_text(render_text(c, email))
    if not (s.whatsapp_token and s.whatsapp_phone_number_id and s.whatsapp_recipient):
        raise WhatsAppError("WhatsApp is not configured (token, phone number ID, recipient)")

    url = f"https://graph.facebook.com/{s.whatsapp_api_version}/{s.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {s.whatsapp_token}"}
    payload = _payload(c, email)
    own_client = client is None
    client = client or httpx.Client(timeout=20)
    try:
        r = _request_with_retries(lambda: client.post(url, json=payload, headers=headers))
        return r.json()["messages"][0]["id"]
    finally:
        if own_client:
            client.close()
