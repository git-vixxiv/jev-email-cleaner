from jev_email_cleaner.email_record import parse_message, recipients, unsubscribe_target

from .fakes import gmail_message


def test_parse_plain_body_attachment_and_list_headers():
    msg = gmail_message("1", "Shop <Deals@Shop.com>", "Big sale", "50% off everything", attachment=True,
                        list_unsub="<mailto:u@shop.com>, <https://shop.com/unsub>", labels=("INBOX", "UNREAD"))
    rec = parse_message(msg, body_chars=10)
    assert rec.sender_email == "deals@shop.com" and rec.sender_domain == "shop.com"
    assert rec.body == "50% off ev"
    assert rec.has_attachment and rec.is_list_mail and rec.unread and not rec.starred
    assert unsubscribe_target(rec.list_unsubscribe) == "https://shop.com/unsub"


def test_html_fallback_strips_tags_and_scripts():
    msg = gmail_message("2", "a@b.com", "Hi", "<p>Hello&nbsp;<b>there</b></p><script>evil()</script>", html=True)
    assert parse_message(msg, 500).body == "Hello there"


def test_body_chars_zero_skips_body():
    assert parse_message(gmail_message("3", "a@b.com", "Hi", "secret"), 0).body == ""


def test_recipients_parses_to_and_cc():
    msg = {"payload": {"headers": [{"name": "To", "value": "Ann <Ann@X.com>, bob@y.com"},
                                   {"name": "Cc", "value": "c@z.com"}]}}
    assert recipients(msg) == {"ann@x.com", "bob@y.com", "c@z.com"}
