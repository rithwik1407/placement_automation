"""Fetch a Gmail message and turn it into plain text the classifier can read."""

import base64
import html
import re
from dataclasses import dataclass, field

from app.config import get_settings


@dataclass
class EmailMessage:
    id: str
    thread_id: str
    from_header: str
    subject: str
    date: str
    body: str
    auth_results: list[str] = field(default_factory=list)
    label_ids: list[str] = field(default_factory=list)

    @property
    def link(self) -> str:
        return get_settings().gmail_link_template.format(message_id=self.id, thread_id=self.thread_id)


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", raw)
    raw = re.sub(r"(?i)</t[dh]>", " | ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(raw)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _collect_parts(part: dict, plain: list[str], htmls: list[str], attachments: list[str]) -> None:
    mime = part.get("mimeType", "")
    body = part.get("body", {})
    if part.get("filename"):
        attachments.append(part["filename"])
    elif mime == "text/plain" and body.get("data"):
        plain.append(_decode(body["data"]))
    elif mime == "text/html" and body.get("data"):
        htmls.append(_decode(body["data"]))
    for sub in part.get("parts", []) or []:
        _collect_parts(sub, plain, htmls, attachments)


def parse_message(msg: dict) -> EmailMessage:
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])

    def header(name: str) -> str:
        return next((h["value"] for h in headers if h["name"].lower() == name.lower()), "")

    plain: list[str] = []
    htmls: list[str] = []
    attachments: list[str] = []
    _collect_parts(payload, plain, htmls, attachments)
    body = "\n".join(plain).strip() or _html_to_text("\n".join(htmls)) or msg.get("snippet", "")
    if attachments:
        body += "\n\n[Attachments (not read): " + ", ".join(attachments) + "]"

    return EmailMessage(
        id=msg["id"],
        thread_id=msg.get("threadId", ""),
        from_header=header("From"),
        subject=header("Subject"),
        date=header("Date"),
        body=body,
        auth_results=[h["value"] for h in headers if h["name"].lower() == "authentication-results"],
        label_ids=msg.get("labelIds", []),
    )


def fetch_metadata(service, message_id: str) -> dict:
    """Cheap fetch of headers only, so non-allowlisted mail is never downloaded in full."""
    user = get_settings().gmail_user_id
    return (
        service.users()
        .messages()
        .get(
            userId=user,
            id=message_id,
            format="metadata",
            metadataHeaders=["From", "Subject", "Date", "Authentication-Results"],
        )
        .execute()
    )


def fetch_message(service, message_id: str) -> EmailMessage:
    user = get_settings().gmail_user_id
    msg = service.users().messages().get(userId=user, id=message_id, format="full").execute()
    return parse_message(msg)
