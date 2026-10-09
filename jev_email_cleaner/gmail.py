"""Thin wrapper over the Gmail API. Everything mailbox-facing goes through here."""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path

from .email_record import METADATA_HEADERS

# modify = read, label, archive, trash/untrash. It cannot permanently delete:
# trashed mail stays recoverable in Gmail's Trash for 30 days.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
BATCH = 50  # Gmail recommends at most 50 requests per batch


def authorize(credentials_path: str | Path, token_path: str | Path, headless: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if Path(token_path).exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not Path(credentials_path).exists():
            raise FileNotFoundError(
                f"{credentials_path} not found. Create a Desktop OAuth client in Google Cloud Console "
                "(see README) and save its JSON there."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=not headless)
    Path(token_path).write_text(creds.to_json())
    Path(token_path).chmod(0o600)
    return creds


def chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class Gmail:
    def __init__(self, creds):
        from googleapiclient.discovery import build

        self.svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.users = self.svc.users()

    def list_ids(self, query: str, limit: int) -> list[str]:
        ids: list[str] = []
        token = None
        while len(ids) < limit:
            resp = self.users.messages().list(
                userId="me", q=query, pageToken=token, maxResults=min(500, limit - len(ids))
            ).execute()
            ids += [m["id"] for m in resp.get("messages", [])]
            token = resp.get("nextPageToken")
            if not token:
                break
        return ids[:limit]

    def _batch(self, requests: list[tuple[str, object]]) -> tuple[dict[str, dict], list[str]]:
        results: dict[str, dict] = {}
        failed: list[str] = []

        def cb(request_id, response, exception):
            if exception is None:
                results[request_id] = response
            else:
                failed.append(request_id)

        for group in chunks(requests, BATCH):
            batch = self.svc.new_batch_http_request(callback=cb)
            for rid, req in group:
                batch.add(req, request_id=rid)
            batch.execute()
        return results, failed

    def get_messages(self, ids: list[str], full: bool, headers_only: list[str] | None = None) -> list[dict]:
        """Fetch messages in batches; retries rate-limited items once after a pause."""
        def req(mid: str):
            if full:
                return self.users.messages().get(userId="me", id=mid, format="full")
            return self.users.messages().get(
                userId="me", id=mid, format="metadata", metadataHeaders=headers_only or METADATA_HEADERS
            )

        results, failed = self._batch([(m, req(m)) for m in ids])
        if failed:
            time.sleep(2)
            more, _ = self._batch([(m, req(m)) for m in failed])
            results.update(more)
        return [results[m] for m in ids if m in results]

    def ensure_labels(self, names: Iterable[str]) -> dict[str, str]:
        existing = {l["name"]: l["id"] for l in self.users.labels().list(userId="me").execute().get("labels", [])}
        for name in names:
            if name not in existing:
                created = self.users.labels().create(
                    userId="me",
                    body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
                ).execute()
                existing[name] = created["id"]
        return existing

    def modify(self, ids: list[str], add: list[str] | None = None, remove: list[str] | None = None) -> None:
        for group in chunks(ids, 1000):  # batchModify limit
            self.users.messages().batchModify(
                userId="me", body={"ids": group, "addLabelIds": add or [], "removeLabelIds": remove or []}
            ).execute()

    def trash(self, ids: list[str]) -> list[str]:
        """Move to Trash (recoverable for 30 days). Returns ids that succeeded."""
        results, _ = self._batch([(m, self.users.messages().trash(userId="me", id=m)) for m in ids])
        return list(results)

    def untrash(self, ids: list[str]) -> list[str]:
        results, _ = self._batch([(m, self.users.messages().untrash(userId="me", id=m)) for m in ids])
        return list(results)
