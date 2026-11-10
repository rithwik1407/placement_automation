"""Try the Claude classifier on a sample email without Gmail or WhatsApp.

    python scripts/try_classifier.py tests/samples/placement_drive.txt

The sample file's first lines are headers (From:, Subject:), then a blank line, then the body.
Prints the extracted JSON and the WhatsApp alert text it would send. Needs ANTHROPIC_API_KEY.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.classifier.claude_service import classify_email  # noqa: E402
from app.gmail.email_reader import EmailMessage  # noqa: E402
from app.whatsapp.notifier import render_text  # noqa: E402


def load_sample(path: Path) -> EmailMessage:
    head, _, body = path.read_text().partition("\n\n")
    headers = dict(line.split(":", 1) for line in head.splitlines() if ":" in line)
    return EmailMessage(
        id="sample-" + path.stem,
        thread_id="",
        from_header=headers.get("From", "").strip(),
        subject=headers.get("Subject", "").strip(),
        date=headers.get("Date", "").strip(),
        body=body.strip(),
    )


def main() -> None:
    email = load_sample(Path(sys.argv[1]))
    result = classify_email(email)
    print(result.model_dump_json(indent=2))
    if result.is_placement_related:
        print("\n--- WhatsApp alert ---\n" + render_text(result, email))
    else:
        print("\nNot placement-related: no alert would be sent.")


if __name__ == "__main__":
    main()
