"""Self-contained HTML + CSV report of the current plan."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from .email_record import EmailRecord, unsubscribe_target
from .policy import DELETE


@dataclass
class SenderStats:
    sender: str
    name: str
    count: int
    unread_rate: float
    delete_rate: float
    unsubscribe: str


def sender_stats(emails: dict[str, EmailRecord], decisions: list[dict], min_count: int = 3) -> list[SenderStats]:
    """Senders worth unsubscribing from: frequent, mostly unread, mostly disposable, with an unsubscribe link."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in decisions:
        groups[emails[d["id"]].sender_email].append(d)
    out = []
    for sender, ds in groups.items():
        recs = [emails[d["id"]] for d in ds]
        link = next((unsubscribe_target(r.list_unsubscribe) for r in recs if r.list_unsubscribe), "")
        if len(ds) < min_count or not link:
            continue
        out.append(SenderStats(
            sender=sender,
            name=recs[0].sender_name,
            count=len(ds),
            unread_rate=sum(r.unread for r in recs) / len(recs),
            delete_rate=sum(d["action"] == DELETE for d in ds) / len(ds),
            unsubscribe=link,
        ))
    out.sort(key=lambda s: (s.delete_rate + s.unread_rate) * s.count, reverse=True)
    return out


def _cell(value: str) -> str:
    """Neutralize spreadsheet formula injection from sender-controlled text."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def _safe_href(url: str) -> str:
    return escape(url, quote=True) if url.startswith(("https://", "mailto:")) else ""


ASSETS = Path(__file__).parent / "assets"
_MSG_ID = re.compile(r"^[0-9a-zA-Z]+$")


def gmail_url(message_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{message_id}" if _MSG_ID.match(message_id) else ""


def build_payload(emails: dict[str, EmailRecord], decisions: list[dict], label_prefix: str) -> dict:
    plan_id = hashlib.sha256(
        json.dumps(sorted((d["id"], d["action"]) for d in decisions)).encode()).hexdigest()[:10]
    rows = []
    for d in sorted(decisions, key=lambda d: (-d["disposable"], emails[d["id"]].sender_email)):
        e = emails[d["id"]]
        rows.append({
            "id": e.id, "name": e.sender_name, "from": e.sender_email, "subject": e.subject,
            "topic": d["topic"], "action": d["action"], "age": e.age_days(), "unread": e.unread,
            "disposable": round(d["disposable"], 3), "value": round(d["lasting_value"], 2),
            "reasons": d["reasons"], "url": gmail_url(e.id),
        })
    by_sender: dict[str, list[str]] = defaultdict(list)
    for d in decisions:
        by_sender[emails[d["id"]].sender_email].append(d["id"])
    senders = [{
        "sender": s.sender, "name": s.name, "count": s.count, "unread_rate": round(s.unread_rate, 3),
        "delete_rate": round(s.delete_rate, 3), "unsubscribe": s.unsubscribe if _safe_href(s.unsubscribe) else "",
        "ids": by_sender[s.sender],
    } for s in sender_stats(emails, decisions)[:100]]
    return {
        "plan_id": plan_id, "generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "label_prefix": label_prefix,
        "archive_count": sum(1 for d in decisions if d["archive"]), "emails": rows, "senders": senders,
    }


def render_html(emails: dict[str, EmailRecord], decisions: list[dict], label_prefix: str) -> str:
    payload = json.dumps(build_payload(emails, decisions, label_prefix), ensure_ascii=False)
    # Mail content is attacker-controlled: never let it close the <script> element.
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = (ASSETS / "report.html").read_text()
    for marker, value in (
        ("/*TOKENS*/", (ASSETS / "tokens.css").read_text()),
        ("/*CSS*/", (ASSETS / "report.css").read_text()),
        ("/*JS*/", (ASSETS / "report.js").read_text()),
        ("/*DATA*/", payload),
    ):
        page = page.replace(marker, value, 1)
    return page


def write_reports(emails: dict[str, EmailRecord], decisions: list[dict], out_dir: str | Path, label_prefix: str):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    html_path = out / f"plan-{stamp}.html"
    csv_path = out / f"plan-{stamp}.csv"
    html_path.write_text(render_html(emails, decisions, label_prefix))
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "date", "from", "subject", "topic", "action", "archive",
                    "disposable", "lasting_value", "protect", "reasons"])
        for d in decisions:
            e = emails[d["id"]]
            w.writerow([e.id, datetime.fromtimestamp(e.internal_ms / 1000).date(), _cell(e.sender_email), _cell(e.subject),
                        d["topic"], d["action"], d["archive"], f"{d['disposable']:.3f}",
                        f"{d['lasting_value']:.2f}", f"{d['protect']:.3f}", _cell("; ".join(d["reasons"]))])
    return html_path, csv_path
