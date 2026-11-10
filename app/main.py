"""FastAPI app.

Endpoints
- POST /pubsub/gmail      Pub/Sub push endpoint for Gmail notifications (new mail).
- POST /tasks/renew-watch Called daily by Cloud Scheduler to renew the Gmail watch.
- POST /tasks/sync        Called every ~15 min by Cloud Scheduler: catches missed pushes
                          and retries failed alerts.
- GET  /healthz
"""

import base64
import hmac
import json
import logging

from fastapi import Depends, FastAPI, Header, HTTPException, Request

from app.config import get_settings
from app.database.models import init_db
from app.gmail.oauth import gmail_service
from app.gmail.watcher import new_message_ids_since_last_sync, recent_message_ids, start_watch
from app.services import deduplication
from app.services.pipeline import process_many, summarize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("placement-agent")

app = FastAPI(title="College Placement Email Alert Agent")


@app.on_event("startup")
def _startup() -> None:
    init_db()


def verify_pubsub(authorization: str | None = Header(default=None)) -> None:
    """Verify the Google-signed OIDC token that Pub/Sub attaches to push requests."""
    s = get_settings()
    if not s.pubsub_verify_token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    from google.auth.transport import requests as g_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(
            authorization.removeprefix("Bearer "), g_requests.Request(), audience=s.pubsub_audience or None
        )
    except ValueError as e:
        raise HTTPException(401, f"invalid token: {e}") from e
    if s.pubsub_service_account_email and claims.get("email") != s.pubsub_service_account_email:
        raise HTTPException(403, "unexpected push service account")


def verify_scheduler(x_scheduler_secret: str | None = Header(default=None)) -> None:
    secret = get_settings().scheduler_secret
    if not secret or not x_scheduler_secret or not hmac.compare_digest(secret, x_scheduler_secret):
        raise HTTPException(401, "bad scheduler secret")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/pubsub/gmail", dependencies=[Depends(verify_pubsub)])
async def pubsub_gmail(request: Request):
    envelope = await request.json()
    data = envelope.get("message", {}).get("data")
    if data:
        notice = json.loads(base64.b64decode(data))
        log.info("Gmail change for %s, historyId %s", notice.get("emailAddress"), notice.get("historyId"))
    service = gmail_service()
    ids = new_message_ids_since_last_sync(service)
    results = process_many(service, ids)
    log.info("push processed %d message(s): %s", len(ids), summarize(results))
    # Always 2xx so Pub/Sub does not redeliver; failures are recorded and retried by /tasks/sync.
    return {"processed": len(ids)}


@app.post("/tasks/renew-watch", dependencies=[Depends(verify_scheduler)])
def renew_watch():
    resp = start_watch(gmail_service())
    return {"historyId": resp.get("historyId"), "expiration": resp.get("expiration")}


@app.post("/tasks/sync", dependencies=[Depends(verify_scheduler)])
def sync():
    service = gmail_service()
    released = deduplication.release_stale()
    ids = new_message_ids_since_last_sync(service)
    # Safety net: re-check the last day's inbox; already-processed IDs are skipped by dedup.
    for mid in recent_message_ids(service, query="newer_than:1d"):
        if mid not in ids:
            ids.append(mid)
    for mid in deduplication.retryable_ids():
        if mid not in ids:
            ids.append(mid)
    results = process_many(service, ids)
    return {"checked": len(ids), "released_stale": released, "results": json.loads(summarize(results))}
