"""Telegram, written for a phone: short messages, and a failing watcher is reported once.

This replaces urlwatch's built-in `telegram` reporter (same name, same config).

Format. One block per watcher: its name, then one line per item. The API url, the numbered
summary, diff headers and urlwatch's footer are left out, because the item lines already carry
the link that matters. When a watcher only added lines (`additions_only`) the leading "+" is
dropped; when it also removed some, the "+" and "-" stay so a change reads as before and after.
A failure is one line with the reason.

Muting. urlwatch repeats an error alert on every run once a job has failed `max_tries` times.
In a 60 second loop a revoked token would mean hundreds of messages an hour, so the same
watcher's error is sent at most once per `error_every` minutes (default 60). Changes are never
muted, and the first non-error result for a watcher resets the clock, so the next outage
alerts straight away.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from urlwatch.reporters import TelegramReporter
from urlwatch.util import chunkstring

# the "--- @ <date>" and "+++ @ <date>" lines urlwatch puts at the top of every diff
DIFF_HEADER = re.compile(r"^(\+\+\+|---) @")

ROOT = Path(__file__).resolve().parent.parent.parent


def state_file() -> Path:
    return Path(os.environ.get("TRIPWIRE_STATE_DIR", ROOT / "state")) / "telegram-errors.json"


class ThrottledTelegramReporter(TelegramReporter):
    """Send a message using Telegram, muting repeated errors from the same watcher"""

    __kind__ = "telegram"  # takes over from urlwatch's own, so the config key stays `telegram`

    def render(self, state) -> str | None:
        """The message block for one watcher result, or None when there is nothing to say."""
        name = state.job.pretty_name()
        if state.verb == "error":
            reason = (state.traceback or "").strip().splitlines()
            return f"⚠️ {name} failed\n{reason[-1] if reason else 'unknown error'}"
        lines = [
            line
            for line in (state.get_diff() or "").splitlines()
            if line[:1] in "+-" and not DIFF_HEADER.match(line)
        ]
        if not lines:
            return None
        if not any(line.startswith("-") for line in lines):
            lines = [line[1:] for line in lines]  # pure additions: the "+" says nothing
        return "\n".join([name, *lines])

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
        return self.deliver()

    def deliver(self):
        blocks = [self.render(s) for s in self.report.get_filtered_job_states(self.job_states)]
        text = "\n\n".join(block for block in blocks if block)
        if not text:
            return None
        chat_ids = self.config["chat_id"]
        chat_ids = [chat_ids] if isinstance(chat_ids, str) else chat_ids
        result = None
        for chunk in chunkstring(text, self.MAX_LENGTH, numbering=True):
            for chat_id in chat_ids:
                result = self.submitToTelegram(self.config["bot_token"], chat_id, chunk)
        return result
