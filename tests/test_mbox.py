import mailbox
from email.message import EmailMessage

from jev_email_cleaner.mbox import export_mbox, folder_for, import_mbox, message_key
from jev_email_cleaner.policy import Decision
from jev_email_cleaner.store import Store


def _msg(mid, sender, subject, body, labels, to="joe@x.com", attach=False, list_unsub=None):
    m = EmailMessage()
    m["Message-ID"], m["From"], m["To"], m["Subject"] = mid, sender, to, subject
    m["Date"] = "Tue, 03 Feb 2026 10:00:00 +0000"
    m["X-Gmail-Labels"] = labels
    m["X-GM-THRID"] = "123"
    if list_unsub:
        m["List-Unsubscribe"] = list_unsub
    m.set_content(body)
    if attach:
        m.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="invoice.pdf")
    return m


def _mbox(tmp_path, msgs):
    path = tmp_path / "takeout.mbox"
    box = mailbox.mbox(str(path))
    for m in msgs:
        box.add(m)
    box.flush()
    box.close()
    return path


def test_import_parses_bodies_labels_and_sent(tmp_path):
    path = _mbox(tmp_path, [
        _msg("<a@1>", "Shop <Deals@Shop.com>", "Sale", "50% off", "Inbox,Unread,Category Promotions",
             list_unsub="<https://shop.com/u>"),
        _msg("<b@1>", "Ann <ann@x.com>", "Invoice", "attached", "Inbox,Starred", attach=True),
        _msg("<c@1>", "joe@x.com", "Re: hi", "thanks", "Sent", to="Bob <Bob@Y.com>"),
        _msg("<d@1>", "spam@z.com", "Win", "now", "Spam"),
    ])
    store = Store(tmp_path / "db.sqlite")
    n, sent = import_mbox(store, path, body_chars=100, log=lambda *_: None)
    assert (n, sent) == (2, 1)
    recs = {r.subject: r for r in store.emails()}
    assert recs["Sale"].is_list_mail and recs["Sale"].unread and recs["Sale"].body == "50% off"
    assert recs["Sale"].sender_email == "deals@shop.com" and recs["Sale"].internal_ms > 0
    assert recs["Invoice"].has_attachment and recs["Invoice"].starred
    assert store.correspondents() == {"bob@y.com"}


def test_export_routes_every_message(tmp_path):
    msgs = [_msg("<a@1>", "s@shop.com", "Sale", "x", "Inbox"),
            _msg("<b@1>", "ann@x.com", "Invoice", "y", "Inbox"),
            _msg("<c@1>", "joe@x.com", "Re", "z", "Sent"),
            _msg("<d@1>", "n@new.com", "New", "w", "Inbox")]
    path = _mbox(tmp_path, msgs)
    store = Store(tmp_path / "db.sqlite")
    store.save_decisions([
        Decision(message_key(msgs[0]), "Promotions", "delete", True, 0.9, 0.1, 0.0, []),
        Decision(message_key(msgs[1]), "Finance & Tax", "keep", False, 0.1, 2.5, 0.8, []),
    ])
    counts = export_mbox(store, path, tmp_path / "out", log=lambda *_: None)
    assert counts == {"_Suggested-Delete": 1, "Finance & Tax": 1, "Sent": 1, "_Not-yet-judged": 1}
    out = mailbox.mbox(str(tmp_path / "out" / "Finance & Tax.mbox"))
    assert [m["Subject"] for m in out] == ["Invoice"]


def test_folder_names_are_filesystem_safe():
    assert folder_for({"action": "keep", "topic": "Accounts/Security?"}, []) == "AccountsSecurity"
    assert folder_for(None, ["TRASH"]) == "_Spam-and-Trash"
