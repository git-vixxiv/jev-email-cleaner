from datetime import datetime, timezone

from jev_email_cleaner.config import Config
from jev_email_cleaner.pipeline import apply_labels, classify, plan, sync, trash_suggested, undo
from jev_email_cleaner.report import write_reports
from jev_email_cleaner.store import Store

from .fakes import NOW_MS, FakeGmail, JevEndpoint, gmail_message

NOW = datetime.fromtimestamp(NOW_MS / 1000, timezone.utc)
quiet = lambda *_: None  # noqa: E731


def mailbox():
    inbox = [
        gmail_message("m1", "Shop <deals@shop.com>", "Big sale today", "50% off",
                      list_unsub="<https://shop.com/u>"),
        gmail_message("m2", "Shop <deals@shop.com>", "Final sale", "last chance", list_unsub="<https://shop.com/u>"),
        gmail_message("m3", "Bank <billing@bank.com>", "Invoice March", "amount due"),
        gmail_message("m4", "Ann <ann@friend.com>", "Big sale at the farm, want to come?", "hi"),
        gmail_message("m5", "Social <n@social.com>", "Someone liked your post", "like"),
        gmail_message("m6", "Shop <deals@shop.com>", "Weekend sale", "20% off", list_unsub="<https://shop.com/u>"),
    ]
    sent = [{"id": "s1", "payload": {"headers": [{"name": "To", "value": "ann@friend.com"}]}}]
    return FakeGmail(inbox, sent)


def test_end_to_end_label_review_trash_undo(tmp_path):
    cfg = Config()
    gmail, store = mailbox(), Store(tmp_path / "db.sqlite")
    assert sync(gmail, store, cfg, log=quiet) == 6
    assert sync(gmail, store, cfg, log=quiet) == 0  # incremental
    assert store.correspondents() == {"ann@friend.com"}

    classify(store, cfg, JevEndpoint().client_factory(), log=quiet)
    decisions = {d.message_id: d for d in plan(store, cfg, NOW)}
    assert decisions["m1"].action == "delete" and decisions["m2"].action == "delete"
    assert decisions["m3"].action == "keep"
    assert decisions["m4"].action == "keep"  # Ann is a correspondent: hard protection beats "sale"

    run1 = apply_labels(gmail, store, cfg)
    sd = gmail.labels[cfg.suggested_delete_label]
    assert sd in gmail.messages["m1"]["labelIds"]
    assert gmail.labels["Jev/Finance"] in gmail.messages["m3"]["labelIds"]
    assert "INBOX" not in gmail.messages["m1"]["labelIds"]  # Promotions archived
    assert "INBOX" in gmail.messages["m4"]["labelIds"]      # protected, not archived

    # Re-applying is idempotent: no duplicate actions logged.
    run_again = apply_labels(gmail, store, cfg)
    assert store.run_actions(run_again) == []

    # User rescues m2 in Gmail by removing the label; only m1 (and m5) get trashed.
    gmail.modify(["m2"], remove=[sd])
    _, n = trash_suggested(gmail, store, cfg)
    assert "m1" in gmail.trashed and "m2" not in gmail.trashed
    assert n == len(gmail.trashed)

    undo(gmail, store)  # reverts the trash run
    assert not gmail.trashed
    undo(gmail, store, run1)  # reverts labels and archive
    assert gmail.messages["m1"]["labelIds"] == ["INBOX"]

    html, csv = write_reports({e.id: e for e in store.emails()}, store.decisions(), tmp_path / "r", cfg.label_prefix)
    text = html.read_text()
    assert "Unsubscribe candidates" in text and "https://shop.com/u" in text
    assert csv.read_text().count("\n") == 7
