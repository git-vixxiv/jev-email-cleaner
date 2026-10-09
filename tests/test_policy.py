from datetime import datetime, timezone

from jev_email_cleaner.config import Config
from jev_email_cleaner.email_record import parse_message
from jev_email_cleaner.judge import build_questions
from jev_email_cleaner.policy import DELETE, KEEP, REVIEW, decide

from .fakes import NOW_MS, gmail_message, scripted_answers

NOW = datetime.fromtimestamp(NOW_MS / 1000, timezone.utc)
QUESTIONS = {k: q.model_dump(mode="json") for k, q in build_questions(Config()).items()}


def run(subject, correspondents=frozenset(), **kw):
    rec = parse_message(gmail_message("1", "Sender <s@x.com>", subject, **kw), 500)
    answers = scripted_answers({"email": {"subject": subject}}, QUESTIONS)
    return decide(rec, answers, Config(), set(correspondents), NOW)


def test_old_marketing_is_deleted_and_archived():
    d = run("Big sale")
    assert d.action == DELETE and d.topic == "Promotions" and d.archive
    assert d.disposable > 0.9


def test_records_and_personal_mail_are_protected():
    assert run("Invoice 42").action == KEEP
    d = run("lunch friday?")
    assert d.action == KEEP and any("personally" in r for r in d.reasons)


def test_hard_protections_override_judgments():
    assert run("Big sale", labels=("INBOX", "STARRED")).action == KEEP
    assert run("Big sale", correspondents={"s@x.com"}).action == KEEP
    assert run("Big sale", attachment=True).action == KEEP
    # list mail with an attachment is not protected by the attachment rule
    assert run("Big sale", attachment=True, list_unsub="<https://x.com/u>").action == DELETE


def test_recent_mail_is_never_deleted():
    d = run("Big sale", age_days=5)
    assert d.action == KEEP and "too recent" in d.reasons[0]


def test_borderline_goes_to_review():
    assert run("maybe interesting digest").action == REVIEW


def test_protected_mail_is_not_archived():
    cfg_topics_archive = run("Big sale", labels=("INBOX", "STARRED"))
    assert not cfg_topics_archive.archive
