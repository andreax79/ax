from __future__ import annotations

import sqlite3

from corecoder.usage_db import UsageDB, UsageTotals


def test_usage_db_returns_daily_and_monthly_totals(tmp_path):
    db = UsageDB(path=tmp_path / "usage.sqlite3")

    db.record("2026-01-01", "m1", 10, 5)
    db.record("2026-01-01", "m1", 2, 3)
    db.record("2026-02-01", "m2", 100, 50)

    assert next(db.totals_for_date("2026-01-01")) == UsageTotals("m1", 12, 8)
    assert next(db.totals_for_month("2026-02-01")) == UsageTotals("m2", 100, 50)


def test_usage_db_accumulates_by_date_and_mode(tmp_path):
    db = UsageDB(path=tmp_path / "usage.sqlite3")

    db.record("2026-01-01", "m1", 10, 5)
    db.record("2026-01-01", "m1", 2, 3)
    db.record("2026-01-01", "m2", 7, 1)

    with sqlite3.connect(db.path) as conn:
        rows = conn.execute(
            "SELECT usage_date, model, input_tokens, output_tokens FROM llm_usage ORDER BY model"
        ).fetchall()

    assert rows == [
        ("2026-01-01", "m1", 12, 8),
        ("2026-01-01", "m2", 7, 1),
    ]
