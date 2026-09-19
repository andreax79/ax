"""SQLite-backed LLM usage metrics.

This module stores per-day token counts for each model in a small local
SQLite database. It is intentionally minimal: write paths create the schema on
first use, and read paths expose aggregated totals for a single day or month.
"""

from __future__ import annotations

import sqlite3
import typing as t
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .utils import CONFIG_DIR

USAGE_DB_PATH = CONFIG_DIR / "usage.sqlite3"


class UsageTotals(t.NamedTuple):
    """Aggregated token usage for a single model."""

    model: str
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        """Return the sum of input and output tokens."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class UsageDB:
    """Persist and query token usage in a local SQLite database."""

    path: Path = USAGE_DB_PATH

    def __post_init__(self) -> None:
        """Create the database directory and initialize the schema if needed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_usage (
                    usage_date TEXT NOT NULL,
                    model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (usage_date, model)
                )
            """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_usage_date ON llm_usage(usage_date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage(model)"
            )
            conn.commit()

    def record(
        self, usage_date: str, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        """Add token usage for a model on a specific date.

        If a row for the same date and model already exists, the new token counts
        are added to the existing totals.
        """
        with sqlite3.connect(self.path) as conn:
            conn.execute(
                """
                INSERT INTO llm_usage (usage_date, model, input_tokens, output_tokens)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(usage_date, model) DO UPDATE SET
                    input_tokens = input_tokens + excluded.input_tokens,
                    output_tokens = output_tokens + excluded.output_tokens
                """,
                (usage_date, model, input_tokens, output_tokens),
            )
            conn.commit()

    def record_today(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """Convenience wrapper that records usage under today's date."""
        self.record(date.today().isoformat(), model, input_tokens, output_tokens)

    def totals_for_date(self, usage_date: str) -> t.Iterable[UsageTotals]:
        """Yield per-model totals for a single day."""
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT model, COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0)
                FROM llm_usage
                WHERE usage_date = ?
                GROUP BY model
                """,
                (usage_date,),
            ).fetchall()
            for row in rows:
                yield UsageTotals(row[0], int(row[1]), int(row[2]))

    def totals_for_today(self) -> t.Iterable[UsageTotals]:
        """Yield per-model totals for today."""
        return self.totals_for_date(date.today().isoformat())

    def totals_for_month(
        self, usage_date: str | None = None
    ) -> t.Iterable[UsageTotals]:
        """Yield per-model totals for the month containing the given date.

        If no date is supplied, the current date is used.
        """
        if usage_date is None:
            usage_date = date.today().isoformat()
        month_prefix = usage_date[:7]
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute(
                """
                SELECT model, COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0)
                FROM llm_usage
                WHERE usage_date LIKE ?
                GROUP BY model
                """,
                (f"{month_prefix}%",),
            ).fetchall()
            for row in rows:
                yield UsageTotals(row[0], int(row[1]), int(row[2]))
