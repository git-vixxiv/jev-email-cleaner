"""SQLite cache of messages, Jev judgments, decisions and an undo log."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .email_record import EmailRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    id TEXT PRIMARY KEY, thread_id TEXT, sender_name TEXT, sender_email TEXT,
    subject TEXT, snippet TEXT, body TEXT, internal_ms INTEGER, label_ids TEXT,
    list_unsubscribe TEXT, is_list_mail INTEGER, has_attachment INTEGER
);
CREATE TABLE IF NOT EXISTS correspondents (email TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS judgments (
    id TEXT PRIMARY KEY, question_hash TEXT, model TEXT, answers TEXT,
    input_tokens INTEGER, created REAL
);
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY, topic TEXT, action TEXT, archive INTEGER, reasons TEXT,
    disposable REAL, lasting_value REAL, protect REAL
);
CREATE TABLE IF NOT EXISTS action_log (
    run_id TEXT, message_id TEXT, op TEXT, label_id TEXT, ts REAL
);
"""


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- emails -------------------------------------------------------
    def upsert_emails(self, records: list[EmailRecord]) -> None:
        rows = [r.to_row() for r in records]
        if not rows:
            return
        cols = list(rows[0])
        self.db.executemany(
            f"INSERT OR REPLACE INTO emails ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [tuple(r[c] for c in cols) for r in rows],
        )
        self.db.commit()

    def emails(self) -> list[EmailRecord]:
        return [EmailRecord.from_row(dict(r)) for r in self.db.execute("SELECT * FROM emails")]

    def known_ids(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT id FROM emails")}

    def set_labels(self, message_id: str, label_ids: list[str]) -> None:
        self.db.execute("UPDATE emails SET label_ids=? WHERE id=?", (",".join(label_ids), message_id))
        self.db.commit()

    # --- correspondents -----------------------------------------------
    def add_correspondents(self, addrs: set[str]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO correspondents VALUES (?)", [(a,) for a in addrs])
        self.db.commit()

    def correspondents(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT email FROM correspondents")}

    # --- judgments ----------------------------------------------------
    def save_judgment(self, message_id: str, question_hash: str, model: str, answers: dict, tokens: int) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO judgments VALUES (?,?,?,?,?,?)",
            (message_id, question_hash, model, json.dumps(answers), tokens, time.time()),
        )
        self.db.commit()

    def judgments(self, question_hash: str | None = None) -> dict[str, dict]:
        sql, args = "SELECT id, answers FROM judgments", ()
        if question_hash:
            sql, args = sql + " WHERE question_hash=?", (question_hash,)
        return {r["id"]: json.loads(r["answers"]) for r in self.db.execute(sql, args)}

    def judgment_tokens(self) -> int:
        return self.db.execute("SELECT COALESCE(SUM(input_tokens),0) FROM judgments").fetchone()[0]

    # --- decisions ----------------------------------------------------
    def save_decisions(self, decisions: list) -> None:
        self.db.execute("DELETE FROM decisions")
        self.db.executemany(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)",
            [
                (d.message_id, d.topic, d.action, int(d.archive), json.dumps(d.reasons),
                 d.disposable, d.lasting_value, d.protect)
                for d in decisions
            ],
        )
        self.db.commit()

    def decisions(self) -> list[dict]:
        out = []
        for r in self.db.execute("SELECT * FROM decisions"):
            d = dict(r)
            d["reasons"] = json.loads(d["reasons"])
            d["archive"] = bool(d["archive"])
            out.append(d)
        return out

    # --- undo log -----------------------------------------------------
    def log_action(self, run_id: str, message_id: str, op: str, label_id: str = "") -> None:
        self.db.execute("INSERT INTO action_log VALUES (?,?,?,?,?)", (run_id, message_id, op, label_id, time.time()))

    def commit(self) -> None:
        self.db.commit()

    def last_run_id(self) -> str | None:
        row = self.db.execute("SELECT run_id FROM action_log ORDER BY ts DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def run_actions(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM action_log WHERE run_id=? ORDER BY ts DESC", (run_id,))]

    def delete_run(self, run_id: str) -> None:
        self.db.execute("DELETE FROM action_log WHERE run_id=?", (run_id,))
        self.db.commit()
