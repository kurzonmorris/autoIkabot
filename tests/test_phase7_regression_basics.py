import pytest

from autoIkabot.ui.prompts import ReturnToMainMenu, read, read_input
from autoIkabot.utils import process


def test_get_process_health_prefix_contract():
    assert process.get_process_health({"status": "[WAITING] idle"}) == "WAITING"
    assert process.get_process_health({"status": "[PROCESSING] doing"}) == "PROCESSING"
    assert process.get_process_health({"status": "[PAUSED] stopped"}) == "PAUSED"
    assert process.get_process_health({"status": "[BROKEN] fail"}) == "BROKEN"


def test_get_process_health_frozen_fallback(monkeypatch):
    monkeypatch.setattr(process.time, "time", lambda: 1_000.0)
    entry = {"status": "running", "last_heartbeat": 1_000.0 - process.HEARTBEAT_STALE_THRESHOLD - 1}
    assert process.get_process_health(entry) == "FROZEN"


def test_sleep_with_heartbeat_refreshes_status(monkeypatch):
    sleeps = []

    class FakeSession:
        def __init__(self):
            self._status = "[WAITING] test"
            self.calls = []

        def setStatus(self, status):
            self.calls.append(status)

    fake = FakeSession()

    def fake_sleep(v):
        sleeps.append(v)

    monkeypatch.setattr(process.time, "sleep", fake_sleep)
    process.sleep_with_heartbeat(fake, seconds=650, interval=300)

    # Sleeps in chunks and refreshes between chunks only.
    assert sleeps == [300, 300, 50]
    assert fake.calls == ["[WAITING] test", "[WAITING] test"]


def test_global_escape_token_read_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "\\")
    with pytest.raises(ReturnToMainMenu):
        read_input("Prompt: ")


def test_global_escape_token_read(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "\\")
    with pytest.raises(ReturnToMainMenu):
        read(msg="Prompt: ")
