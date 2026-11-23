"""The telegram reporter: compact messages, and repeats of one watcher's error are muted."""

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


TRACEBACK = (
    'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\nValueError: boom\n'
)


class State:
    def __init__(self, verb, job, diff="+a new line"):
        self.verb, self.job, self.diff = verb, job, diff
        self.old_data, self.new_data = "old", "new"
        self.traceback = TRACEBACK

    def get_diff(self):
        return self.diff


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
    assert telegram([State("error", job)]) == ["⚠️ feed failed\nValueError: boom"]
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
        assert message == "feed\na new line"


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


MIXED = (
    "--- @\tMon, 23 Nov 2026 10:00:00 +0000\n+++ @\tMon, 23 Nov 2026 10:01:00 +0000\n"
    "@@ -1,3 +1,3 @@\n SEP-0001 | Active\n-SEP-0002 | Draft\n+SEP-0002 | Final\n SEP-0003 | Draft"
)


def test_new_items_are_the_watcher_name_then_one_plain_line_each(telegram):
    diff = "+💬 ada on #1: hi https://github.com/o/r/issues/1#issuecomment-5\n+🆕 issue #2 [open] x"
    assert telegram([State("changed", Job("feed"), diff)]) == [
        "feed\n💬 ada on #1: hi https://github.com/o/r/issues/1#issuecomment-5\n🆕 issue #2 [open] x"
    ]


def test_a_before_and_after_change_keeps_both_markers_and_drops_the_noise(telegram):
    (message,) = telegram([State("changed", Job("SEP index"), MIXED)])
    assert message == "SEP index\n-SEP-0002 | Draft\n+SEP-0002 | Final"


def test_the_message_never_carries_the_api_url_or_urlwatchs_footer(telegram):
    (message,) = telegram([State("changed", Job("feed"), MIXED)])
    for noise in ("example.invalid", "urlwatch", "CHANGED", "01.", "@@", "---", "+++"):
        assert noise not in message


def test_several_watchers_in_one_pass_are_separated_by_a_blank_line(telegram):
    states = [State("changed", Job("a"), "+one"), State("changed", Job("b"), "+two")]
    assert telegram(states) == ["a\none\n\nb\ntwo"]


def test_a_change_with_nothing_to_show_sends_nothing(telegram):
    assert telegram([State("changed", Job("feed"), "--- @ x\n+++ @ y\n")]) == []
