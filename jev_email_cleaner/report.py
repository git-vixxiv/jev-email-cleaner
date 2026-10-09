"""Self-contained HTML + CSV report of the current plan."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from .email_record import EmailRecord, unsubscribe_target
from .policy import DELETE, KEEP, REVIEW


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


def _rows(emails, decisions, action, limit=500) -> str:
    picked = sorted((d for d in decisions if d["action"] == action), key=lambda d: -d["disposable"])[:limit]
    rows = []
    for d in picked:
        e = emails[d["id"]]
        link = f"https://mail.google.com/mail/u/0/#all/{escape(e.id)}"
        rows.append(
            f"<tr><td>{escape(e.sender_name or e.sender_email)}<br><small>{escape(e.sender_email)}</small></td>"
            f"<td><a href='{link}' target='_blank' rel='noopener'>{escape(e.subject or '(no subject)')}</a></td>"
            f"<td>{escape(d['topic'])}</td><td>{e.age_days()}d</td>"
            f"<td>{escape('; '.join(d['reasons']))}</td></tr>"
        )
    return "".join(rows) or "<tr><td colspan=5>None</td></tr>"


def render_html(emails: dict[str, EmailRecord], decisions: list[dict], label_prefix: str) -> str:
    actions = Counter(d["action"] for d in decisions)
    topics = Counter(d["topic"] for d in decisions)
    senders = sender_stats(emails, decisions)[:50]
    topic_rows = "".join(f"<tr><td>{escape(t)}</td><td>{n}</td></tr>" for t, n in topics.most_common())
    sender_rows = "".join(
        f"<tr><td>{escape(s.name or s.sender)}<br><small>{escape(s.sender)}</small></td><td>{s.count}</td>"
        f"<td>{s.unread_rate:.0%}</td><td>{s.delete_rate:.0%}</td>"
        + (f"<td><a href='{_safe_href(s.unsubscribe)}' target='_blank' rel='noopener noreferrer'>Unsubscribe</a></td>"
           if _safe_href(s.unsubscribe) else "<td>-</td>")
        + "</tr>"
        for s in senders
    ) or "<tr><td colspan=5>None</td></tr>"
    head = "<tr><th>From</th><th>Subject</th><th>Topic</th><th>Age</th><th>Why</th></tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Mailbox Cleanup Plan</title>
<style>
:root{{--bg:#fafaf9;--fg:#1c1917;--muted:#78716c;--line:#e7e5e4;--accent:#b91c1c;--ok:#15803d;--warn:#b45309}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--muted:#a8a29e;--line:#44403c;--accent:#f87171;--ok:#4ade80;--warn:#fbbf24}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:24px 16px}}
h1{{margin:0 0 4px}} h2{{margin-top:36px;border-bottom:1px solid var(--line);padding-bottom:4px}}
.muted,small{{color:var(--muted)}} a{{color:inherit}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:20px 0}}
.card{{border:1px solid var(--line);border-radius:10px;padding:12px}} .card b{{font-size:28px;display:block}}
.del b{{color:var(--accent)}} .rev b{{color:var(--warn)}} .keep b{{color:var(--ok)}}
.wrap{{overflow-x:auto}} table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}
</style></head><body>
<h1>Mailbox cleanup plan</h1>
<div class="muted">Generated {datetime.now():%Y-%m-%d %H:%M}. Nothing has changed in Gmail until you run <code>jevclean apply</code>.</div>
<div class="cards">
<div class="card"><b>{len(decisions)}</b>emails judged</div>
<div class="card del"><b>{actions[DELETE]}</b>suggested for deletion</div>
<div class="card rev"><b>{actions[REVIEW]}</b>need your review</div>
<div class="card keep"><b>{actions[KEEP]}</b>keep</div>
</div>
<p class="muted">Suggested deletions are labeled <code>{escape(label_prefix)}/Suggested-Delete</code> in Gmail by
<code>jevclean apply</code>. Remove that label from anything you want to keep, then run
<code>jevclean apply --trash</code>. Trash stays recoverable in Gmail for 30 days.</p>
<h2>Folders</h2><div class="wrap"><table><tr><th>Topic</th><th>Emails</th></tr>{topic_rows}</table></div>
<h2>Unsubscribe candidates</h2>
<p class="muted">Frequent senders you rarely read. Ranked by volume times (unread rate + delete rate).</p>
<div class="wrap"><table><tr><th>Sender</th><th>Emails</th><th>Unread</th><th>Deletable</th><th></th></tr>{sender_rows}</table></div>
<h2>Suggested for deletion</h2><div class="wrap"><table>{head}{_rows(emails, decisions, DELETE)}</table></div>
<h2>Needs review</h2><div class="wrap"><table>{head}{_rows(emails, decisions, REVIEW)}</table></div>
</body></html>"""


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
