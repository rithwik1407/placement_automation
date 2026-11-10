"""Check Gmail once for new placement-cell emails and send alerts. Used by the free
GitHub Actions setup (every 5 minutes), but also works from cron on any machine.

    python scripts/poll_once.py            # normal run
    python scripts/poll_once.py --test     # just send a test message to check the channel

Only counts are printed: GitHub Actions logs of a public repo are public, so no email
content, subjects or addresses are ever logged here.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database.models import get_state, init_db, set_state  # noqa: E402
from app.gmail.email_reader import fetch_metadata  # noqa: E402
from app.gmail.oauth import gmail_service  # noqa: E402
from app.gmail.watcher import recent_message_ids  # noqa: E402
from app.services import deduplication  # noqa: E402
from app.services.pipeline import process_many, summarize  # noqa: E402
from app.services.sender_verification import get_allowlist  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

# On the very first run (or if the saved state was lost), emails older than this are
# recorded as already seen instead of alerted, so you don't get a flood of old alerts.
BASELINE_MINUTES = 90


def build_query() -> str:
    a = get_allowlist()
    senders = sorted(a.addresses) + [f"@{d}" for d in sorted(a.domains)]
    return f"from:({' OR '.join(senders)}) newer_than:3d"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="send a test message and exit")
    args = parser.parse_args()

    if args.test:
        from app.whatsapp.notifier import send_text

        send_text(
            "College Placement Alert: test message. If you can read this, alerts are set up correctly."
        )
        print("test message sent")
        return

    init_db()
    service = gmail_service()
    ids = recent_message_ids(service, query=build_query())

    if get_state("initialized") is None:
        cutoff_ms = (time.time() - BASELINE_MINUTES * 60) * 1000
        baselined = 0
        for mid in ids:
            if int(fetch_metadata(service, mid).get("internalDate", 0)) < cutoff_ms and deduplication.claim(mid):
                deduplication.finish(mid, "baseline")
                baselined += 1
        set_state("initialized", "1")
        print(f"first run: {baselined} older email(s) marked as already seen")

    deduplication.release_stale()
    for mid in deduplication.retryable_ids():
        if mid not in ids:
            ids.append(mid)
    results = process_many(service, ids)
    new = {k: v for k, v in results.items() if v != "duplicate"}
    print(f"checked {len(ids)} email(s); new: {summarize(new)}")
    if any(v == "failed" for v in new.values()):
        sys.exit(1)  # makes the failed run visible in GitHub; it is retried next run


if __name__ == "__main__":
    main()
