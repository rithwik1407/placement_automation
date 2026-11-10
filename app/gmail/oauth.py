"""Gmail OAuth credentials (read-only scope only)."""

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import get_settings

# The agent never sends, deletes or modifies mail.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def load_credentials() -> Credentials:
    settings = get_settings()
    if settings.gmail_token_json:
        info = json.loads(settings.gmail_token_json)
    else:
        path = Path(settings.gmail_token_file)
        if not path.exists():
            raise RuntimeError(
                f"Gmail token not found at {path}. Run `python scripts/authorize_gmail.py` first."
            )
        info = json.loads(path.read_text())
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    return creds


def gmail_service():
    return build("gmail", "v1", credentials=load_credentials(), cache_discovery=False)
