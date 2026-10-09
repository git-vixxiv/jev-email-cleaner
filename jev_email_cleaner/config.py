"""Configuration: YAML file merged over defaults."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_TOPICS: dict[str, str] = {
    "Finance": "Banking, credit cards, investments, invoices, bills, tax documents, payment receipts",
    "Work": "Clients, colleagues, projects, business operations, vendors",
    "Shopping": "Order confirmations, shipping and delivery updates, returns, retailer accounts",
    "Travel": "Flights, hotels, car rentals, itineraries, boarding passes, loyalty programs",
    "Newsletters": "Editorial newsletters, digests, blogs, industry news the recipient subscribed to",
    "Promotions": "Sales, discounts, coupons, product marketing, upsells",
    "Accounts & Security": "Password resets, sign-in alerts, two-factor codes, account and service notices",
    "Personal": "Friends and family, personal correspondence, personal matters",
}


@dataclass
class Policy:
    min_age_days: int = 30
    protect_threshold: float = 0.35
    delete_disposable_min: float = 0.80
    delete_value_max: float = 0.9
    review_disposable_min: float = 0.50
    review_value_max: float = 1.6
    min_confidence: float = 0.45


@dataclass
class Config:
    sync_query: str = "-in:starred -in:chats"
    sync_max_messages: int = 2000
    sent_scan_max: int = 1000
    body_chars: int = 1200
    model: str = "jev-latest"
    concurrency: int = 8
    label_prefix: str = "Jev"
    topics: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOPICS))
    archive_topics: list[str] = field(default_factory=lambda: ["Newsletters", "Promotions"])
    policy: Policy = field(default_factory=Policy)

    @property
    def suggested_delete_label(self) -> str:
        return f"{self.label_prefix}/Suggested-Delete"

    def topic_label(self, topic: str) -> str:
        return f"{self.label_prefix}/{topic}"


def load_config(path: str | Path | None) -> Config:
    cfg = Config()
    if path is None or not Path(path).exists():
        return cfg
    raw = yaml.safe_load(Path(path).read_text()) or {}
    policy_raw = raw.pop("policy", None) or {}
    for key, value in raw.items():
        if not hasattr(cfg, key):
            raise ValueError(f"Unknown config key: {key}")
        setattr(cfg, key, value)
    for key, value in policy_raw.items():
        if not hasattr(cfg.policy, key):
            raise ValueError(f"Unknown policy key: {key}")
        setattr(cfg.policy, key, value)
    if not cfg.topics:
        raise ValueError("Config must define at least one topic")
    unknown = set(cfg.archive_topics) - set(cfg.topics)
    if unknown:
        raise ValueError(f"archive_topics not in topics: {sorted(unknown)}")
    return cfg
