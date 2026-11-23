"""Telegram, but a watcher that keeps failing is reported once, not on every pass.

urlwatch repeats an error alert on every run once a job has failed `max_tries` times. In a
60 second loop a revoked token would mean hundreds of messages an hour. This replaces the
built-in `telegram` reporter (same name, same config) and mutes repeats of the same watcher's
error for `error_every` minutes (default 60). Changes are never muted, and the first
non-error result for a watcher resets the clock, so the next outage alerts straight away.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from urlwatch.reporters import TelegramReporter

ROOT = Path(__file__).resolve().parent.parent.parent


def state_file() -> Path:
    return Path(os.environ.get("TRIPWIRE_STATE_DIR", ROOT / "state")) / "telegram-errors.json"


class ThrottledTelegramReporter(TelegramReporter):
    """Send a message using Telegram, muting repeated errors from the same watcher"""

    __kind__ = "telegram"  # takes over from urlwatch's own, so the config key stays `telegram`

    def submit(self):
        window = float(self.config.get("error_every", 60)) * 60
        file = state_file()
        try:
            last_sent = json.loads(file.read_text())
        except (OSError, ValueError):
            last_sent = {}
        now, keep = time.time(), []
        for state in self.job_states:
            guid = state.job.get_guid()
            if state.verb != "error":
                last_sent.pop(guid, None)  # recovered: the next failure is news again
                keep.append(state)
            elif now - last_sent.get(guid, 0) >= window:
                last_sent[guid] = now
                keep.append(state)
        self.job_states = keep
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps(last_sent))
        except OSError:
            pass  # throttling is best effort: never lose an alert because a file is unwritable
        return super().submit()
