"""The telegram reporter mutes repeats of the same watcher's error, never real changes."""

import datetime

import pytest
from urlwatch.reporters import ReporterBase, TelegramReporter

from tripwire.reporters.telegram import ThrottledTelegramReporter


class Job:
    def __init__(self, name):
        self.name = name

    def get_guid(self):
        return self.name

    def pretty_name(self):
        return self.name

    def get_location(self):
        return f"http://example.invalid/{self.name}"


class State:
    def __init__(self, verb, job):
        self.verb, self.job = verb, job
        self.old_data, self.new_data = "old", "new"
        self.traceback = "Traceback: boom"

    def get_diff(self):
        return "+a new line"


class Report:
    def __init__(self):
        text = {"line_length": 0, "details": True, "footer": False, "minimal": False}
        self.config = {"report": {"text": text}}

    def get_filtered_job_states(self, states):
        yield from states


@pytest.fixture
def telegram(monkeypatch, tmp_path):
    """Returns send(states, **config) -> the messages that would have gone to Telegram."""
    monkeypatch.setenv("TRIPWIRE_STATE_DIR", str(tmp_path))
    messages: list[str] = []
    monkeypatch.setattr(
        TelegramReporter,
        "submitToTelegram",
        lambda self, token, chat, text: (
            messages.append(text) or type("R", (), {"status_code": 200})
        ),
    )

    def send(states, **config):
        cfg = {"bot_token": "t", "chat_id": "1", **config}
        before = len(messages)
        ThrottledTelegramReporter(Report(), cfg, states, datetime.timedelta()).submit()
        return messages[before:]

    return send


def test_it_replaces_urlwatchs_own_telegram_reporter():
    import hooks  # noqa: F401

    assert ReporterBase.__subclasses__["telegram"] is ThrottledTelegramReporter


def test_the_same_error_is_sent_once_not_on_every_pass(telegram):
    job = Job("feed")
    assert "ERROR: feed" in telegram([State("error", job)])[0]
    assert telegram([State("error", job)]) == []
    assert telegram([State("error", job)]) == []


def test_each_watcher_has_its_own_clock(telegram):
    assert telegram([State("error", Job("a"))])
    assert telegram([State("error", Job("b"))])
    assert telegram([State("error", Job("a"))]) == []


def test_real_changes_are_never_muted(telegram):
    job = Job("feed")
    telegram([State("error", job)])
    for _ in range(3):
        (message,) = telegram([State("changed", job)])
        assert "+a new line" in message


def test_recovering_resets_the_clock_so_the_next_outage_alerts_straight_away(telegram):
    job = Job("feed")
    assert telegram([State("error", job)])
    assert telegram([State("error", job)]) == []
    telegram([State("unchanged", job)])  # it recovered
    assert telegram([State("error", job)])


def test_error_every_zero_turns_the_throttle_off(telegram):
    job = Job("feed")
    assert telegram([State("error", job)], error_every=0)
    assert telegram([State("error", job)], error_every=0)


def test_the_window_is_configurable_in_minutes(telegram, monkeypatch):
    import tripwire.reporters.telegram as module

    clock = [1_000_000.0]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])
    job = Job("feed")
    assert telegram([State("error", job)], error_every=10)
    clock[0] += 9 * 60
    assert telegram([State("error", job)], error_every=10) == []
    clock[0] += 2 * 60
    assert telegram([State("error", job)], error_every=10)


def test_an_unwritable_state_file_never_loses_an_alert(telegram, monkeypatch, tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the state directory should be")
    monkeypatch.setenv("TRIPWIRE_STATE_DIR", str(blocker))
    assert telegram([State("error", Job("feed"))])
