# College Placement Email Alert Agent

Watches your **personal Gmail** for emails from your **official college placement cell only**, uses
**Claude** to decide whether each one is a placement update and to pull out the details, and sends you
an **immediate WhatsApp alert** for every relevant email.

It only reads mail (`gmail.readonly`). It never replies to emails, submits forms or applies to jobs.

```
New mail in Gmail
   │  Gmail push notification (Pub/Sub)
   ▼
FastAPI /pubsub/gmail ── History API: which messages are new?
   │
   ├─ already processed (Gmail message ID in DB)? ──────────► skip, no duplicate alert
   ├─ sender not on allowlist, or DKIM/DMARC fails? ────────► skip (email body never downloaded)
   ▼
Claude classifier (structured JSON)
   ├─ not placement-related (fest, fees, ads, newsletters) ──► no alert
   ▼
WhatsApp Cloud API template message ──► your phone
```

Cloud Scheduler calls two more endpoints: `/tasks/renew-watch` daily (Gmail watches expire after
7 days) and `/tasks/sync` every 15 minutes (catches missed pushes and retries failed alerts).

## Free setup (recommended): GitHub Actions + CallMeBot, no paid services

| Piece | Free option used | Paid option (optional) |
|---|---|---|
| Runs the agent | GitHub Actions every 5 min (`.github/workflows/poll.yml`), free on public repos | Cloud Run + Pub/Sub push (instant) |
| Classifier | Keyword rules (`CLASSIFIER=rules`) | Claude API (`CLASSIFIER=claude`) |
| WhatsApp | CallMeBot free personal WhatsApp API (`NOTIFY_CHANNEL=callmebot`) | Meta WhatsApp Cloud API |
| Backup channel | Telegram bot (`NOTIFY_CHANNEL=telegram`) | |
| Database | SQLite file kept in the Actions cache (message IDs and statuses only) | PostgreSQL |
| Gmail | Gmail API, read-only (free; Google Cloud project without billing) | |

Trade-offs: alerts arrive within about 5 to 15 minutes instead of instantly (GitHub delays scheduled
runs), and the keyword classifier fills only fields the email labels clearly ("Company:", "Last date:");
everything else says "Not mentioned in email", and every alert includes a short excerpt of the email.

Steps:

1. **CallMeBot API key.** On callmebot.com, open the WhatsApp API page: save the
   bot's number in your contacts, send it `I allow callmebot to send me messages` on WhatsApp, and it
   replies with your API key. (If the page says the bot is full, use Telegram instead: create a bot with
   @BotFather, send it any message, and get your chat ID from
   `https://api.telegram.org/bot<TOKEN>/getUpdates`.)
2. **Gmail token.** Create a Google Cloud project (no billing needed), enable the Gmail API, set up the
   OAuth consent screen (External, publish it "In production"), create a **Desktop app** OAuth client,
   save it as `secrets/client_secret.json`, then on your computer run
   `pip install -r requirements.txt && python scripts/authorize_gmail.py` and sign in with the Gmail
   account to monitor. Copy the whole content of `secrets/gmail_token.json`.
3. **GitHub secrets.** In the repo: Settings → Secrets and variables → Actions → New repository secret:
   `GMAIL_TOKEN_JSON`, `CALLMEBOT_PHONE` (e.g. `919xxxxxxxxx`), `CALLMEBOT_APIKEY`
   (or `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` plus a repository *variable* `NOTIFY_CHANNEL=telegram`).
   Secrets are never shown in logs, even on a public repo.
4. **Test.** Actions → Placement alerts → Run workflow → tick "Only send a test message". You should get
   a WhatsApp (or Telegram) message within a minute.
5. **Turn it on.** Add a repository *variable* `ALERTS_ENABLED` = `true` (same settings page, Variables
   tab). Until then the 5-minute schedule does nothing, so it doesn't fail before setup is finished.
   From then on it runs every 5 minutes. On its first run it marks placement emails older
   than 90 minutes as already seen, so you are not flooded with old alerts.

Logs on a public repo are public, so the polling script prints only counts, never subjects or senders.

The rest of this README covers the original paid, instant setup (Cloud Run, Pub/Sub, Claude, Meta
WhatsApp Cloud API). You don't need it for the free setup.

## Near-instant alerts (free): Google Apps Script

GitHub Actions can only check every 5 minutes (often later). For alerts within about a minute,
run the same rules inside your Google account with Apps Script: [`apps_script/Code.gs`](apps_script/Code.gs).

1. Open https://script.google.com while signed in to the Gmail account to monitor → **New project**.
2. Replace the contents of `Code.gs` with `apps_script/Code.gs` from this repo → Save.
3. **Project Settings** (gear) → **Script properties** → add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. In the editor, pick the function **`setup`** → **Run** → allow the permissions (Google shows an
   "unverified app" screen for your own script: Advanced → Go to project).
   You get a Telegram test message, and from then on Gmail is checked every minute.
5. Optional: run **`resendRecent`** to re-send alerts for the last 3 days.
6. Turn off the GitHub schedule so you don't get each alert twice: set the repository variable
   `ALERTS_ENABLED` to `false`.

Truly instant (push) delivery needs the paid Cloud Run + Pub/Sub setup described below.

## What gets alerted

| Category | Priority shown |
|---|---|
| Placement drive | URGENT |
| Registration open / closing / extended | URGENT |
| Shortlist | URGENT |
| Interview schedule | URGENT |
| Result / offer update | URGENT |
| Online test / assessment | HIGH |
| Pre-placement talk | HIGH |
| Eligibility criteria | HIGH |

Every one of these is sent immediately. Anything else from the placement cell (fests, fee notices,
academic notices, course promotions) is ignored. Missing details are shown as
"Not mentioned in email", and an ambiguous deadline is shown as "Unclear: <the email's words>".

Example alert:

```
College Placement Alert

URGENT · NEW PLACEMENT DRIVE
Company: Example Technologies
Role: Software Development Engineer
Eligibility: B.Tech CSE, IT, ECE (2027 batch), CGPA 7.0+, no active backlogs
Registration deadline: 13 November 2026, 5:00 PM
Date / time: Online assessment 16 November 2026, 10:00 AM
Next action: Register on the placement portal before the deadline.

Subject: Campus Drive: Example Technologies - SDE ...
Original email (placement cell): https://mail.google.com/mail/u/0/#all/<id>
```

## Project layout

```
app/
  main.py                      FastAPI endpoints (Pub/Sub push, scheduler tasks, health)
  config.py                    Settings from environment variables
  gmail/oauth.py               Read-only Gmail credentials
  gmail/watcher.py             users.watch + History API sync
  gmail/email_reader.py        Fetch message, headers, plain-text body
  classifier/claude_service.py Claude prompt, JSON schema, priorities
  whatsapp/notifier.py         WhatsApp Cloud API sender (template or text) with retries
  services/sender_verification.py  Allowlist + DKIM/DMARC check
  services/deduplication.py    Gmail-message-ID dedup, failure tracking, retries
  services/pipeline.py         The end-to-end flow for one message
  database/models.py           processed_emails (audit trail) and sync_state tables
config/placement_senders.json  Official placement-cell senders (EDIT THIS)
scripts/authorize_gmail.py     One-time Gmail OAuth
scripts/try_classifier.py      Try Claude on a sample email, no Gmail/WhatsApp needed
tests/                         Offline tests (pytest)
```

## Connections you need

| # | Connection | What you get | Goes into |
|---|---|---|---|
| 1 | Google Cloud project with Gmail API + Pub/Sub | OAuth client, Pub/Sub topic | `secrets/client_secret.json`, `GMAIL_PUBSUB_TOPIC` |
| 2 | Your personal Gmail, authorized read-only | OAuth token | `GMAIL_TOKEN_JSON` |
| 3 | Anthropic Claude API (console.anthropic.com, with API credits) | API key | `ANTHROPIC_API_KEY` |
| 4 | Meta WhatsApp Business Platform (Cloud API) | Phone number ID, permanent token, approved template | `WHATSAPP_*` |
| 5 | Hosting (Cloud Run recommended) + PostgreSQL | Public HTTPS URL, DB URL | `DATABASE_URL`, `PUBSUB_AUDIENCE` |

## Setup

### 1. Set the official placement-cell senders

Edit `config/placement_senders.json`. The values there now are **placeholders**.

```json
{
  "addresses": ["placements@yourcollege.ac.in"],
  "domains": [],
  "require_authentication": true
}
```

- Use `addresses` for exact mailboxes (safest). Use `domains` only if the placement cell sends from many
  addresses on its own subdomain; allowing the whole college domain would also let in fest and fee emails
  (Claude would still filter those, but you spend API calls on them).
- `require_authentication: true` requires Gmail's DKIM or DMARC check to pass for that domain, so a spoofed
  "From: placements@yourcollege..." is rejected. If the placement cell's mail reaches you through a
  forwarder or Google Group and gets skipped, check the `processed_emails.error` column; only then
  consider setting it to `false`.

### 2. Local install and offline tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in later steps
pytest -q
```

### 3. Google Cloud: Gmail API, OAuth, Pub/Sub

```bash
gcloud projects create placement-alerts-XXXX   # or use an existing project
gcloud config set project placement-alerts-XXXX
gcloud services enable gmail.googleapis.com pubsub.googleapis.com run.googleapis.com \
    cloudscheduler.googleapis.com secretmanager.googleapis.com

# Topic Gmail publishes to, and permission for Gmail to publish
gcloud pubsub topics create gmail-placement
gcloud pubsub topics add-iam-policy-binding gmail-placement \
    --member=serviceAccount:gmail-api-push@system.gserviceaccount.com --role=roles/pubsub.publisher
```

OAuth client: Console → **APIs & Services → OAuth consent screen** → External, add yourself as a test
user, add scope `.../auth/gmail.readonly`. Then **Credentials → Create credentials → OAuth client ID →
Desktop app**, download the JSON to `secrets/client_secret.json`.

> While the consent screen is in "Testing" mode, Google expires refresh tokens after 7 days. For a
> personal tool, publish the app ("In production"; you will see an unverified-app warning for your own
> account, which is fine) so the token keeps working.

Authorize your personal Gmail:

```bash
python scripts/authorize_gmail.py      # opens a browser; writes secrets/gmail_token.json
```

### 4. Claude API

Create an API key at console.anthropic.com (API credits are separate from a Claude.ai subscription) and
set `ANTHROPIC_API_KEY`. Try the classifier on the sample emails:

```bash
python scripts/try_classifier.py tests/samples/placement_drive.txt   # → alert text
python scripts/try_classifier.py tests/samples/newsletter.txt        # → no alert
```

The classifier uses `claude-opus-5-5` with structured JSON output, and server-side fallback enabled so a
rare safety-classifier decline is retried on another model instead of failing. Model and effort are set
with `CLAUDE_MODEL` / `CLAUDE_EFFORT`. Only emails from verified placement-cell senders are ever sent to
Claude.

### 5. WhatsApp Cloud API

1. In Meta for Developers, create an app of type **Business** and add the **WhatsApp** product. This
   creates (or links) a Meta business portfolio and a WhatsApp Business Account.
2. Add a sender phone number (a number not already registered on regular WhatsApp), or use the test
   number while developing. Note the **Phone number ID**.
3. Add your personal WhatsApp number as a recipient (with the test number, add it under "To" and verify it).
4. Create a **System User** in Business Settings, give it the app and WhatsApp account, and generate a
   permanent token with `whatsapp_business_messaging` permission → `WHATSAPP_TOKEN`.
5. In WhatsApp Manager → **Message templates**, create a template:
   - Name: `placement_alert`, Category: **Utility**, Language: **English** (`en`)
   - Header (text): `College Placement Alert`
   - Body:
     ```
     *{{1}}*
     Company: {{2}}
     Role: {{3}}
     Eligibility: {{4}}
     Registration deadline: {{5}}
     Date / time: {{6}}
     Next action: {{7}}
     Subject: {{8}}
     Original email: {{9}}
     Sent by your placement alert agent. Always confirm details in the original email.
     ```
   - Provide sample values for each variable when submitting. Approval usually takes minutes to a day.
   If you name the template differently or the language code differs (e.g. `en_US`), set
   `WHATSAPP_TEMPLATE_NAME` / `WHATSAPP_TEMPLATE_LANGUAGE`.
6. Set `WHATSAPP_PHONE_NUMBER_ID` and `WHATSAPP_RECIPIENT` (country code + number, digits only).

Template messages are delivered any time. `WHATSAPP_MODE=text` sends a free-form message instead, but
WhatsApp only delivers those within 24 hours of you messaging the business number, so use it for tests only.

### 6. Database

Any PostgreSQL works (Cloud SQL, Neon, Supabase, Railway). Set
`DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dbname`. Tables are created on startup.
For local testing the default is a SQLite file.

### 7. Deploy to Cloud Run

Store secrets in Secret Manager (`gmail-token` = contents of `secrets/gmail_token.json`,
`anthropic-key`, `whatsapp-token`, `db-url`, `scheduler-secret`), then:

```bash
gcloud run deploy placement-agent --source . --region asia-south1 \
  --allow-unauthenticated --min-instances 0 --max-instances 2 \
  --set-secrets GMAIL_TOKEN_JSON=gmail-token:latest,ANTHROPIC_API_KEY=anthropic-key:latest,\
WHATSAPP_TOKEN=whatsapp-token:latest,DATABASE_URL=db-url:latest,SCHEDULER_SECRET=scheduler-secret:latest \
  --set-env-vars GMAIL_PUBSUB_TOPIC=projects/PROJECT/topics/gmail-placement,\
WHATSAPP_PHONE_NUMBER_ID=...,WHATSAPP_RECIPIENT=91XXXXXXXXXX,\
PUBSUB_AUDIENCE=https://SERVICE_URL/pubsub/gmail,\
PUBSUB_SERVICE_ACCOUNT_EMAIL=gmail-push@PROJECT.iam.gserviceaccount.com
```

(`--allow-unauthenticated` is needed so Pub/Sub and Scheduler can reach it; the app itself verifies the
Pub/Sub OIDC token and the scheduler secret on every request.)

### 8. Pub/Sub push subscription

```bash
gcloud iam service-accounts create gmail-push
gcloud pubsub subscriptions create gmail-placement-push --topic gmail-placement \
  --push-endpoint=https://SERVICE_URL/pubsub/gmail \
  --push-auth-service-account=gmail-push@PROJECT.iam.gserviceaccount.com \
  --push-auth-token-audience=https://SERVICE_URL/pubsub/gmail \
  --ack-deadline=120
```

### 9. Start the Gmail watch and schedule the jobs

```bash
# Start the watch now
curl -X POST https://SERVICE_URL/tasks/renew-watch -H "X-Scheduler-Secret: $SCHEDULER_SECRET"

# Renew daily, and run the safety-net sync every 15 minutes
gcloud scheduler jobs create http renew-gmail-watch --schedule "0 6 * * *" --time-zone Asia/Kolkata \
  --uri https://SERVICE_URL/tasks/renew-watch --http-method POST \
  --headers X-Scheduler-Secret=$SCHEDULER_SECRET --location asia-south1
gcloud scheduler jobs create http placement-sync --schedule "*/15 * * * *" \
  --uri https://SERVICE_URL/tasks/sync --http-method POST \
  --headers X-Scheduler-Secret=$SCHEDULER_SECRET --location asia-south1
```

The first sync looks back one day, so a placement email from the last 24 hours may produce an alert
right after deployment. That is expected.

### 10. Test end to end

1. Set `DRY_RUN=true` first: alerts are written to the logs instead of WhatsApp.
2. Ask a friend to send you a mail with a placement-style subject from a non-allowlisted address → it
   should show `skipped_sender` in `processed_emails`.
3. Wait for (or forward yourself a copy of) a real placement-cell email → `alerted` (forwarded copies come
   from you, not the placement cell, so they are correctly skipped; add your own address temporarily with
   `require_authentication: false` for a test, then remove it).
4. Set `DRY_RUN=false` and confirm the WhatsApp message arrives.

## Operating notes

- **Audit trail:** `processed_emails` has one row per message with status (`alerted`, `skipped_sender`,
  `not_placement`, `failed`), category, confidence, the extracted JSON, the WhatsApp message ID and any
  error. Non-allowlisted mail stores only the sender address and the reason.
- **No silent failures:** a failure is stored with its error and retried by `/tasks/sync` up to 5 times.
- **Duplicates:** the Gmail message ID is the primary key, so the same email never alerts twice, even if
  Pub/Sub redelivers or the sync job sees it again.
- **Privacy:** only verified placement-cell emails are downloaded in full and sent to Claude. Very long
  bodies are cut at `MAX_EMAIL_CHARS` (default 40,000 characters). Attachments are not read; their file
  names are listed in the text Claude sees.
- **Secrets:** never commit `.env` or `secrets/` (both are in `.gitignore`).
