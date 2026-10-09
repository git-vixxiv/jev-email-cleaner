from jev_email_cleaner.config import Config
from jev_email_cleaner.email_record import parse_message
from jev_email_cleaner.judge import build_questions, build_state, estimate_cost, question_hash
from jev_email_cleaner.pipeline import classify
from jev_email_cleaner.store import Store

from .fakes import JevEndpoint, gmail_message


def test_request_shape_matches_api(tmp_path):
    cfg = Config()
    store = Store(tmp_path / "db.sqlite")
    store.upsert_emails([parse_message(gmail_message("1", "s@shop.com", "Big sale", "buy"), 500)])
    jev = JevEndpoint()
    ok, bad = classify(store, cfg, jev.client_factory(), log=lambda *_: None)
    assert (ok, bad) == (1, 0)

    body = jev.requests[0]
    assert body["model"] == "jev-latest"
    qs = body["questions"]
    assert {k: q["type"] for k, q in qs.items()} == {
        "topic": "choice", "kind": "choice", "keep_record": "noul", "personal": "noul",
        "needs_action": "noul", "lasting_value": "score"}
    assert list(qs["topic"]["criteria"])[-1] == "Other"
    assert 2 <= len(qs["lasting_value"]["criteria"]) <= 10
    # Dates are computed in code, never sent to Jev.
    assert "date" not in str(body["state"]).lower()

    saved = store.judgments(question_hash(cfg))["1"]
    assert saved["kind"]["choice"] == "marketing"
    assert saved["lasting_value"]["type"] == "score"


def test_failures_are_isolated_and_retried_next_run(tmp_path):
    cfg = Config()
    store = Store(tmp_path / "db.sqlite")
    store.upsert_emails([parse_message(gmail_message(str(i), "s@x.com", s), 500)
                         for i, s in enumerate(["Big sale", "BOOM", "Invoice 7"])])
    ok, bad = classify(store, cfg, JevEndpoint(fail_subjects=("BOOM",)).client_factory(), log=lambda *_: None)
    assert (ok, bad) == (2, 1)
    # Only the failed one is pending; cached judgments are not re-bought.
    jev = JevEndpoint()
    assert classify(store, cfg, jev.client_factory(), log=lambda *_: None) == (1, 0)
    assert len(jev.requests) == 1


def test_question_hash_changes_with_topics():
    a, b = Config(), Config()
    b.topics = {**b.topics, "Kids": "School and activities"}
    assert question_hash(a) != question_hash(b)


def test_cost_estimate_is_small():
    recs = [parse_message(gmail_message(str(i), "s@x.com", "Hello", "x" * 1200), 1200) for i in range(1000)]
    tokens, usd = estimate_cost(recs, Config())
    assert 300_000 < tokens < 3_000_000 and usd < 0.15
    assert set(build_state(recs[0])["email"]) >= {"subject", "body_excerpt", "sent_via_mailing_list"}
    assert len(build_questions(Config())) == 6
