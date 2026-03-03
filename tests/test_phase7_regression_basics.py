import threading
from collections import deque

import pytest
import requests

from autoIkabot.ui.prompts import ReturnToMainMenu, read, read_input
from autoIkabot.ui import menu
from autoIkabot.utils import process
from autoIkabot.web.session import Session, SessionBrokenError


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


def test_terminate_background_tasks_non_processing_force_killed(monkeypatch):
    # PID 10 is non-processing and should be force-killed right away.
    entries = [{"pid": 10, "status": "idle"}, {"pid": 11, "status": "[PROCESSING] work"}]
    monkeypatch.setattr(process, "update_process_list", lambda session: entries)

    sent_signals = []
    monkeypatch.setattr(process.os, "kill", lambda pid, sig: sent_signals.append((pid, sig)))

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def is_running(self):
            # Processing PID exits before grace force-kill stage.
            return self.pid == 10

        def status(self):
            return "running"

    monkeypatch.setattr(process.psutil, "Process", FakeProc)
    monkeypatch.setattr(process.time, "sleep", lambda _: None)

    summary = process.terminate_background_tasks(session=object(), processing_grace_seconds=1)

    assert summary["total"] == 2
    assert summary["processing"] == 1
    assert summary["force_killed"] == 0
    # Expect both TERM attempts plus immediate kill for non-processing PID 10.
    assert any(pid == 10 for pid, _ in sent_signals)


def test_terminate_background_tasks_processing_force_killed_after_grace(monkeypatch):
    entries = [{"pid": 21, "status": "[PROCESSING] long op"}]
    monkeypatch.setattr(process, "update_process_list", lambda session: entries)

    sent_signals = []
    monkeypatch.setattr(process.os, "kill", lambda pid, sig: sent_signals.append((pid, sig)))

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def is_running(self):
            return True

        def status(self):
            return "running"

    monkeypatch.setattr(process.psutil, "Process", FakeProc)
    monkeypatch.setattr(process.time, "sleep", lambda _: None)

    ticks = iter([0.0, 1.1, 1.1])
    monkeypatch.setattr(process.time, "time", lambda: next(ticks))

    summary = process.terminate_background_tasks(session=object(), processing_grace_seconds=1)

    assert summary["total"] == 1
    assert summary["processing"] == 1
    assert summary["force_killed"] == 1
    # TERM + force kill on same processing PID.
    assert len([pid for pid, _ in sent_signals if pid == 21]) >= 2


def test_dispatch_module_auto_returns_false_on_child_escape(monkeypatch):
    class FakeSession:
        def to_dict(self):
            return {"username": "u", "mundo": "1", "servidor": "en"}

    class FakeEvent:
        def wait(self, timeout=0):
            return False

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            self._first = True

        def get_nowait(self):
            if self._first:
                self._first = False
                return "escaped"
            raise Exception("empty")

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 555
            self.exitcode = None

        def start(self):
            return None

        def is_alive(self):
            return True

        def terminate(self):
            return None

    monkeypatch.setattr(menu, "update_process_list", lambda session, new_processes=None: [])
    monkeypatch.setattr(menu.multiprocessing, "Event", FakeEvent)
    monkeypatch.setattr(menu.multiprocessing, "Queue", FakeQueue)
    monkeypatch.setattr(menu.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: 0)
    menu._RUNTIME_CHILD_PIDS.clear()

    result = menu.dispatch_module_auto(
        FakeSession(),
        {"name": "TestMod", "func": lambda *_: None, "background": True},
        [1, 2, 3],
    )

    assert result is False
    assert 555 in menu._RUNTIME_CHILD_PIDS


def test_dispatch_module_auto_times_out_and_terminates_child(monkeypatch):
    class FakeSession:
        def to_dict(self):
            return {"username": "u", "mundo": "1", "servidor": "en"}

    class FakeEvent:
        def wait(self, timeout=0):
            return False

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            pass

        def get_nowait(self):
            raise Exception("empty")

    terminated = {"value": False}

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 777
            self.exitcode = None

        def start(self):
            return None

        def is_alive(self):
            return True

        def terminate(self):
            terminated["value"] = True

    monkeypatch.setattr(menu, "update_process_list", lambda session, new_processes=None: [])
    monkeypatch.setattr(menu.multiprocessing, "Event", FakeEvent)
    monkeypatch.setattr(menu.multiprocessing, "Queue", FakeQueue)
    monkeypatch.setattr(menu.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: 0)
    clock = {"t": 0.0}

    def fake_time():
        current = clock["t"]
        clock["t"] += 61.0
        return current

    monkeypatch.setattr(menu.time, "time", fake_time)
    menu._RUNTIME_CHILD_PIDS.clear()

    result = menu.dispatch_module_auto(
        FakeSession(),
        {"name": "SlowMod", "func": lambda *_: None, "background": True},
        [],
    )

    assert result is False
    assert terminated["value"] is True


def test_session_get_marks_broken_on_retry_budget_exhausted(monkeypatch):
    class FakeHTTP:
        def get(self, *args, **kwargs):
            raise requests.exceptions.ConnectionError("boom")

    fake = type("S", (), {})()
    fake.url_base = "https://example.invalid/index.php?"
    fake.s = FakeHTTP()
    fake.request_history = deque(maxlen=5)
    fake._network_retry_budget = 2
    fake._enforce_rate_limit = lambda: None
    fake._try_extract_token = lambda *_: None
    fake._try_extract_city_id = lambda *_: None
    fake._is_maintenance = lambda *_: False
    fake._is_expired = lambda *_: False
    fake._handle_session_expired = lambda: None

    def mark_broken(code, detail):
        raise SessionBrokenError(f"{code}|{detail}")

    fake._mark_broken = mark_broken
    monkeypatch.setattr("autoIkabot.web.session.time.sleep", lambda *_: None)

    with pytest.raises(SessionBrokenError) as exc:
        Session.get(fake, url="view=city")

    assert "GET_RETRY_EXHAUSTED" in str(exc.value)


def test_session_post_marks_broken_on_request_id_retry_budget(monkeypatch):
    class FakeResp:
        status_code = 200

        class _Elapsed:
            @staticmethod
            def total_seconds():
                return 0.01

        elapsed = _Elapsed()
        text = "TXT_ERROR_WRONG_REQUEST_ID"

    class FakeHTTP:
        def post(self, *args, **kwargs):
            return FakeResp()

    fake = type("S", (), {})()
    fake.url_base = "https://example.invalid/index.php?"
    fake.s = FakeHTTP()
    fake.request_history = deque(maxlen=5)
    fake._network_retry_budget = 2
    fake._enforce_rate_limit = lambda: None
    fake._try_extract_token = lambda *_: None
    fake._try_extract_city_id = lambda *_: None
    fake._is_maintenance = lambda *_: False
    fake._is_expired = lambda *_: False
    fake._handle_session_expired = lambda: None
    fake._action_request_token = ""
    fake._extract_token = lambda: "abc"
    fake._token_lock = threading.Lock()

    def mark_broken(code, detail):
        raise SessionBrokenError(f"{code}|{detail}")

    fake._mark_broken = mark_broken

    with pytest.raises(SessionBrokenError) as exc:
        Session.post(fake, url="action=request", payload={"x": "1"}, params={})

    assert "POST_REQUEST_ID_EXHAUSTED" in str(exc.value)
