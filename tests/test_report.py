import json
import re

from jev_email_cleaner.email_record import parse_message
from jev_email_cleaner.report import _cell, build_payload, gmail_url, render_html

from .fakes import gmail_message

DECISION = {"topic": "Promotions", "action": "delete", "archive": True,
            "reasons": ["r"], "disposable": 1.0, "lasting_value": 0.0, "protect": 0.0}


def hostile_inputs():
    rec = parse_message(gmail_message("1", "x@evil.com", "</script><img src=x onerror=alert(1)>", "b",
                                      list_unsub="<javascript:alert(1)>"), 100)
    recs = {str(i): parse_message(gmail_message(str(i), "x@evil.com", "s", list_unsub="<javascript:alert(1)>"), 100)
            for i in range(2, 5)}
    recs["1"] = rec
    return recs, [{**DECISION, "id": i} for i in recs]


def test_mail_content_cannot_break_out_of_the_data_block():
    emails, decisions = hostile_inputs()
    html = render_html(emails, decisions, "Jev")
    data = re.search(r'<script type="application/json" id="plan-data">(.*?)</script>', html, re.S).group(1)
    assert "</script" not in data and "<img" not in data
    payload = json.loads(data)
    hostile = next(e for e in payload["emails"] if e["id"] == "1")
    assert hostile["subject"].startswith("</script>")  # round-trips intact for textContent
    assert all(s["unsubscribe"] == "" for s in payload["senders"])  # javascript: links dropped


def test_template_markers_all_filled():
    emails, decisions = hostile_inputs()
    html = render_html(emails, decisions, "Jev")
    for marker in ("/*TOKENS*/", "/*CSS*/", "/*JS*/", "/*DATA*/"):
        assert marker not in html
    assert "Hallmark · genre: modern-minimal" in html


def test_payload_plan_id_is_stable_and_urls_safe():
    emails, decisions = hostile_inputs()
    a, b = build_payload(emails, decisions, "Jev"), build_payload(emails, decisions, "Jev")
    assert a["plan_id"] == b["plan_id"] and a["archive_count"] == 4
    assert gmail_url("19c6f828d42cff2c").endswith("#all/19c6f828d42cff2c")
    assert gmail_url("x\"onmouseover=1") == ""


def test_csv_formula_injection_neutralized():
    assert _cell("=HYPERLINK(\"x\")").startswith("'")
    assert _cell("Normal subject") == "Normal subject"
