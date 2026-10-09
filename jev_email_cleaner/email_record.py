"""Turn raw Gmail API messages into EmailRecord, computing facts in code.

Jev is weak at dates, counting and arithmetic, so age, sender volume, read rate
and correspondence history are all computed here and never asked of the model.
"""

from __future__ import annotations

import base64
import html
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr

METADATA_HEADERS = ["From", "To", "Cc", "Subject", "Date", "List-Unsubscribe", "List-Id", "Precedence"]


@dataclass
class EmailRecord:
    id: str
    thread_id: str
    sender_name: str
    sender_email: str
    subject: str
    snippet: str
    body: str
    internal_ms: int
    label_ids: list[str] = field(default_factory=list)
    list_unsubscribe: str = ""
    is_list_mail: bool = False
    has_attachment: bool = False

    @property
    def sender_domain(self) -> str:
        return self.sender_email.rpartition("@")[2]

    @property
    def unread(self) -> bool:
        return "UNREAD" in self.label_ids

    @property
    def starred(self) -> bool:
        return "STARRED" in self.label_ids

    def age_days(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        sent = datetime.fromtimestamp(self.internal_ms / 1000, timezone.utc)
        return max(0, (now - sent).days)

    def to_row(self) -> dict:
        row = asdict(self)
        row["label_ids"] = ",".join(self.label_ids)
        return row

    @classmethod
    def from_row(cls, row: dict) -> "EmailRecord":
        data = dict(row)
        data["label_ids"] = [x for x in (data.get("label_ids") or "").split(",") if x]
        data["is_list_mail"] = bool(data["is_list_mail"])
        data["has_attachment"] = bool(data["has_attachment"])
        return cls(**data)


def _headers(payload: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


# Marketing mail pads previews with invisible joiners to hide the body from preview panes.
_FILLER = re.compile(r"[\u034f\u200b-\u200f\u00ad\ufeff]+")
_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_WS = re.compile(r"\s+")


def clean_text(text: str) -> str:
    return _WS.sub(" ", _FILLER.sub("", html.unescape(text))).strip()


def html_to_text(markup: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", markup))).strip()


def extract_body(payload: dict) -> tuple[str, bool]:
    """Return (plain text body, has_attachment). Prefers text/plain over text/html."""
    plain: list[str] = []
    rich: list[str] = []
    has_attachment = False
    stack = [payload]
    while stack:
        part = stack.pop()
        stack.extend(reversed(part.get("parts", [])))
        if part.get("filename"):
            has_attachment = True
            continue
        data = part.get("body", {}).get("data")
        if not data:
            continue
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            plain.append(_decode(data))
        elif mime == "text/html":
            rich.append(html_to_text(_decode(data)))
    text = "\n".join(plain) if plain else "\n".join(rich)
    return clean_text(text), has_attachment


def parse_message(msg: dict, body_chars: int) -> EmailRecord:
    payload = msg.get("payload", {})
    headers = _headers(payload)
    name, addr = parseaddr(headers.get("from", ""))
    body, has_attachment = extract_body(payload) if body_chars > 0 else ("", False)
    return EmailRecord(
        id=msg["id"],
        thread_id=msg.get("threadId", ""),
        sender_name=name,
        sender_email=addr.lower(),
        subject=headers.get("subject", ""),
        snippet=clean_text(msg.get("snippet", "")),
        body=body[:body_chars],
        internal_ms=int(msg.get("internalDate", 0)),
        label_ids=list(msg.get("labelIds", [])),
        list_unsubscribe=headers.get("list-unsubscribe", ""),
        is_list_mail=bool(headers.get("list-id") or headers.get("list-unsubscribe"))
        or headers.get("precedence", "").lower() in {"bulk", "list", "junk"},
        has_attachment=has_attachment,
    )


def recipients(msg: dict) -> set[str]:
    """Lowercased addresses in To/Cc of a (sent) message."""
    headers = _headers(msg.get("payload", {}))
    pairs = getaddresses([headers.get("to", ""), headers.get("cc", "")])
    return {addr.lower() for _, addr in pairs if addr}


def unsubscribe_target(header: str) -> str:
    """Pick the best unsubscribe link from a List-Unsubscribe header (https first)."""
    targets = re.findall(r"<([^>]+)>", header)
    for t in targets:
        if t.startswith("https://"):
            return t
    return targets[0] if targets else ""


def parse_connector_message(msg: dict) -> EmailRecord:
    """Parse one message from the Gmail connector's search_threads output (subject + snippet, no body).

    The connector exposes neither List-* headers nor attachments, so is_list_mail and
    has_attachment are False and the attachment protection cannot fire in this mode.
    """
    name, addr = parseaddr(msg.get("sender", ""))
    return EmailRecord(
        id=msg["id"],
        thread_id=msg.get("threadId", ""),
        sender_name=name,
        sender_email=addr.lower(),
        subject=clean_text(msg.get("subject", "")),
        snippet=clean_text(msg.get("snippet", "")),
        body="",
        internal_ms=int(msg.get("internalDate", 0)),
        label_ids=list(msg.get("labelIds", [])),
    )
