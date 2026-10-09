"""Turn Jev judgments + code-computed facts into decisions.

Rules, in order:
1. Hard protections from observed facts (starred, you have emailed the sender,
   a non-list message with an attachment, too recent) always win.
2. Any single protective judgment (record / personal / open action) above
   protect_threshold blocks deletion: an "any serious reason" rule, not a weighted sum.
3. Otherwise deletion needs strong disposable-kind evidence, low lasting value,
   and a confident kind judgment; weaker evidence goes to review.

Thresholds come from config, so tuning never requires re-running Jev.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import Config
from .email_record import EmailRecord
from .judge import DISPOSABLE_KINDS

KEEP, REVIEW, DELETE = "keep", "review", "delete"
# An unanswered "please do X" from years ago is almost always resolved or moot.
NEEDS_ACTION_RELEVANT_DAYS = 365


@dataclass
class Decision:
    message_id: str
    topic: str
    action: str
    archive: bool
    disposable: float
    lasting_value: float
    protect: float
    reasons: list[str] = field(default_factory=list)


def decide(
    rec: EmailRecord,
    answers: dict,
    cfg: Config,
    correspondents: set[str],
    now: datetime | None = None,
) -> Decision:
    p = cfg.policy
    age = rec.age_days(now)
    kind = answers["kind"]
    topic = answers["topic"]["choice"]
    disposable = sum(kind["probabilities"].get(k, 0.0) for k in DISPOSABLE_KINDS)
    lasting = answers["lasting_value"]["score"]

    signals = {
        "looks like a record worth keeping": answers["keep_record"]["noul"],
        "written personally to you": answers["personal"]["noul"],
    }
    if age < NEEDS_ACTION_RELEVANT_DAYS:
        signals["asks you to take an action"] = answers["needs_action"]["noul"]
    protect = max(signals.values())

    hard: list[str] = []
    if rec.starred:
        hard.append("starred")
    if rec.sender_email in correspondents:
        hard.append("you have emailed this sender")
    if rec.has_attachment and not rec.is_list_mail:
        hard.append("has an attachment and is not list mail")

    reasons: list[str] = []
    action = KEEP
    if hard:
        reasons += [f"protected: {h}" for h in hard]
    elif protect > p.protect_threshold:
        reasons += [f"protected: {name} ({prob:.0%})" for name, prob in signals.items() if prob > p.protect_threshold]
    elif age < p.min_age_days:
        reasons.append(f"too recent to suggest deletion ({age}d < {p.min_age_days}d)")
    else:
        evidence = f"{kind['choice']} ({disposable:.0%} disposable), lasting value {lasting:.1f}/3, {age}d old"
        strong = disposable >= p.delete_disposable_min and lasting <= p.delete_value_max
        if strong and kind["confidence"] >= p.min_confidence:
            action = DELETE
            reasons.append(evidence)
        elif strong or (disposable >= p.review_disposable_min and lasting <= p.review_value_max):
            action = REVIEW
            reasons.append(evidence)
            if strong:
                reasons.append(f"kind judgment uncertain (confidence {kind['confidence']:.2f})")

    is_protected = bool(hard) or protect > p.protect_threshold
    archive = topic in cfg.archive_topics and not is_protected and "INBOX" in rec.label_ids
    return Decision(rec.id, topic, action, archive, disposable, lasting, protect, reasons)
