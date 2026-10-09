# jev-email-cleaner

Sorts a Gmail mailbox into topic labels and proposes mail to delete, using
[TypeSafe Jev](https://docs.typesafe.ai) for the semantic judgments and plain
code for every decision that touches your mailbox.

Nothing is deleted without two explicit steps from you, and every change can be undone.

## How it works

```
sync ──► classify ──► plan ──► apply ──► (review in Gmail) ──► apply --trash
 Gmail     Jev:        code:    labels,                          Trash
 → SQLite  6 typed     policy   archive,                         (30-day
           judgments   + report Suggested-Delete label            recovery)
```

| Step | What happens | Changes Gmail? |
| --- | --- | --- |
| `sync` | Downloads headers + a body excerpt for mail matching `sync_query`, and the recipients of your sent mail. Incremental. | No |
| `estimate` | Shows tokens and dollar cost before you spend anything. | No |
| `classify` | One Jev request per email, six questions in parallel. Cached; re-runs only judge new mail. | No |
| `plan` | Applies the deletion policy and writes `reports/plan-*.html` and `.csv`. Re-run freely after editing thresholds: no new Jev calls. | No |
| `apply` | Adds `Jev/<Topic>` labels, archives configured topics, labels deletion candidates `Jev/Suggested-Delete`. | Yes, reversible |
| `apply --trash` | Trashes whatever **still** carries `Jev/Suggested-Delete` in Gmail. Remove the label from anything you want to keep first. | Yes, reversible |
| `undo` | Reverses the last run (or `--run ID`): untrash, remove labels, restore Inbox. | Yes |

### What Jev decides vs. what code decides

Jev answers narrow semantic questions about each email
([`judge.py`](jev_email_cleaner/judge.py)):

| Question | Primitive | Used for |
| --- | --- | --- |
| `topic` | Choice over your folders + `Other` | Which label to apply |
| `kind` | Choice: correspondence, transactional, security, newsletter, marketing, notification, spam | Probability the email is a disposable kind |
| `keep_record` | Noul | Protection: receipts, statements, legal, medical, tax |
| `personal` | Noul | Protection: written by a human to you |
| `needs_action` | Noul | Protection: open obligation (only for mail under a year old) |
| `lasting_value` | Score, 4 levels | Value months from now |

Code owns everything else ([`policy.py`](jev_email_cleaner/policy.py)):

- **Hard protections** (no probability can override): starred, anyone you have emailed, non-list mail with an attachment, mail newer than `min_age_days`.
- **Any-reason protection**: if *any* protective Noul exceeds `protect_threshold`, the email is kept. This is deliberately not a weighted average.
- **Delete** needs high disposable-kind probability, low lasting value, and a confident `kind` answer. Weaker evidence goes to **review**.
- Dates, ages, sender counts and read rates are computed in code. Per TypeSafe's docs Jev is unreliable at date and number comparison, so it never sees them.
- Email text is treated as untrusted: every question tells Jev to ignore instructions inside the email, and the report HTML-escapes all content and blocks spreadsheet formula injection in the CSV.

### Extras

- **Unsubscribe candidates**: the report ranks frequent senders you rarely read and links their `List-Unsubscribe` target.
- **Cost guard**: `classify` aborts if the estimate exceeds `--max-cost` (default $5). At the published $0.042 per million input tokens, the estimator puts 10,000 emails at about $0.60 to $0.70 (~1,600 tokens each, mostly the question set, which is resent per email). `estimate` uses a chars/4 approximation; `classify` reports actual tokens billed.
- **Cache invalidation**: changing topics, question wording or model re-judges automatically; changing thresholds does not.

## Setup (about 15 minutes, most of it in Google Cloud Console)

1. **Python 3.11+**
   ```sh
   python -m venv .venv && source .venv/bin/activate
   pip install -e ".[dev]"
   ```
2. **TypeSafe key**: create one at <https://console.typesafe.ai/> and `export TYPESAFE_API_KEY=...`
3. **Gmail OAuth client** (one-time):
   1. Google Cloud Console → create a project → *APIs & Services → Library* → enable **Gmail API**.
   2. *OAuth consent screen*: choose **Internal** if this is a Google Workspace account (no token expiry issues), otherwise **External** and add yourself as a test user.
   3. *Credentials → Create credentials → OAuth client ID → Desktop app*. Download the JSON as `credentials.json` in this folder.
   4. `jevclean auth` and approve in the browser. Use `--headless` on a machine without a browser.

   External apps left in "Testing" status get refresh tokens that expire after 7 days; re-run `jevclean auth` when that happens.
4. **Config**: `cp config.example.yaml config.yaml` and edit topics, archive list and thresholds.

## First run

```sh
jevclean sync                 # pulls up to sync_max_messages
jevclean estimate
jevclean classify --limit 200 # start small, inspect, then run without --limit
jevclean plan                 # open reports/plan-*.html
jevclean apply                # labels + archive + Suggested-Delete
# In Gmail, open label Jev/Suggested-Delete, remove the label from anything to keep
jevclean apply --trash
jevclean undo                 # if anything looks wrong
```

**Tune on a sample first.** The thresholds in `config.example.yaml` are starting points, not tested values. Run 200 emails, read the review and delete lists, adjust `policy:` and re-run `plan` (free) until the delete list contains nothing you would keep.

## Data and privacy

- Mailbox data and the OAuth token stay in `data/` (git-ignored). The token file is written with `0600` permissions.
- Sender, subject, preview and up to `body_chars` of body text are sent to TypeSafe's API for each email. Set `body_chars: 0` to send only sender, subject and Gmail's preview snippet. TypeSafe states it does not train on customer requests; zero data retention is offered on enterprise plans ([docs](https://docs.typesafe.ai/models#data-handling)).
- If the mailbox holds privileged or regulated material (client communications, medical records), exclude it via `sync_query` (for example `-label:Clients`) or confirm TypeSafe's terms meet your obligations before syncing it.
- The OAuth scope is `gmail.modify`: read, label, archive, trash. The app cannot permanently delete mail.

## Development

```sh
pytest
```

Tests run the real TypeSafe SDK against a mock HTTP endpoint and a fake in-memory Gmail, so they need no keys and touch no mailbox.

## Roadmap ideas

- Sender-level batching: judge one representative per bulk sender and propagate, cutting cost and API calls on large mailboxes.
- Feedback loop: record your rescues from `Suggested-Delete` and use them to tune thresholds automatically.
- Gmail filters: export "always label/archive this sender" filters so new mail is sorted by Gmail itself.
- Scheduled runs (cron or a Claude Code routine) for weekly incremental cleanup.
- Local web UI for approving the plan instead of the Gmail-label review step.
