"""Test doubles: a Gmail mailbox in memory and a scripted Jev HTTP endpoint."""

from __future__ import annotations

import base64
import json

import httpx2
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

from jev_email_cleaner.judge import KIND_CRITERIA

DAY_MS = 86_400_000
NOW_MS = 1_790_000_000_000  # fixed "now" for tests


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def gmail_message(mid, sender, subject, body="", age_days=100, labels=("INBOX",), list_unsub="", attachment=False,
                  html=False):
    headers = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]
    if list_unsub:
        headers.append({"name": "List-Unsubscribe", "value": list_unsub})
    parts = [{"mimeType": "text/html" if html else "text/plain", "body": {"data": b64(body)}}]
    if attachment:
        parts.append({"mimeType": "application/pdf", "filename": "doc.pdf", "body": {"attachmentId": "a1"}})
    return {
        "id": mid, "threadId": "t" + mid, "snippet": body[:60], "labelIds": list(labels),
        "internalDate": str(NOW_MS - age_days * DAY_MS),
        "payload": {"mimeType": "multipart/mixed", "headers": headers, "parts": parts},
    }


class FakeGmail:
    def __init__(self, messages: list[dict], sent: list[dict] | None = None):
        self.messages = {m["id"]: m for m in messages}
        self.sent = {m["id"]: m for m in (sent or [])}
        self.labels = {"INBOX": "INBOX", "STARRED": "STARRED", "UNREAD": "UNREAD"}
        self.trashed: set[str] = set()

    def list_ids(self, query, limit):
        if query == "in:sent":
            return list(self.sent)[:limit]
        if query.startswith("label:"):
            label_id = self.labels.get(query.split('"')[1])
            return [m for m, msg in self.messages.items() if label_id in msg["labelIds"] and m not in self.trashed]
        return [m for m in self.messages if m not in self.trashed][:limit]

    def get_messages(self, ids, full, headers_only=None):
        pool = {**self.messages, **self.sent}
        return [pool[i] for i in ids]

    def ensure_labels(self, names):
        for n in names:
            self.labels.setdefault(n, "Label_" + n)
        return dict(self.labels)

    def modify(self, ids, add=None, remove=None):
        for i in ids:
            labels = self.messages[i]["labelIds"]
            for l in add or []:
                if l not in labels:
                    labels.append(l)
            for l in remove or []:
                if l in labels:
                    labels.remove(l)

    def trash(self, ids):
        self.trashed |= set(ids)
        return list(ids)

    def untrash(self, ids):
        self.trashed -= set(ids)
        return list(ids)


def scripted_answers(state: dict, questions: dict) -> dict:
    """Deterministic stand-in for Jev keyed off words in the subject."""
    subject = state["email"]["subject"].lower()
    if "sale" in subject:
        kind, topic, record, personal, action, value = "marketing", "Promotions", 0.02, 0.01, 0.05, 0.1
    elif "invoice" in subject:
        kind, topic, record, personal, action, value = "transactional", "Finance", 0.95, 0.05, 0.4, 2.8
    elif "lunch" in subject:
        kind, topic, record, personal, action, value = "correspondence", "Personal", 0.2, 0.97, 0.6, 1.5
    elif "maybe" in subject:  # borderline newsletter
        kind, topic, record, personal, action, value = "newsletter", "Newsletters", 0.1, 0.05, 0.1, 1.2
    else:
        kind, topic, record, personal, action, value = "notification", "Social", 0.03, 0.02, 0.05, 0.3
    answers = {}
    for key, q in questions.items():
        if key == "topic":
            probs = {o: (0.9 if o == topic else 0.1 / (len(q["criteria"]) - 1)) for o in q["criteria"]}
            answers[key] = {"type": "choice", "choice": topic, "probabilities": probs, "confidence": 0.8}
        elif key == "kind":
            p = 0.6 if kind == "newsletter" else 0.94
            probs = {o: (p if o == kind else (1 - p) / (len(KIND_CRITERIA) - 1)) for o in q["criteria"]}
            answers[key] = {"type": "choice", "choice": kind, "probabilities": probs, "confidence": 0.85}
        elif key in ("keep_record", "personal", "needs_action"):
            answers[key] = {"type": "noul", "noul": {"keep_record": record, "personal": personal,
                                                      "needs_action": action}[key]}
        elif key == "lasting_value":
            legend = {str(i): str(c) for i, c in enumerate(q["criteria"])}
            answers[key] = {"type": "score", "score": value, "legend": legend,
                            "probabilities": {k: 0.25 for k in legend}, "confidence": 0.7}
    return answers


class JevEndpoint:
    """Records requests and replies like POST /v1/systemone."""

    def __init__(self, fail_subjects: tuple[str, ...] = ()):
        self.requests: list[dict] = []
        self.fail_subjects = fail_subjects

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        if any(s in body["state"]["email"]["subject"] for s in self.fail_subjects):
            return httpx2.Response(422, json={"detail": "bad question"})
        return httpx2.Response(200, json={
            "model": "jev-1.13.0",
            "answers": scripted_answers(body["state"], body["questions"]),
            "usage": {"input_tokens": 500, "output_tokens": 20},
        })

    def client_factory(self):
        return lambda: AsyncTypeSafeClient(
            api_key="test", transport=httpx2.MockTransport(self.handler), retry=RetryPolicy(max_retries=0)
        )
