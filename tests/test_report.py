from jev_email_cleaner.email_record import parse_message
from jev_email_cleaner.report import _cell, render_html

from .fakes import gmail_message


def test_untrusted_content_is_escaped_and_links_restricted():
    rec = parse_message(gmail_message("1", "x@evil.com", "<img src=x onerror=alert(1)>", "b",
                                      list_unsub="<javascript:alert(1)>"), 100)
    decisions = [{"id": "1", "topic": "Promotions", "action": "delete", "archive": True,
                  "reasons": ["r"], "disposable": 1.0, "lasting_value": 0.0, "protect": 0.0}] * 3
    html = render_html({"1": rec}, decisions, "Jev")
    assert "<img src=x" not in html and "javascript:" not in html


def test_csv_formula_injection_neutralized():
    assert _cell("=HYPERLINK(\"x\")").startswith("'")
    assert _cell("Normal subject") == "Normal subject"
