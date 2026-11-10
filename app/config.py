"""Application settings, loaded from environment variables (or a local .env file)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    # --- Database ---
    database_url: str = "sqlite:///./placement_agent.db"  # use postgresql+psycopg://... in production

    # --- Gmail ---
    # Path to the OAuth token JSON produced by scripts/authorize_gmail.py,
    # or the JSON itself in GMAIL_TOKEN_JSON (preferred on Cloud Run via Secret Manager).
    gmail_token_file: str = "secrets/gmail_token.json"
    gmail_token_json: str | None = None
    gmail_user_id: str = "me"
    # Full Pub/Sub topic name, e.g. projects/my-project/topics/gmail-placement
    gmail_pubsub_topic: str = ""
    # Gmail web link template for the "open original email" button.
    gmail_link_template: str = "https://mail.google.com/mail/u/0/#all/{message_id}"

    # --- Pub/Sub push authentication ---
    # Pub/Sub push subscriptions can attach a Google-signed OIDC token. We verify it.
    pubsub_verify_token: bool = True
    pubsub_audience: str = ""  # usually your push endpoint URL
    pubsub_service_account_email: str = ""  # the service account set on the push subscription

    # Shared secret for /tasks/* endpoints (called by Cloud Scheduler).
    scheduler_secret: str = ""

    # --- Sender allowlist ---
    placement_senders_file: str = str(ROOT_DIR / "config" / "placement_senders.json")

    # --- Claude ---
    anthropic_api_key: str | None = None  # the SDK also reads ANTHROPIC_API_KEY itself
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "medium"
    # Email bodies above this many characters are cut before classification (placement mails are
    # normally far shorter; this only guards against huge HTML newsletters slipping through).
    max_email_chars: int = 40_000

    # --- WhatsApp Cloud API ---
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_recipient: str = ""  # your personal number in international format, digits only, e.g. 9198xxxxxxxx
    whatsapp_api_version: str = "v23.0"
    # "template" works any time (needs an approved template); "text" only works inside
    # the 24-hour customer-service window (i.e. right after you message the business number).
    whatsapp_mode: str = "template"
    whatsapp_template_name: str = "placement_alert"
    whatsapp_template_language: str = "en"

    # When true, alerts are logged instead of sent (useful for local testing).
    dry_run: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
