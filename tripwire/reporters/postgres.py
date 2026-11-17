"""Record every detected change in Postgres, so there is a history to query."""

from __future__ import annotations

import sys

import psycopg
from urlwatch.reporters import ReporterBase


class PostgresReporter(ReporterBase):
    """Store each new, changed or failed watcher result in the `changes` table"""

    __kind__ = "postgres"

    def submit(self):
        dsn = self.config.get("dsn", "")
        rows = [self._row(state) for state in self.job_states if state.verb != "unchanged"]
        if not rows:
            return
        try:  # history is a bonus: a database outage must never stop the other reporters
            with psycopg.connect(dsn) as conn:
                conn.cursor().executemany(
                    "INSERT INTO changes (watcher, url, verb, old_content, new_content, diff)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    rows,
                )
        except psycopg.Error as e:
            print(
                f"postgres reporter: could not record {len(rows)} result(s): {e}", file=sys.stderr
            )

    @staticmethod
    def _row(state) -> tuple:
        job = state.job
        if state.verb == "error":
            detail = f"{type(state.exception).__name__}: {state.exception}"
            return (job.pretty_name(), job.get_location(), "error", state.old_data, None, detail)
        diff = state.get_diff() if state.verb == "changed" else None
        return (
            job.pretty_name(), job.get_location(), state.verb,
            state.old_data, state.new_data, diff,
        )  # fmt: skip
