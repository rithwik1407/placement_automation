"""Free, offline classifier: keyword rules + labelled-field extraction. No API key needed.

It is deliberately conservative about extraction: a field is only filled when the email
labels it explicitly ("Company: ...", "Last date: ...") or a deadline sentence contains a
clear date. Everything else is "Not mentioned in email", and the alert includes a short
excerpt of the email so nothing important is lost.
"""

import re

from app.classifier.claude_service import NOT_MENTIONED, Classification
from app.gmail.email_reader import EmailMessage

# Checked in this order; the first category with a hit wins.
CATEGORY_PATTERNS: list[tuple[str, list[str]]] = [
    ("RESULT", [r"\bfinal(ly)? selected\b", r"\bselected (students|candidates)\b", r"\boffer letters?\b",
                r"\bresults? (of|for)\b", r"\bcongratulations\b", r"\bplaced students\b"]),
    ("SHORTLIST", [r"\bshort[\s-]?listed\b", r"\bshort[\s-]?list\b", r"\bselected for (the )?next round\b",
                   r"\bcleared (the )?(online )?(test|assessment|round)\b"]),
    ("INTERVIEW_SCHEDULE", [r"\binterviews?\b", r"\bhr round\b", r"\btechnical round\b",
                            r"\bgroup discussion\b", r"\b(gd|pi) round\b"]),
    ("ASSESSMENT", [r"\bonline (test|assessment|exam)\b", r"\bassessments?\b", r"\bcoding (round|test|challenge)\b",
                    r"\baptitude\b", r"\btest link\b", r"\bhackerrank\b", r"\bhackerearth\b", r"\bmettl\b",
                    r"\bamcat\b", r"\bcocubes\b"]),
    ("PRE_PLACEMENT_TALK", [r"\bpre[\s-]?placement talk\b", r"\bppt\b", r"\bcompany presentation\b"]),
    ("PLACEMENT_DRIVE", [r"\b(campus|placement|recruitment|hiring|off[\s-]?campus|pool[\s-]?campus) drive\b",
                         r"\bcampus (recruitment|placement|hiring)\b", r"\brecruitment\b", r"\bhiring\b",
                         r"\bjob (opening|opportunity|role)\b", r"\binternship (opportunity|drive|offer)\b",
                         r"\bctc\b", r"\bstipend\b", r"\bpackage\b.*\blpa\b", r"\blpa\b", r"\bjob description\b"]),
    ("REGISTRATION", [r"\bregistrations?\b", r"\bregister\b", r"\bapply (now|before|by)\b", r"\blast date\b",
                      r"\bdeadline\b", r"\bgoogle form\b", r"forms\.gle/"]),
    ("ELIGIBILITY", [r"\beligib(le|ility)\b", r"\bcgpa\b", r"\bbacklogs?\b"]),
]

# Emails matching these and none of the strong placement words above are ignored.
IGNORE_PATTERNS = [r"\bfest\b", r"\bfee(s)? (payment|due)\b", r"\bnewsletter\b", r"\bholiday\b",
                   r"\bexam(ination)? (time ?table|schedule)\b", r"\bunsubscribe\b"]

MONTHS = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
DATE_RE = re.compile(
    rf"(\b\d{{1,2}}(st|nd|rd|th)?[\s\-]*{MONTHS}\.?,?(\s*\d{{4}})?"
    rf"|\b{MONTHS}\.?\s+\d{{1,2}}(st|nd|rd|th)?,?(\s*\d{{4}})?"
    r"|\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b"
    r"|\b(today|tomorrow)\b)",
    re.I,
)
TIME_RE = re.compile(r"\b\d{1,2}([:.]\d{2})?\s*(am|pm|a\.m\.|p\.m\.|hrs)\b", re.I)
LINK_RE = re.compile(r"https?://[^\s<>\")\]]+", re.I)

LABELS = {
    "company": r"(company|organi[sz]ation|company name|recruiter)",
    "role": r"(role|job role|designation|position|profile|job title)",
    "eligibility": r"(eligibility( criteria)?|eligible (branches|streams|courses)|criteria)",
    "registration_deadline": r"(last date( (to|for) (register|registration|apply))?|deadline|registration (closes|deadline|ends)|apply by)",
    "event_date_time": r"(date of (the )?(drive|test|exam|interview|assessment|ppt)|(drive|test|interview|assessment|exam) date|date ?(&|and) ?time|date|schedule)",
    "venue_or_link": r"(venue|location|mode|meeting link|test link|registration link|link)",
}


def _labelled(text: str, label: str) -> str | None:
    m = re.search(rf"(?im)^\s*[*•\-]?\s*{label}\s*[:\-–]\s*(.+)$", text)
    if m:
        value = m.group(m.lastindex).strip(" *\t")
        return value[:300] if value else None
    return None


def _deadline_sentence(text: str) -> str | None:
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        s = sentence.strip()
        if re.search(r"\b(before|by|last date|deadline|on or before|closes?|till|until)\b", s, re.I) and re.search(
            r"\b(regist|apply|submit|fill)", s, re.I
        ):
            if DATE_RE.search(s):
                return s[:300]
            return "Unclear: " + s[:280]
    return None


def _excerpt(body: str, limit: int = 350) -> str:
    text = re.sub(r"\s+", " ", body)
    text = re.sub(r"^(dear|hello|hi)\s+(all|students?|candidates?)[,!]?\s*", "", text, flags=re.I)
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def classify_email_rules(email: EmailMessage) -> Classification:
    text = f"{email.subject}\n{email.body}"
    lower = text.lower()

    category = "NOT_PLACEMENT"
    hits = 0
    for cat, patterns in CATEGORY_PATTERNS:
        n = sum(1 for p in patterns if re.search(p, lower))
        if n and category == "NOT_PLACEMENT":
            category = cat
        hits += n

    ignored = any(re.search(p, lower) for p in IGNORE_PATTERNS)
    strong = category not in ("NOT_PLACEMENT", "REGISTRATION", "ELIGIBILITY")
    if ignored and not strong:
        category = "NOT_PLACEMENT"
    is_placement = category != "NOT_PLACEMENT"

    fields = {k: _labelled(email.body, v) for k, v in LABELS.items()}
    if not fields["registration_deadline"]:
        fields["registration_deadline"] = _deadline_sentence(email.body)
    if not fields["venue_or_link"]:
        link = LINK_RE.search(email.body)
        fields["venue_or_link"] = link.group(0) if link else None
    if not fields["event_date_time"] and category in ("ASSESSMENT", "INTERVIEW_SCHEDULE", "PRE_PLACEMENT_TALK"):
        d = DATE_RE.search(email.body)
        if d:
            t = TIME_RE.search(email.body, d.start())
            fields["event_date_time"] = d.group(0) + (f", {t.group(0)}" if t and t.start() - d.end() < 40 else "")

    action = {
        "PLACEMENT_DRIVE": "Read the drive details and register if eligible.",
        "REGISTRATION": "Complete the registration before the deadline.",
        "ELIGIBILITY": "Check whether you meet the eligibility criteria.",
        "SHORTLIST": "Check whether your name is on the shortlist.",
        "ASSESSMENT": "Prepare for and attend the assessment on time.",
        "INTERVIEW_SCHEDULE": "Check your interview slot and be ready on time.",
        "PRE_PLACEMENT_TALK": "Attend the pre-placement talk.",
        "RESULT": "Check the result and any next steps in the email.",
    }.get(category, NOT_MENTIONED)

    return Classification(
        is_placement_related=is_placement,
        category=category,
        company=fields["company"] or NOT_MENTIONED,
        role=fields["role"] or NOT_MENTIONED,
        eligibility=fields["eligibility"] or NOT_MENTIONED,
        registration_deadline=fields["registration_deadline"] or NOT_MENTIONED,
        event_date_time=fields["event_date_time"] or NOT_MENTIONED,
        venue_or_link=fields["venue_or_link"] or NOT_MENTIONED,
        action_required=action,
        summary=_excerpt(email.body),
        confidence=min(1.0, 0.4 + 0.15 * hits) if is_placement else 0.6,
    )
