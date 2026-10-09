"""jevclean: categorize and clean up Gmail with TypeSafe Jev."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from .config import load_config
from .store import Store


def _gmail(args):
    from .gmail import Gmail, authorize

    return Gmail(authorize(args.credentials, Path(args.data) / "token.json", headless=args.headless))


def _store(args) -> Store:
    return Store(Path(args.data) / "mailbox.sqlite")


def cmd_auth(args, cfg):
    _gmail(args)
    print(f"Authorized. Token saved to {Path(args.data) / 'token.json'}")


def cmd_sync(args, cfg):
    from .pipeline import sync

    sync(_gmail(args), _store(args), cfg)


def cmd_import_json(args, cfg):
    import json

    from .pipeline import import_json

    n = import_json(_store(args), json.loads(Path(args.file).read_text()))
    print(f"Imported {n} messages from {args.file}")


def cmd_import_mbox(args, cfg):
    from .mbox import import_mbox

    n, sent = import_mbox(_store(args), args.file, cfg.body_chars)
    print(f"Imported {n} messages from {args.file}; scanned {sent} sent messages for people you emailed")


def cmd_export_mbox(args, cfg):
    from .mbox import export_mbox

    counts = export_mbox(_store(args), args.file, args.out)
    for name, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:6}  {name}.mbox")


def cmd_estimate(args, cfg):
    from .judge import estimate_cost
    from .pipeline import pending

    todo = pending(_store(args), cfg)
    tokens, usd = estimate_cost(todo, cfg)
    print(f"{len(todo)} emails to judge, ~{tokens:,} input tokens, ~${usd:.2f}")


def cmd_classify(args, cfg):
    from typesafe_sdk import AsyncTypeSafeClient

    from .judge import estimate_cost
    from .pipeline import classify, pending

    if not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("Set TYPESAFE_API_KEY (create one at https://console.typesafe.ai/).")
    store = _store(args)
    todo = pending(store, cfg)[: args.limit]
    tokens, usd = estimate_cost(todo, cfg)
    print(f"Judging {len(todo)} emails, ~{tokens:,} tokens, ~${usd:.2f}")
    if usd > args.max_cost:
        sys.exit(f"Estimated cost exceeds --max-cost ${args.max_cost:.2f}. Use --limit or raise --max-cost.")

    ok, bad = classify(store, cfg, AsyncTypeSafeClient, args.limit)
    print(f"Judged {ok}, failed {bad}. Total tokens to date: {store.judgment_tokens():,}")


def cmd_plan(args, cfg):
    from .pipeline import plan
    from .report import write_reports

    store = _store(args)
    plan(store, cfg)
    decisions = store.decisions()
    if not decisions:
        sys.exit("No judged emails yet. Run sync and classify first.")
    emails = {e.id: e for e in store.emails()}
    html_path, csv_path = write_reports(emails, decisions, args.reports, cfg.label_prefix)
    counts = Counter(d["action"] for d in decisions)
    print(f"{len(decisions)} emails: {counts['delete']} delete, {counts['review']} review, {counts['keep']} keep, "
          f"{sum(d['archive'] for d in decisions)} to archive")
    print(f"Report: {html_path}\nCSV:    {csv_path}")


def cmd_decisions(args, cfg):
    import json

    from .pipeline import import_decisions

    accepted, skipped = import_decisions(_store(args), json.loads(Path(args.file).read_text()))
    print(f"Saved {accepted} decisions" + (f", skipped {skipped} unknown or invalid" if skipped else "")
          + ". Run `jevclean plan` to rebuild the plan with them.")


def cmd_apply(args, cfg):
    from .pipeline import apply_labels, trash_suggested

    gmail, store = _gmail(args), _store(args)
    if args.trash:
        if not args.yes:
            answer = input(f"Trash every message labeled {cfg.suggested_delete_label}? [y/N] ")
            if answer.strip().lower() != "y":
                sys.exit("Cancelled.")
        run_id, n = trash_suggested(gmail, store, cfg)
        print(f"Trashed {n} messages (recoverable 30 days). Undo with: jevclean undo --run {run_id}")
        return
    if not store.decisions():
        sys.exit("No plan yet. Run: jevclean plan")
    run_id = apply_labels(gmail, store, cfg, mark_suggested=not args.no_mark, archive=not args.no_archive)
    print(f"Labels applied. Review '{cfg.suggested_delete_label}' in Gmail, remove the label from anything to keep, "
          f"then run: jevclean apply --trash\nUndo with: jevclean undo --run {run_id}")


def cmd_undo(args, cfg):
    from .pipeline import undo

    run_id, n = undo(_gmail(args), _store(args), args.run)
    print(f"Reverted {n} actions from run {run_id}" if run_id else "Nothing to undo.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jevclean", description=__doc__)
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--data", default="data", help="local cache directory (holds mailbox data and OAuth token)")
    p.add_argument("--credentials", default="credentials.json", help="Google OAuth Desktop client JSON")
    p.add_argument("--reports", default="reports")
    p.add_argument("--headless", action="store_true", help="print the OAuth URL instead of opening a browser")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("auth", help="connect to Gmail").set_defaults(fn=cmd_auth)
    sub.add_parser("sync", help="download message metadata/excerpts to the local cache").set_defaults(fn=cmd_sync)
    ij = sub.add_parser("import-json", help="load messages exported via the Gmail connector (no OAuth needed)")
    ij.add_argument("file")
    ij.set_defaults(fn=cmd_import_json)
    im = sub.add_parser("import-mbox", help="load a Google Takeout MBOX (full bodies, no OAuth needed)")
    im.add_argument("file")
    im.set_defaults(fn=cmd_import_mbox)
    em = sub.add_parser("export-mbox", help="split a Takeout MBOX into one MBOX per folder using the plan")
    em.add_argument("file", help="the same Takeout MBOX passed to import-mbox")
    em.add_argument("--out", default="export", help="output directory")
    em.set_defaults(fn=cmd_export_mbox)
    sub.add_parser("estimate", help="estimate Jev cost for unjudged mail").set_defaults(fn=cmd_estimate)

    c = sub.add_parser("classify", help="ask Jev about each unjudged email")
    c.add_argument("--limit", type=int, default=None)
    c.add_argument("--max-cost", type=float, default=5.0, help="abort if estimated USD exceeds this")
    c.set_defaults(fn=cmd_classify)

    sub.add_parser("plan", help="apply policy and write an HTML/CSV report (no Gmail changes)").set_defaults(
        fn=cmd_plan)

    dc = sub.add_parser("decisions", help="load keep/delete choices exported from the review page")
    dc.add_argument("file")
    dc.set_defaults(fn=cmd_decisions)

    a = sub.add_parser("apply", help="label/archive in Gmail, or trash the reviewed suggestions")
    a.add_argument("--no-archive", action="store_true")
    a.add_argument("--no-mark", action="store_true", help="skip the Suggested-Delete label")
    a.add_argument("--trash", action="store_true", help="trash messages still labeled Suggested-Delete")
    a.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    a.set_defaults(fn=cmd_apply)

    u = sub.add_parser("undo", help="reverse the last apply (or --run ID)")
    u.add_argument("--run", default=None)
    u.set_defaults(fn=cmd_undo)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.fn(args, load_config(args.config))


if __name__ == "__main__":
    main()
