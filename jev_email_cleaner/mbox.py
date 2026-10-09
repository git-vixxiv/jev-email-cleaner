"""Google Takeout MBOX in, sorted MBOX files out.

Takeout gives one "All mail Including Spam and Trash.mbox" with full bodies, headers and
attachments, so this path needs no Gmail OAuth and no per-message API calls. After
classify + plan, export_mbox copies every original message, byte for byte, into one MBOX
per destination folder, ready to import into any mail host (Thunderbird, Apple Mail,
Outlook via an MBOX importer, Fastmail, Proton's import tool, ...).
"""

from __future__ import annotations

import hashlib
import mailbox
import re
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

from .email_record import EmailRecord, clean_text, html_to_text
from .store import Store

# Takeout's X-Gmail-Labels names -> Gmail API label ids the policy code already understands.
_LABELS = {"inbox": "INBOX", "unread": "UNREAD", "starred": "STARRED", "sent": "SENT",
           "important": "IMPORTANT", "spam": "SPAM", "trash": "TRASH", "draft": "DRAFT", "chat": "CHAT"}
_UNSAFE = re.compile(r"[^\w&.\- ]+")


def _text(value) -> str:
    if value is None:
        return ""
    try:
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def message_key(msg: Message) -> str:
    """Stable id for a message: its Message-ID, else a hash of sender, date and subject."""
    basis = (msg.get("Message-ID") or "").strip() or "|".join(
        _text(msg.get(h)) for h in ("From", "Date", "Subject"))
    return "m-" + hashlib.sha1(basis.encode("utf-8", "replace")).hexdigest()[:20]


def gmail_labels(msg: Message) -> list[str]:
    raw = _text(msg.get("X-Gmail-Labels"))
    labels = []
    for name in (p.strip() for p in raw.split(",") if p.strip()):
        labels.append(_LABELS.get(name.lower(), name))
    return labels


def _body_and_attachments(msg: Message) -> tuple[str, bool]:
    plain, rich, has_attachment = [], [], False
    for part in msg.walk():
        if part.is_multipart():
            continue
        if part.get_filename() or (part.get("Content-Disposition") or "").lower().startswith("attachment"):
            has_attachment = True
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        (plain if ctype == "text/plain" else rich).append(text if ctype == "text/plain" else html_to_text(text))
    return clean_text("\n".join(plain) if plain else "\n".join(rich)), has_attachment


def _epoch_ms(msg: Message) -> int:
    try:
        return int(parsedate_to_datetime(_text(msg.get("Date"))).timestamp() * 1000)
    except Exception:
        return 0


def parse_mbox_message(msg: Message, body_chars: int) -> EmailRecord:
    name, addr = parseaddr(_text(msg.get("From")))
    body, has_attachment = _body_and_attachments(msg)
    unsub = _text(msg.get("List-Unsubscribe"))
    return EmailRecord(
        id=message_key(msg),
        thread_id=_text(msg.get("X-GM-THRID")),
        sender_name=name,
        sender_email=addr.lower(),
        subject=clean_text(_text(msg.get("Subject"))),
        snippet=body[:200],
        body=body[:body_chars],
        internal_ms=_epoch_ms(msg),
        label_ids=gmail_labels(msg),
        list_unsubscribe=unsub,
        is_list_mail=bool(msg.get("List-Id") or unsub)
        or _text(msg.get("Precedence")).lower() in {"bulk", "list", "junk"},
        has_attachment=has_attachment,
    )


def import_mbox(store: Store, path: str | Path, body_chars: int, log=print, batch: int = 500) -> tuple[int, int]:
    """Load a Takeout MBOX. Sent mail feeds the correspondents list instead of being judged.

    Returns (messages imported for judging, sent messages scanned).
    """
    records: list[EmailRecord] = []
    addrs: set[str] = set()
    imported = sent = 0
    for msg in mailbox.mbox(str(path), create=False):
        labels = gmail_labels(msg)
        if "SENT" in labels:
            sent += 1
            # Python 3.13's strict getaddresses yields nothing if several fields are empty.
            pairs = getaddresses([v for v in (_text(msg.get(h)) for h in ("To", "Cc", "Bcc")) if v])
            addrs |= {a.lower() for _, a in pairs if a}
            continue
        if {"SPAM", "TRASH", "CHAT", "DRAFT"} & set(labels):
            continue
        records.append(parse_mbox_message(msg, body_chars))
        if len(records) >= batch:
            store.upsert_emails(records)
            imported += len(records)
            records = []
            log(f"  imported {imported}")
    store.upsert_emails(records)
    store.add_correspondents(addrs)
    return imported + len(records), sent


def folder_for(decision: dict | None, labels: list[str]) -> str:
    """Destination MBOX name for one message."""
    if "SENT" in labels:
        return "Sent"
    if {"SPAM", "TRASH"} & set(labels):
        return "_Spam-and-Trash"
    if "CHAT" in labels or "DRAFT" in labels:
        return "_Chats-and-Drafts"
    if decision is None:
        return "_Not-yet-judged"
    if decision["action"] == "delete":
        return "_Suggested-Delete"
    return _UNSAFE.sub("", decision["topic"]).strip() or "Other"


def export_mbox(store: Store, src: str | Path, out_dir: str | Path, log=print) -> dict[str, int]:
    """Copy every message of src into out_dir/<Folder>.mbox. Nothing is dropped; suggested
    deletions land in _Suggested-Delete.mbox so you decide whether to import them."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    decisions = {d["id"]: d for d in store.decisions()}
    boxes: dict[str, mailbox.mbox] = {}
    counts: dict[str, int] = {}
    try:
        for msg in mailbox.mbox(str(src), create=False):
            name = folder_for(decisions.get(message_key(msg)), gmail_labels(msg))
            if name not in boxes:
                boxes[name] = mailbox.mbox(str(out / f"{name}.mbox"))
                boxes[name].lock()
            boxes[name].add(msg)
            counts[name] = counts.get(name, 0) + 1
    finally:
        for box in boxes.values():
            box.flush()
            box.unlock()
            box.close()
    total = sum(counts.values())
    log(f"Wrote {total} messages into {len(counts)} files in {out}")
    return counts
