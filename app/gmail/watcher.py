"""Gmail push notifications (users.watch) and history sync.

Gmail's Pub/Sub notification only says "the mailbox changed, historyId=N". We then ask the
History API what was added since the last historyId we processed.
"""

import logging

from googleapiclient.errors import HttpError

from app.config import get_settings
from app.database.models import get_state, set_state

log = logging.getLogger(__name__)

HISTORY_KEY = "gmail_last_history_id"
WATCH_EXPIRY_KEY = "gmail_watch_expiration"


def start_watch(service) -> dict:
    """Create/renew the Gmail watch. Must be renewed at least every 7 days; we renew daily."""
    settings = get_settings()
    if not settings.gmail_pubsub_topic:
        raise RuntimeError("GMAIL_PUBSUB_TOPIC is not set")
    resp = (
        service.users()
        .watch(
            userId=settings.gmail_user_id,
            body={
                "topicName": settings.gmail_pubsub_topic,
                "labelIds": ["INBOX"],
                "labelFilterBehavior": "INCLUDE",
            },
        )
        .execute()
    )
    set_state(WATCH_EXPIRY_KEY, str(resp.get("expiration", "")))
    if get_state(HISTORY_KEY) is None:
        set_state(HISTORY_KEY, str(resp["historyId"]))
    log.info("Gmail watch active until %s (historyId %s)", resp.get("expiration"), resp.get("historyId"))
    return resp


def new_message_ids_since_last_sync(service) -> list[str]:
    """Return IDs of messages added to the mailbox since the stored historyId,
    and advance the stored historyId."""
    settings = get_settings()
    start = get_state(HISTORY_KEY)
    if start is None:
        # First run: just record the current position; older mail is not alerted on.
        profile = service.users().getProfile(userId=settings.gmail_user_id).execute()
        set_state(HISTORY_KEY, str(profile["historyId"]))
        return []

    ids: list[str] = []
    latest = start
    page_token = None
    try:
        while True:
            resp = (
                service.users()
                .history()
                .list(
                    userId=settings.gmail_user_id,
                    startHistoryId=start,
                    historyTypes=["messageAdded"],
                    labelId="INBOX",
                    pageToken=page_token,
                )
                .execute()
            )
            for h in resp.get("history", []):
                for added in h.get("messagesAdded", []):
                    mid = added["message"]["id"]
                    if mid not in ids:
                        ids.append(mid)
            latest = resp.get("historyId", latest)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        if e.resp.status == 404:
            # historyId too old (Gmail keeps roughly a week). Fall back to a recent scan.
            log.warning("historyId %s expired; falling back to recent-message scan", start)
            ids = recent_message_ids(service, query="newer_than:2d")
            latest = service.users().getProfile(userId=settings.gmail_user_id).execute()["historyId"]
        else:
            raise

    set_state(HISTORY_KEY, str(latest))
    return ids


def recent_message_ids(service, query: str = "newer_than:1d") -> list[str]:
    """Fallback scan used by the periodic sync job, in case a push was missed."""
    settings = get_settings()
    ids: list[str] = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId=settings.gmail_user_id, q=query, labelIds=["INBOX"], pageToken=page_token, maxResults=100)
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            return ids
