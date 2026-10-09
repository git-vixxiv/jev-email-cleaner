"""The steps the CLI runs. Each step reads/writes the Store, so steps can be re-run independently."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import datetime

from .config import Config
from .email_record import EmailRecord, parse_connector_message, parse_message, recipients
from .judge import judge_all, question_hash
from .policy import DELETE, KEEP, Decision, decide
from .store import Store


def sync(gmail, store: Store, cfg: Config, log=print) -> int:
    """Pull new messages matching sync_query, plus sent-mail recipients. Returns new message count."""
    ids = gmail.list_ids(cfg.sync_query, cfg.sync_max_messages)
    new = [i for i in ids if i not in store.known_ids()]
    log(f"{len(ids)} messages match, {len(new)} new")
    for start in range(0, len(new), 200):
        msgs = gmail.get_messages(new[start : start + 200], full=cfg.body_chars > 0)
        store.upsert_emails([parse_message(m, cfg.body_chars) for m in msgs])
        log(f"  fetched {min(start + 200, len(new))}/{len(new)}")

    sent_ids = gmail.list_ids("in:sent", cfg.sent_scan_max)
    addrs: set[str] = set()
    for msg in gmail.get_messages(sent_ids, full=False, headers_only=["To", "Cc"]):
        addrs |= recipients(msg)
    store.add_correspondents(addrs)
    log(f"{len(addrs)} people you have emailed (their mail is protected)")
    return len(new)


def import_json(store: Store, data: dict) -> int:
    """Load {"messages": [...connector messages], "sent_recipients": [...]} into the store."""
    records = [parse_connector_message(m) for m in data.get("messages", [])]
    store.upsert_emails(records)
    store.add_correspondents({a.lower() for a in data.get("sent_recipients", [])})
    return len(records)


def pending(store: Store, cfg: Config) -> list[EmailRecord]:
    done = store.judgments(question_hash(cfg))
    return [r for r in store.emails() if r.id not in done]


def classify(store: Store, cfg: Config, client_factory, limit: int | None = None, log=print) -> tuple[int, int]:
    """Judge emails without a current judgment. Returns (succeeded, failed).

    client_factory() must return an AsyncTypeSafeClient (an async context manager).
    """
    qh = question_hash(cfg)
    todo = pending(store, cfg)[:limit]
    ok, bad = 0, 0

    def on_result(rec, answers, model, tokens):
        nonlocal ok
        store.save_judgment(rec.id, qh, model, answers, tokens)
        ok += 1
        if ok % 100 == 0:
            log(f"  judged {ok}/{len(todo)}")

    def on_error(rec, exc):
        nonlocal bad
        bad += 1
        log(f"  failed {rec.id}: {type(exc).__name__}: {exc}")

    async def run() -> None:
        async with client_factory() as client:
            await judge_all(todo, cfg, client, on_result, on_error)

    asyncio.run(run())
    return ok, bad


def plan(store: Store, cfg: Config, now: datetime | None = None) -> list[Decision]:
    judgments = store.judgments(question_hash(cfg))
    correspondents = store.correspondents()
    overrides = store.overrides()
    decisions = []
    for rec in store.emails():
        if rec.id not in judgments:
            continue
        d = decide(rec, judgments[rec.id], cfg, correspondents, now)
        if rec.id in overrides and overrides[rec.id] != d.action:
            d.reasons.insert(0, f"you chose {overrides[rec.id]} (plan said {d.action})")
            d.action = overrides[rec.id]
        decisions.append(d)
    store.save_decisions(decisions)
    return decisions


def import_decisions(store: Store, data: dict) -> tuple[int, int]:
    """Store keep/delete choices exported from the review page. Returns (accepted, skipped)."""
    known = store.known_ids()
    raw = data.get("decisions", {})
    accepted = {i: a for i, a in raw.items() if i in known and a in (KEEP, DELETE)}
    store.set_overrides(accepted)
    return len(accepted), len(raw) - len(accepted)


def apply_labels(gmail, store: Store, cfg: Config, mark_suggested: bool = True, archive: bool = True) -> str:
    """Add topic labels, the Suggested-Delete label, and archive configured topics. Returns run id."""
    decisions = store.decisions()
    run_id = uuid.uuid4().hex[:12]
    names = {cfg.topic_label(d["topic"]) for d in decisions if d["topic"] != "Other"}
    if mark_suggested:
        names.add(cfg.suggested_delete_label)
    label_ids = gmail.ensure_labels(sorted(names))
    current = {r.id: set(r.label_ids) for r in store.emails()}

    adds: dict[str, list[str]] = defaultdict(list)
    for d in decisions:
        if d["topic"] != "Other":
            adds[label_ids[cfg.topic_label(d["topic"])]].append(d["id"])
        if mark_suggested and d["action"] == DELETE:
            adds[label_ids[cfg.suggested_delete_label]].append(d["id"])
    for label_id, ids in adds.items():
        ids = [i for i in ids if label_id not in current.get(i, set())]
        if not ids:
            continue
        gmail.modify(ids, add=[label_id])
        for i in ids:
            store.log_action(run_id, i, "add_label", label_id)
            current.setdefault(i, set()).add(label_id)

    if archive:
        to_archive = [d["id"] for d in decisions if d["archive"] and "INBOX" in current.get(d["id"], set())]
        if to_archive:
            gmail.modify(to_archive, remove=["INBOX"])
            for i in to_archive:
                store.log_action(run_id, i, "remove_label", "INBOX")
                current[i].discard("INBOX")

    for i, labels in current.items():
        store.set_labels(i, sorted(labels))
    store.commit()
    return run_id


def trash_suggested(gmail, store: Store, cfg: Config) -> tuple[str, int]:
    """Trash every message that still carries the Suggested-Delete label in Gmail.

    Gmail is the source of truth: removing the label from a message in Gmail rescues it.
    """
    ids = gmail.list_ids(f'label:"{cfg.suggested_delete_label}"', 100_000)
    run_id = uuid.uuid4().hex[:12]
    done = gmail.trash(ids)
    for i in done:
        store.log_action(run_id, i, "trash")
    store.commit()
    return run_id, len(done)


def undo(gmail, store: Store, run_id: str | None = None) -> tuple[str | None, int]:
    """Reverse every action from one run (default: the most recent)."""
    run_id = run_id or store.last_run_id()
    if not run_id:
        return None, 0
    actions = store.run_actions(run_id)
    by_op: dict[tuple[str, str], list[str]] = defaultdict(list)
    for a in actions:
        by_op[(a["op"], a["label_id"])].append(a["message_id"])
    current = {r.id: set(r.label_ids) for r in store.emails()}
    for (op, label_id), ids in by_op.items():
        if op == "trash":
            gmail.untrash(ids)
        elif op == "add_label":
            gmail.modify(ids, remove=[label_id])
            for i in ids:
                current.get(i, set()).discard(label_id)
        elif op == "remove_label":
            gmail.modify(ids, add=[label_id])
            for i in ids:
                current.get(i, set()).add(label_id)
    for i in {a["message_id"] for a in actions if a["message_id"] in current}:
        store.set_labels(i, sorted(current[i]))
    store.delete_run(run_id)
    return run_id, len(actions)
