"""One-time Gmail authorization (read-only).

1. Download your OAuth client (Desktop app) JSON from Google Cloud Console to secrets/client_secret.json
2. Run:  python scripts/authorize_gmail.py
3. A browser opens; sign in with the personal Gmail account to monitor and allow read-only access.
4. The token is written to secrets/gmail_token.json. Store its contents as the GMAIL_TOKEN_JSON
   secret in production. Never commit it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

from app.gmail.oauth import SCOPES  # noqa: E402

CLIENT_SECRET = Path("secrets/client_secret.json")
TOKEN_OUT = Path("secrets/gmail_token.json")


def main() -> None:
    if not CLIENT_SECRET.exists():
        sys.exit(f"Missing {CLIENT_SECRET}. Download the OAuth client JSON from Google Cloud Console.")
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    TOKEN_OUT.parent.mkdir(exist_ok=True)
    TOKEN_OUT.write_text(creds.to_json())
    print(f"Saved read-only Gmail token to {TOKEN_OUT}")


if __name__ == "__main__":
    main()
