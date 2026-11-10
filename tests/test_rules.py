"""Free rule-based classifier tests (no network)."""

from app.classifier.claude_service import NOT_MENTIONED
from app.classifier.rules import classify_email_rules
from app.gmail.email_reader import EmailMessage


def mail(subject, body):
    return EmailMessage("id1", "t1", "kabita_tnp@vnrvjiet.in", subject, "", body)


def test_drive_with_labelled_fields():
    c = classify_email_rules(mail(
        "Campus Drive: Example Technologies - SDE (2027 batch)",
        "Dear Students,\nCompany: Example Technologies\nRole: Software Development Engineer\n"
        "Eligibility: B.Tech CSE, IT, ECE, CGPA 7.0 and above\nCTC: 12 LPA\n"
        "Interested students must register before 13 November 2026, 5:00 PM using https://forms.gle/abc123\n",
    ))
    assert c.is_placement_related and c.category == "PLACEMENT_DRIVE" and c.priority == "URGENT"
    assert c.company == "Example Technologies"
    assert c.role == "Software Development Engineer"
    assert "CGPA 7.0" in c.eligibility
    assert "13 November 2026" in c.registration_deadline
    assert c.venue_or_link == "https://forms.gle/abc123"


def test_missing_fields_are_not_invented():
    c = classify_email_rules(mail("Hiring for interns", "A company is hiring interns. Details will follow."))
    assert c.is_placement_related
    assert c.company == NOT_MENTIONED and c.role == NOT_MENTIONED and c.registration_deadline == NOT_MENTIONED


def test_unclear_deadline():
    c = classify_email_rules(mail("Registration for XYZ drive", "Please register soon, the form closes shortly."))
    assert c.registration_deadline.startswith("Unclear:")


def test_shortlist_and_interview_and_result():
    assert classify_email_rules(mail("Shortlisted students - ABC Corp", "List attached.")).category == "SHORTLIST"
    i = classify_email_rules(mail("ABC Corp interview schedule", "Technical interviews on 18th Nov at 10:30 AM in Seminar Hall."))
    assert i.category == "INTERVIEW_SCHEDULE" and "18th Nov" in i.event_date_time and "10:30 AM" in i.event_date_time
    assert classify_email_rules(mail("Final selected candidates - ABC", "Congratulations to all.")).category == "RESULT"
    a = classify_email_rules(mail("Online test for DEF", "The online assessment is on 20 Nov, 2 PM. Test link will be shared."))
    assert a.category == "ASSESSMENT" and a.priority == "HIGH"


def test_non_placement_ignored():
    for subject, body in [
        ("Annual Cultural Fest - Volunteers needed", "The fest is from 20-22 Nov. Register with the student council."),
        ("Semester fee payment due", "Fee payment closes on 30 November."),
        ("Library timings changed", "The library will open at 9 AM."),
    ]:
        c = classify_email_rules(mail(subject, body))
        assert not c.is_placement_related, subject


def test_company_guessed_from_subject_and_body():
    cases = [
        ("Campus Drive: Example Technologies - SDE (2027 batch)", "", "Example Technologies"),
        ("Infosys Recruitment Drive 2027 Batch", "", "Infosys"),
        ("TCS Hiring - Ninja and Digital roles", "", "TCS"),
        ("Fwd: Registration for Deloitte USI - Analyst", "", "Deloitte USI"),
        ("Shortlisted students - Zoho Corporation", "", "Zoho Corporation"),
        ("Important: Placement update", "Dear Students,\nAccenture is visiting our campus on 20 Nov.", "Accenture"),
        ("Pool campus drive for 2027 batch", "Recruitment drive by Cognizant Technology Solutions for B.Tech students.",
         "Cognizant Technology Solutions"),
    ]
    for subject, body, expected in cases:
        c = classify_email_rules(mail(subject, body or "Recruitment drive details inside."))
        assert c.company == expected, (subject, c.company)


def test_company_not_invented():
    c = classify_email_rules(mail("Placement drive for 2027 batch", "Recruitment drive for B.Tech students. Register soon."))
    assert c.company == NOT_MENTIONED


def test_alert_omits_missing_fields():
    from app.whatsapp.notifier import render_text

    c = classify_email_rules(mail("Hiring for interns", "A company is hiring interns. Details will follow."))
    text = render_text(c, mail("Hiring for interns", ""))
    assert "Not mentioned" not in text and "Company:" not in text and "Summary:" in text
