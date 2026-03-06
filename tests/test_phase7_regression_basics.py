import threading
import json
import os
from collections import deque
from contextlib import contextmanager

import pytest
import requests
from requests.cookies import RequestsCookieJar

from autoIkabot.ui.prompts import ReturnToMainMenu, read, read_input
import autoIkabot.ui.prompts as prompts
from autoIkabot.ui import menu
from autoIkabot.modules import autoLoader
from autoIkabot.modules import taskStatus as task_status_mod
import autoIkabot.modules.resourceTransportManager as rtm_mod
import autoIkabot.modules.constructionManager as cm_mod
import autoIkabot.modules.activateMiracle as am_mod
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


def test_session_mark_broken_child_reports_critical_error(monkeypatch):
    fake = type("S", (), {})()
    fake.is_parent = False
    statuses = []
    fake.setStatus = lambda status: statuses.append(status)

    reports = []
    import autoIkabot.utils.process as process_mod
    monkeypatch.setattr(process_mod, "report_critical_error", lambda *args: reports.append(args))

    with pytest.raises(SessionBrokenError) as exc:
        Session._mark_broken(fake, "X_CODE", "details")

    msg = str(exc.value)
    assert "X_CODE" in msg
    assert "details" in msg
    assert statuses and statuses[-1].startswith("[BROKEN] X_CODE")
    assert reports and reports[-1][1] == "Session"


def test_session_mark_broken_parent_does_not_report_critical_error(monkeypatch):
    fake = type("S", (), {})()
    fake.is_parent = True
    statuses = []
    fake.setStatus = lambda status: statuses.append(status)

    import autoIkabot.utils.process as process_mod
    monkeypatch.setattr(process_mod, "report_critical_error", lambda *args: (_ for _ in ()).throw(RuntimeError("should not report")))

    with pytest.raises(SessionBrokenError):
        Session._mark_broken(fake, "PARENT_CODE", "details")

    assert statuses and statuses[-1].startswith("[BROKEN] PARENT_CODE")


def test_session_mark_broken_still_raises_when_reporting_fails(monkeypatch):
    fake = type("S", (), {})()
    fake.is_parent = False

    def broken_status(_status):
        raise RuntimeError("status update failed")

    fake.setStatus = broken_status

    import autoIkabot.utils.process as process_mod
    monkeypatch.setattr(
        process_mod,
        "report_critical_error",
        lambda *args: (_ for _ in ()).throw(RuntimeError("report failed")),
    )

    with pytest.raises(SessionBrokenError) as exc:
        Session._mark_broken(fake, "REPORT_FAIL", "details")

    assert "REPORT_FAIL" in str(exc.value)


def test_child_entry_signals_escape_and_unblocks_parent(monkeypatch):
    class FakeQueue:
        def __init__(self):
            self.values = []

        def put_nowait(self, value):
            self.values.append(value)

    class FakeEvent:
        def __init__(self):
            self.called = 0

        def set(self):
            self.called += 1

    class FakeSessionObj:
        pass

    fake_queue = FakeQueue()
    fake_event = FakeEvent()

    monkeypatch.setattr(menu, "report_critical_error", lambda *args, **kwargs: None)

    # Replace imports done inside _child_entry
    import autoIkabot.web.session as session_mod
    monkeypatch.setattr(session_mod.Session, "from_dict", lambda data: FakeSessionObj())
    monkeypatch.setattr("autoIkabot.utils.logging.setup_account_logger", lambda *args, **kwargs: None)

    def fake_func(session, event, stdin_fd):
        raise ReturnToMainMenu()

    menu._child_entry(fake_func, {"username": "u", "mundo": "1", "servidor": "en"}, fake_event, 0, fake_queue)

    assert fake_queue.values == ["escaped"]
    assert fake_event.called == 1


def test_child_entry_signals_crash_and_reports_error(monkeypatch):
    class FakeQueue:
        def __init__(self):
            self.values = []

        def put_nowait(self, value):
            self.values.append(value)

    class FakeEvent:
        def __init__(self):
            self.called = 0

        def set(self):
            self.called += 1

    class FakeSessionObj:
        pass

    fake_queue = FakeQueue()
    fake_event = FakeEvent()
    reports = []

    monkeypatch.setattr(menu, "report_critical_error", lambda *args: reports.append(args))

    import autoIkabot.web.session as session_mod
    monkeypatch.setattr(session_mod.Session, "from_dict", lambda data: FakeSessionObj())
    monkeypatch.setattr("autoIkabot.utils.logging.setup_account_logger", lambda *args, **kwargs: None)

    def fake_func(session, event, stdin_fd):
        raise RuntimeError("boom")

    menu._child_entry(fake_func, {"username": "u", "mundo": "1", "servidor": "en"}, fake_event, 0, fake_queue)

    assert fake_queue.values == ["crashed"]
    assert fake_event.called == 1
    assert reports, "Expected crash to be reported as critical error"


def test_session_import_cookies_ignores_php_sessid_key():
    class FakeResp:
        text = "ok"

    class FakeHTTP:
        def __init__(self):
            self.cookies = RequestsCookieJar()

        def get(self, *args, **kwargs):
            return FakeResp()

    fake = type("S", (), {})()
    fake.host = "example.invalid"
    fake.url_base = "https://example.invalid/index.php?"
    fake.s = FakeHTTP()
    fake._is_expired = lambda *_: False
    fake._try_extract_token = lambda *_: None
    fake._try_extract_city_id = lambda *_: None

    ok = Session.import_cookies(
        fake,
        '{"ikariam":"IK123", "PHPSESSID":"SHOULD_NOT_BE_IMPORTED"}',
    )

    assert ok is True
    assert fake.s.cookies.get("ikariam") == "IK123"
    assert fake.s.cookies.get("PHPSESSID") is None


def test_handle_session_expired_safe_mode_uses_cookie_refresh_first(monkeypatch):
    # If safe-mode cookie refresh works, login should never be called.
    fake = type("S", (), {})()
    fake._continuity_mode = "safe"
    fake._try_refresh_from_ikariam_cookie = lambda: True

    import autoIkabot.core.login as login_mod
    monkeypatch.setattr(login_mod, "login", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("should not login")))

    Session._handle_session_expired(fake)


def test_handle_session_expired_relogin_updates_session(monkeypatch):
    class FreshHTTP:
        def __init__(self):
            self.headers = {}

    class LoginResult:
        def __init__(self):
            self.http_session = FreshHTTP()
            self.gf_token = "new-gf"
            self.blackbox_token = "new-bb"

    class OldHTTP:
        def __init__(self):
            self.headers = {}

    fake = type("S", (), {})()
    fake._continuity_mode = "aggressive"
    fake._account_info = {}
    fake.gf_token = "old-gf"
    fake.blackbox_token = "old-bb"
    fake.is_parent = False
    fake.s = OldHTTP()
    fake.game_headers = {"User-Agent": "x"}
    fake._proxy_active = False
    fake._action_request_token = "stale"

    import autoIkabot.core.login as login_mod
    monkeypatch.setattr(login_mod, "login", lambda *args, **kwargs: LoginResult())

    Session._handle_session_expired(fake)

    assert fake.gf_token == "new-gf"
    assert fake.blackbox_token == "new-bb"
    assert fake.s.headers.get("User-Agent") == "x"
    assert fake._action_request_token == ""
    # Old tokens are staged into account_info before login call.
    assert fake._account_info["gf_token"] == "old-gf"
    assert fake._account_info["blackbox_token"] == "old-bb"


def test_launch_saved_configs_skips_healthy_and_broken_launches_frozen(monkeypatch):
    cfg_data = {
        "configs": [
            {
                "enabled": True,
                "module_name": "HealthyMod",
                "module_number": 1,
                "description": "healthy",
                "inputs": [],
            },
            {
                "enabled": True,
                "module_name": "BrokenMod",
                "module_number": 2,
                "description": "broken",
                "inputs": [],
            },
            {
                "enabled": True,
                "module_name": "FrozenMod",
                "module_number": 3,
                "description": "frozen",
                "inputs": ["x"],
            },
        ]
    }

    monkeypatch.setattr(autoLoader, "_load_autoload_configs", lambda session: cfg_data)
    saved = {"called": False}
    monkeypatch.setattr(autoLoader, "_save_autoload_configs", lambda session, data: saved.__setitem__("called", True))

    monkeypatch.setattr(menu, "get_registered_modules", lambda: [
        {"name": "HealthyMod", "number": 1, "background": True},
        {"name": "BrokenMod", "number": 2, "background": True},
        {"name": "FrozenMod", "number": 3, "background": True},
    ])

    plist = [
        {"action": "HealthyMod", "pid": 10, "status": "running", "last_heartbeat": 1000},
        {"action": "BrokenMod", "pid": 11, "status": "[BROKEN] fail", "last_heartbeat": 1000},
        {"action": "FrozenMod", "pid": 12, "status": "running", "last_heartbeat": 1},
    ]
    monkeypatch.setattr(process, "update_process_list", lambda session: plist)
    monkeypatch.setattr(process, "is_process_frozen", lambda p: p["action"] == "FrozenMod")

    launched = []
    monkeypatch.setattr(menu, "dispatch_module_auto", lambda session, mod, inputs: launched.append((mod["number"], inputs)) or True)
    monkeypatch.setattr(autoLoader.time, "time", lambda: 1234.0)

    autoLoader.launch_saved_configs(session=object())

    # Healthy and broken are considered already-running/healthy for autoload skip.
    assert launched == [(3, ["x"])]
    assert saved["called"] is True
    frozen_cfg = next(c for c in cfg_data["configs"] if c["module_name"] == "FrozenMod")
    assert frozen_cfg["last_launched"] == 1234.0
    assert frozen_cfg["launch_count"] == 1


def test_launch_saved_configs_no_save_when_nothing_launched(monkeypatch):
    cfg_data = {
        "configs": [
            {
                "enabled": True,
                "module_name": "OnlyMod",
                "module_number": 1,
                "description": "already up",
                "inputs": [],
            }
        ]
    }

    monkeypatch.setattr(autoLoader, "_load_autoload_configs", lambda session: cfg_data)
    saved = {"called": False}
    monkeypatch.setattr(autoLoader, "_save_autoload_configs", lambda session, data: saved.__setitem__("called", True))
    monkeypatch.setattr(menu, "get_registered_modules", lambda: [{"name": "OnlyMod", "number": 1, "background": True}])
    monkeypatch.setattr(process, "update_process_list", lambda session: [{"action": "OnlyMod", "pid": 1, "status": "running", "last_heartbeat": 1000}])
    monkeypatch.setattr(process, "is_process_frozen", lambda p: False)
    monkeypatch.setattr(menu, "dispatch_module_auto", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("should not launch")))

    autoLoader.launch_saved_configs(session=object())

    assert saved["called"] is False


def test_dispatch_background_handles_child_escape(monkeypatch, capsys):
    class FakeSession:
        def to_dict(self):
            return {"username": "u", "mundo": "1", "servidor": "en"}

    class FakeEvent:
        def wait(self, timeout=0):
            return False

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            self.first = True

        def get_nowait(self):
            if self.first:
                self.first = False
                return "escaped"
            raise Exception("empty")

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 888
            self.exitcode = None

        def start(self):
            return None

        def is_alive(self):
            return True

        def terminate(self):
            raise AssertionError("should not terminate on escaped startup")

    monkeypatch.setattr(menu, "update_process_list", lambda session, new_processes=None: [])
    monkeypatch.setattr(menu.multiprocessing, "Event", FakeEvent)
    monkeypatch.setattr(menu.multiprocessing, "Queue", FakeQueue)
    monkeypatch.setattr(menu.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: 0)
    reports = []
    monkeypatch.setattr(menu, "report_critical_error", lambda *args: reports.append(args))
    menu._RUNTIME_CHILD_PIDS.clear()

    menu._dispatch_background(
        FakeSession(),
        {"name": "BgMod", "func": lambda *_: None, "background": True},
    )

    out = capsys.readouterr().out
    assert "Returning to main menu" in out
    assert 888 in menu._RUNTIME_CHILD_PIDS


def test_dispatch_background_timeout_terminates_child(monkeypatch, capsys):
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
            self.pid = 889
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
        clock["t"] += 121.0
        return current

    monkeypatch.setattr(menu.time, "time", fake_time)
    reports = []
    monkeypatch.setattr(menu, "report_critical_error", lambda *args: reports.append(args))
    menu._RUNTIME_CHILD_PIDS.clear()

    menu._dispatch_background(
        FakeSession(),
        {"name": "BgSlow", "func": lambda *_: None, "background": True},
    )

    out = capsys.readouterr().out
    assert "BG_START_TIMEOUT" in out
    assert terminated["value"] is True
    assert reports and reports[-1][2].startswith("BG_START_TIMEOUT:")


def test_dispatch_background_reports_start_fail_when_child_dies(monkeypatch, capsys):
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

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 990
            self.exitcode = 17

        def start(self):
            return None

        def is_alive(self):
            return False

        def terminate(self):
            raise AssertionError("should not terminate dead child")

    monkeypatch.setattr(menu, "update_process_list", lambda session, new_processes=None: [])
    monkeypatch.setattr(menu.multiprocessing, "Event", FakeEvent)
    monkeypatch.setattr(menu.multiprocessing, "Queue", FakeQueue)
    monkeypatch.setattr(menu.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: 0)
    reports = []
    monkeypatch.setattr(menu, "report_critical_error", lambda *args: reports.append(args))

    menu._dispatch_background(
        FakeSession(),
        {"name": "BgDead", "func": lambda *_: None, "background": True},
    )

    out = capsys.readouterr().out
    assert "BG_START_FAIL" in out
    assert reports and reports[-1][2].startswith("BG_START_FAIL:")


def test_read_password_non_tty_escape(monkeypatch):
    monkeypatch.setattr(prompts, "has_tty", lambda: False)
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "\\")
    with pytest.raises(ReturnToMainMenu):
        prompts.read_password("Password: ")


def test_terminate_background_tasks_includes_runtime_only_pids(monkeypatch):
    monkeypatch.setattr(process, "update_process_list", lambda session: [])
    sent = []
    monkeypatch.setattr(process.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def is_running(self):
            return False

        def status(self):
            return "zombie"

    monkeypatch.setattr(process.psutil, "Process", FakeProc)

    summary = process.terminate_background_tasks(session=object(), runtime_pids={12345}, processing_grace_seconds=1)

    assert summary["total"] == 1
    assert summary["processing"] == 0
    assert any(pid == 12345 for pid, _ in sent)


def test_report_and_read_critical_errors_roundtrip(tmp_path, monkeypatch):
    fake_session = type("S", (), {"servidor": "en", "username": "user"})()
    err_file = tmp_path / "errors.json"

    monkeypatch.setattr(process, "_get_error_file_path", lambda _s: str(err_file))
    monkeypatch.setattr(process.os, "getpid", lambda: 4242)

    process.report_critical_error(fake_session, "modA", "E1")
    process.report_critical_error(fake_session, "modB", "E2")

    errors = process.read_critical_errors(fake_session)
    assert [e["module"] for e in errors] == ["modA", "modB"]
    assert [e["message"] for e in errors] == ["E1", "E2"]
    assert not err_file.exists(), "read_critical_errors should clear the file after reading"


def test_read_critical_errors_handles_malformed_json(tmp_path, monkeypatch):
    fake_session = type("S", (), {"servidor": "en", "username": "user"})()
    err_file = tmp_path / "errors_bad.json"
    err_file.write_text("{not-json")

    monkeypatch.setattr(process, "_get_error_file_path", lambda _s: str(err_file))

    errors = process.read_critical_errors(fake_session)
    assert errors == []


def test_read_critical_errors_lock_timeout_returns_empty(tmp_path, monkeypatch):
    fake_session = type("S", (), {"servidor": "en", "username": "user"})()
    err_file = tmp_path / "errors_timeout.json"
    err_file.write_text(json.dumps([{"module": "x"}]))

    monkeypatch.setattr(process, "_get_error_file_path", lambda _s: str(err_file))

    @contextmanager
    def timeout_lock(_path, timeout=5.0, poll=0.05):
        raise TimeoutError("lock busy")
        yield

    monkeypatch.setattr(process, "_file_lock", timeout_lock)

    errors = process.read_critical_errors(fake_session)
    assert errors == []


def test_update_process_list_filters_dead_and_deduplicates(tmp_path, monkeypatch):
    fake_session = type("S", (), {"servidor": "en", "username": "user"})()
    proc_file = tmp_path / "processes.json"
    proc_file.write_text(json.dumps([
        {"pid": 1, "action": "alive-old", "status": "running"},
        {"pid": 2, "action": "dead", "status": "running"},
        {"pid": 3, "action": "wrong-name", "status": "running"},
    ]))

    monkeypatch.setattr(process, "_get_process_file_path", lambda _s: str(proc_file))
    monkeypatch.setattr(process, "_get_our_process_name", lambda: "python")

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def status(self):
            return {1: "running", 2: "zombie", 3: "running", 4: "running"}[self.pid]

        def name(self):
            return {1: "python", 2: "python", 3: "other", 4: "python"}[self.pid]

    monkeypatch.setattr(process.psutil, "Process", FakeProc)

    updated = process.update_process_list(
        fake_session,
        new_processes=[
            {"pid": 1, "action": "alive-new", "status": "new"},
            {"pid": 4, "action": "fresh", "status": "new"},
        ],
    )

    # Keeps only alive + matching process-name entries and deduplicates by PID.
    by_pid = {e["pid"]: e for e in updated}
    assert set(by_pid.keys()) == {1, 4}
    # Existing PID=1 entry remains (new duplicate is ignored by existing_pids gate).
    assert by_pid[1]["action"] == "alive-old"
    assert by_pid[4]["action"] == "fresh"


def test_update_process_status_updates_current_pid_and_heartbeat(tmp_path, monkeypatch):
    fake_session = type("S", (), {"servidor": "en", "username": "user"})()
    proc_file = tmp_path / "processes_status.json"
    proc_file.write_text(json.dumps([
        {"pid": 10, "action": "x", "status": "old", "last_heartbeat": 1.0},
        {"pid": 11, "action": "y", "status": "other", "last_heartbeat": 2.0},
    ]))

    monkeypatch.setattr(process, "_get_process_file_path", lambda _s: str(proc_file))
    monkeypatch.setattr(process.os, "getpid", lambda: 10)
    monkeypatch.setattr(process.time, "time", lambda: 123.456)

    process.update_process_status(fake_session, "[WAITING] idle")

    data = json.loads(proc_file.read_text())
    by_pid = {e["pid"]: e for e in data}
    assert by_pid[10]["status"] == "[WAITING] idle"
    assert by_pid[10]["last_heartbeat"] == 123.456
    # Ensure unrelated entries are untouched.
    assert by_pid[11]["status"] == "other"


def test_update_process_status_noop_when_pid_missing(tmp_path, monkeypatch):
    fake_session = type("S", (), {"servidor": "en", "username": "user"})()
    proc_file = tmp_path / "processes_status_missing.json"
    original = [
        {"pid": 11, "action": "y", "status": "other", "last_heartbeat": 2.0},
    ]
    proc_file.write_text(json.dumps(original))

    monkeypatch.setattr(process, "_get_process_file_path", lambda _s: str(proc_file))
    monkeypatch.setattr(process.os, "getpid", lambda: 99)

    process.update_process_status(fake_session, "[WAITING] idle")

    data = json.loads(proc_file.read_text())
    assert data == original


def test_update_process_list_lock_timeout_returns_empty(tmp_path, monkeypatch):
    fake_session = type("S", (), {"servidor": "en", "username": "user"})()
    proc_file = tmp_path / "processes_timeout.json"
    proc_file.write_text("[]")

    monkeypatch.setattr(process, "_get_process_file_path", lambda _s: str(proc_file))

    @contextmanager
    def timeout_lock(_path, timeout=5.0, poll=0.05):
        raise TimeoutError("lock busy")
        yield

    monkeypatch.setattr(process, "_file_lock", timeout_lock)

    out = process.update_process_list(fake_session)
    assert out == []


def test_update_process_status_lock_timeout_leaves_file_unchanged(tmp_path, monkeypatch):
    fake_session = type("S", (), {"servidor": "en", "username": "user"})()
    proc_file = tmp_path / "processes_status_timeout.json"
    original = [{"pid": 10, "action": "x", "status": "old", "last_heartbeat": 1.0}]
    proc_file.write_text(json.dumps(original))

    monkeypatch.setattr(process, "_get_process_file_path", lambda _s: str(proc_file))

    @contextmanager
    def timeout_lock(_path, timeout=5.0, poll=0.05):
        raise TimeoutError("lock busy")
        yield

    monkeypatch.setattr(process, "_file_lock", timeout_lock)

    process.update_process_status(fake_session, "[WAITING] idle")

    data = json.loads(proc_file.read_text())
    assert data == original


def test_get_process_health_explicit_state_beats_frozen(monkeypatch):
    # Even with stale heartbeat, explicit state prefix should win over FROZEN fallback.
    monkeypatch.setattr(process.time, "time", lambda: 10_000.0)
    stale = 10_000.0 - process.HEARTBEAT_STALE_THRESHOLD - 50
    assert process.get_process_health({"status": "[WAITING] hold", "last_heartbeat": stale}) == "WAITING"
    assert process.get_process_health({"status": "[PAUSED] hold", "last_heartbeat": stale}) == "PAUSED"
    assert process.get_process_health({"status": "[PROCESSING] hold", "last_heartbeat": stale}) == "PROCESSING"
    assert process.get_process_health({"status": "[BROKEN] hold", "last_heartbeat": stale}) == "BROKEN"


def test_sleep_with_heartbeat_short_sleep_no_refresh(monkeypatch):
    calls = []

    class FakeSession:
        _status = "[WAITING] short"

        def setStatus(self, status):
            calls.append(status)

    monkeypatch.setattr(process.time, "sleep", lambda _: None)
    process.sleep_with_heartbeat(FakeSession(), seconds=2, interval=300)

    # No intermediate heartbeat refresh for single-chunk sleeps.
    assert calls == []


def test_read_critical_errors_non_list_payload_returns_empty(tmp_path, monkeypatch):
    fake_session = type("S", (), {"servidor": "en", "username": "user"})()
    err_file = tmp_path / "errors_obj.json"
    err_file.write_text(json.dumps({"oops": 1}))

    monkeypatch.setattr(process, "_get_error_file_path", lambda _s: str(err_file))

    errors = process.read_critical_errors(fake_session)
    assert errors == []


def test_session_export_cookies_only_ikariam():
    class FakeHTTP:
        def __init__(self):
            self.cookies = RequestsCookieJar()
            self.cookies.set("ikariam", "IK-VAL", domain="example.invalid", path="/")
            self.cookies.set("PHPSESSID", "PHS", domain="example.invalid", path="/")

    fake = type("S", (), {})()
    fake.s = FakeHTTP()
    fake.host = "example.invalid"
    fake._get_ikariam_cookie = lambda: Session._get_ikariam_cookie(fake)

    exported = Session.export_cookies(fake)
    assert json.loads(exported) == {"ikariam": "IK-VAL"}


def test_session_export_cookies_js_contains_ikariam_value():
    class FakeHTTP:
        def __init__(self):
            self.cookies = RequestsCookieJar()
            self.cookies.set("ikariam", "IK-JS", domain="example.invalid", path="/")

    fake = type("S", (), {})()
    fake.s = FakeHTTP()
    fake.host = "example.invalid"
    fake._get_ikariam_cookie = lambda: Session._get_ikariam_cookie(fake)

    script = Session.export_cookies_js(fake)
    assert "IK-JS" in script
    assert "PHPSESSID" not in script


def test_dispatch_background_falls_back_to_sync_when_stdin_unavailable(monkeypatch):
    called = {"sync": 0}

    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: (_ for _ in ()).throw(ValueError("no fd")))
    monkeypatch.setattr(menu, "_dispatch_sync", lambda session, mod: called.__setitem__("sync", called["sync"] + 1))

    menu._dispatch_background(
        session=object(),
        mod={"name": "SyncFallback", "func": lambda *_: None, "background": True},
    )

    assert called["sync"] == 1


def test_dispatch_module_auto_returns_false_when_stdin_unavailable(monkeypatch):
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: (_ for _ in ()).throw(ValueError("no fd")))
    # Ensure predetermined input setup/clear won't leak state on failure path.
    set_calls = []

    import autoIkabot.ui.prompts as prompts_mod
    monkeypatch.setattr(prompts_mod, "set_predetermined_input", lambda vals: set_calls.append(list(vals)))

    ok = menu.dispatch_module_auto(
        session=object(),
        mod={"name": "AutoFail", "func": lambda *_: None, "background": True},
        predetermined_inputs=[1, 2],
    )

    assert ok is False
    assert set_calls == [[1, 2], []]


def test_dispatch_module_auto_returns_false_when_child_exits_during_config(monkeypatch):
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

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 333
            self.exitcode = 9

        def start(self):
            return None

        def is_alive(self):
            return False

        def terminate(self):
            raise AssertionError("should not terminate already-exited child")

    monkeypatch.setattr(menu, "update_process_list", lambda session, new_processes=None: [])
    monkeypatch.setattr(menu.multiprocessing, "Event", FakeEvent)
    monkeypatch.setattr(menu.multiprocessing, "Queue", FakeQueue)
    monkeypatch.setattr(menu.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: 0)
    menu._RUNTIME_CHILD_PIDS.clear()

    ok = menu.dispatch_module_auto(
        FakeSession(),
        {"name": "DeadChild", "func": lambda *_: None, "background": True},
        [],
    )

    assert ok is False
    assert 333 in menu._RUNTIME_CHILD_PIDS


def test_file_lock_creates_and_removes_lockfile(tmp_path):
    target = tmp_path / "state.json"
    lock_path = str(target) + ".lock"

    assert not os.path.exists(lock_path)
    with process._file_lock(str(target), timeout=0.2, poll=0.01):
        assert os.path.exists(lock_path)
    assert not os.path.exists(lock_path)


def test_file_lock_timeout_when_lock_exists(tmp_path, monkeypatch):
    target = tmp_path / "state_timeout.json"
    lock_path = str(target) + ".lock"
    with open(lock_path, "w") as f:
        f.write("999")

    tick = {"t": 0.0}

    def fake_time():
        tick["t"] += 0.2
        return tick["t"]

    monkeypatch.setattr(process.time, "time", fake_time)
    monkeypatch.setattr(process.time, "sleep", lambda *_: None)

    with pytest.raises(TimeoutError):
        with process._file_lock(str(target), timeout=0.5, poll=0.01):
            pass

    # Existing lock should remain if we never acquired it.
    assert os.path.exists(lock_path)


def test_shutdown_children_terminates_runtime_and_logs_out(monkeypatch, capsys):
    import main as main_mod

    class FakeSession:
        def __init__(self):
            self.logged_out = False

        def logout(self):
            self.logged_out = True

    class FakeLogger:
        def exception(self, *args, **kwargs):
            raise AssertionError("unexpected logger.exception")

    monkeypatch.setattr("autoIkabot.modules.autoLoader.record_shutdown_restore_states", lambda session: None)
    monkeypatch.setattr("autoIkabot.ui.menu.get_runtime_child_pids", lambda: [11, 22])

    seen = {}

    def fake_terminate(session, runtime_pids, processing_grace_seconds):
        seen["session"] = session
        seen["runtime_pids"] = runtime_pids
        seen["grace"] = processing_grace_seconds
        return {"total": 2, "processing": 1, "force_killed": 0}

    monkeypatch.setattr("autoIkabot.utils.process.terminate_background_tasks", fake_terminate)

    session = FakeSession()
    main_mod._shutdown_children(session, FakeLogger(), print_summary=True, logout=True)

    out = capsys.readouterr().out
    assert "Shutdown: stopped 2 task(s)" in out
    assert seen["session"] is session
    assert seen["runtime_pids"] == [11, 22]
    assert seen["grace"] == 120
    assert session.logged_out is True


def test_shutdown_children_handles_terminate_failure_and_still_logs_out(monkeypatch):
    import main as main_mod

    class FakeSession:
        def __init__(self):
            self.logged_out = False

        def logout(self):
            self.logged_out = True

    class FakeLogger:
        def __init__(self):
            self.messages = []

        def exception(self, msg):
            self.messages.append(msg)

    monkeypatch.setattr("autoIkabot.modules.autoLoader.record_shutdown_restore_states", lambda session: None)
    monkeypatch.setattr("autoIkabot.ui.menu.get_runtime_child_pids", lambda: [99])
    monkeypatch.setattr(
        "autoIkabot.utils.process.terminate_background_tasks",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    logger = FakeLogger()
    session = FakeSession()
    main_mod._shutdown_children(session, logger, print_summary=False, logout=True)

    assert session.logged_out is True
    assert any("Background shutdown cleanup failed" in m for m in logger.messages)



def test_record_shutdown_restore_states_marks_only_running_or_paused(monkeypatch):
    cfg_data = {
        "configs": [
            {"module_name": "RunMod", "enabled": True},
            {"module_name": "PauseMod", "enabled": True},
            {"module_name": "BrokenMod", "enabled": True},
        ]
    }

    monkeypatch.setattr(autoLoader, "_load_autoload_configs", lambda session: cfg_data)
    saved = {"called": False}

    def fake_save(_session, _data):
        saved["called"] = True

    monkeypatch.setattr(autoLoader, "_save_autoload_configs", fake_save)
    monkeypatch.setattr(
        process,
        "update_process_list",
        lambda _session: [
            {"action": "RunMod", "status": "[WAITING] ships", "last_heartbeat": 1000},
            {"action": "PauseMod", "status": "[PAUSED] timer", "last_heartbeat": 1000},
            {"action": "BrokenMod", "status": "[BROKEN] fail", "last_heartbeat": 1000},
        ],
    )

    autoLoader.record_shutdown_restore_states(session=object())

    run_cfg = next(c for c in cfg_data["configs"] if c["module_name"] == "RunMod")
    pause_cfg = next(c for c in cfg_data["configs"] if c["module_name"] == "PauseMod")
    broken_cfg = next(c for c in cfg_data["configs"] if c["module_name"] == "BrokenMod")

    assert run_cfg["last_shutdown_restore"] is True
    assert pause_cfg["last_shutdown_restore"] is True
    assert broken_cfg["last_shutdown_restore"] is False
    assert broken_cfg["last_shutdown_health"] == "BROKEN"
    assert saved["called"] is True


def test_launch_saved_configs_respects_last_shutdown_restore_flag(monkeypatch):
    cfg_data = {
        "configs": [
            {
                "enabled": True,
                "module_name": "RestoreMe",
                "module_number": 1,
                "description": "ok",
                "inputs": ["a"],
                "last_shutdown_restore": True,
                "last_shutdown_health": "RUNNING",
            },
            {
                "enabled": True,
                "module_name": "SkipMe",
                "module_number": 2,
                "description": "broken",
                "inputs": ["b"],
                "last_shutdown_restore": False,
                "last_shutdown_health": "BROKEN",
            },
        ]
    }

    monkeypatch.setattr(autoLoader, "_load_autoload_configs", lambda session: cfg_data)
    monkeypatch.setattr(autoLoader, "_save_autoload_configs", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        menu,
        "get_registered_modules",
        lambda: [
            {"name": "RestoreMe", "number": 1, "background": True},
            {"name": "SkipMe", "number": 2, "background": True},
        ],
    )
    monkeypatch.setattr(process, "update_process_list", lambda session: [])
    monkeypatch.setattr(process, "is_process_frozen", lambda p: False)

    launched = []
    monkeypatch.setattr(menu, "dispatch_module_auto", lambda _session, mod, inputs: launched.append((mod["name"], inputs)) or True)

    autoLoader.launch_saved_configs(session=object())

    assert launched == [("RestoreMe", ["a"])]



def test_task_status_format_heartbeat_age_handles_legacy_and_seconds(monkeypatch):
    now = 1_000.0
    assert task_status_mod._format_heartbeat_age(now, {}) == "legacy"
    assert task_status_mod._format_heartbeat_age(now, {"last_heartbeat": 995.0}) == "5s"


def test_task_status_extract_last_error_from_broken_status():
    assert task_status_mod._extract_last_error("[BROKEN] GET_RETRY_EXHAUSTED: timeout") == "GET_RETRY_EXHAUSTED: timeout"
    assert task_status_mod._extract_last_error("[BROKEN]") == "-"
    assert task_status_mod._extract_last_error("") == "-"



def test_record_shutdown_restore_states_marks_missing_as_stopped(monkeypatch):
    cfg_data = {
        "configs": [
            {
                "module_name": "GoneMod",
                "enabled": True,
                "last_shutdown_restore": True,
                "last_shutdown_health": "RUNNING",
            }
        ]
    }

    monkeypatch.setattr(autoLoader, "_load_autoload_configs", lambda session: cfg_data)
    saved = {"called": False}
    monkeypatch.setattr(autoLoader, "_save_autoload_configs", lambda *_args, **_kwargs: saved.__setitem__("called", True))
    monkeypatch.setattr(process, "update_process_list", lambda _session: [])

    autoLoader.record_shutdown_restore_states(session=object())

    cfg = cfg_data["configs"][0]
    assert cfg["last_shutdown_restore"] is False
    assert cfg["last_shutdown_health"] == "STOPPED"
    assert saved["called"] is True



def test_format_critical_error_line_preserves_code_and_detail():
    err = {"module": "Session", "pid": 99, "message": "GET_RETRY_EXHAUSTED: timeout"}
    line = menu._format_critical_error_line(err)
    assert line == "Session (PID 99) - GET_RETRY_EXHAUSTED: timeout"


def test_format_critical_error_line_falls_back_to_unknown_code():
    err = {"module": "ModA", "pid": 12, "message": "plain message"}
    line = menu._format_critical_error_line(err)
    assert line == "ModA (PID 12) - BG_UNKNOWN: plain message"



def test_dispatch_module_auto_timeout_reports_critical(monkeypatch, capsys):
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
            self.pid = 556
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
        clock["t"] += 121.0
        return current

    monkeypatch.setattr(menu.time, "time", fake_time)
    reports = []
    monkeypatch.setattr(menu, "report_critical_error", lambda *args: reports.append(args))

    result = menu.dispatch_module_auto(
        FakeSession(),
        {"name": "AutoSlow", "func": lambda *_: None, "background": True},
        [],
    )

    out = capsys.readouterr().out
    assert result is False
    assert "BG_AUTOLOAD_TIMEOUT" in out
    assert terminated["value"] is True
    assert reports and reports[-1][2].startswith("BG_AUTOLOAD_TIMEOUT:")



def test_terminate_background_tasks_ignores_current_and_invalid_pids(monkeypatch):
    monkeypatch.setattr(process, "update_process_list", lambda session: [{"pid": os.getpid(), "status": "[PROCESSING] self"}])

    sent = []
    monkeypatch.setattr(process.os, "kill", lambda pid, sig: sent.append((pid, sig)))

    class FakeProc:
        def __init__(self, pid):
            self.pid = pid

        def is_running(self):
            return False

        def status(self):
            return "zombie"

    monkeypatch.setattr(process.psutil, "Process", FakeProc)

    summary = process.terminate_background_tasks(
        session=object(),
        runtime_pids={0, -1, os.getpid()},
        processing_grace_seconds=1,
    )

    assert summary == {"total": 0, "processing": 0, "force_killed": 0}
    assert sent == []



def test_dispatch_background_reports_start_crash_state(monkeypatch, capsys):
    class FakeSession:
        def to_dict(self):
            return {"username": "u", "mundo": "1", "servidor": "en"}

    class FakeEvent:
        def wait(self, timeout=0):
            return False

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            self.first = True

        def get_nowait(self):
            if self.first:
                self.first = False
                return "crashed"
            raise Exception("empty")

    terminated = {"value": False}

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 777
            self.exitcode = 1

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
    reports = []
    monkeypatch.setattr(menu, "report_critical_error", lambda *args: reports.append(args))

    menu._dispatch_background(
        FakeSession(),
        {"name": "BgCrash", "func": lambda *_: None, "background": True},
    )

    out = capsys.readouterr().out
    assert "BG_START_CRASH" in out
    assert terminated["value"] is True
    assert reports and reports[-1][2].startswith("BG_START_CRASH:")


def test_dispatch_module_auto_crash_reports_critical(monkeypatch):
    class FakeSession:
        def to_dict(self):
            return {"username": "u", "mundo": "1", "servidor": "en"}

    class FakeEvent:
        def wait(self, timeout=0):
            return False

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            self.first = True

        def get_nowait(self):
            if self.first:
                self.first = False
                return "crashed"
            raise Exception("empty")

    terminated = {"value": False}

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 778
            self.exitcode = 1

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
    reports = []
    monkeypatch.setattr(menu, "report_critical_error", lambda *args: reports.append(args))

    result = menu.dispatch_module_auto(
        FakeSession(),
        {"name": "AutoCrash", "func": lambda *_: None, "background": True},
        [],
    )

    assert result is False
    assert terminated["value"] is True
    assert reports and reports[-1][2].startswith("BG_AUTOLOAD_CRASH:")



def test_update_process_status_for_pid_updates_target_and_heartbeat(tmp_path, monkeypatch):
    target = tmp_path / "plist.json"
    target.write_text(json.dumps([
        {"pid": 10, "status": "old", "last_heartbeat": 1.0},
        {"pid": 11, "status": "keep", "last_heartbeat": 2.0},
    ]))

    monkeypatch.setattr(process, "_get_process_file_path", lambda session: str(target))

    @contextmanager
    def fake_lock(*args, **kwargs):
        yield

    monkeypatch.setattr(process, "_file_lock", fake_lock)
    monkeypatch.setattr(process.time, "time", lambda: 123.0)

    process.update_process_status_for_pid(session=object(), pid=10, status="[WAITING] started")

    data = json.loads(target.read_text())
    updated = next(e for e in data if e["pid"] == 10)
    untouched = next(e for e in data if e["pid"] == 11)
    assert updated["status"] == "[WAITING] started"
    assert updated["last_heartbeat"] == 123.0
    assert untouched["status"] == "keep"


def test_update_process_status_for_pid_noop_when_pid_missing(tmp_path, monkeypatch):
    target = tmp_path / "plist.json"
    original = [{"pid": 20, "status": "old", "last_heartbeat": 1.0}]
    target.write_text(json.dumps(original))

    monkeypatch.setattr(process, "_get_process_file_path", lambda session: str(target))

    @contextmanager
    def fake_lock(*args, **kwargs):
        yield

    monkeypatch.setattr(process, "_file_lock", fake_lock)

    process.update_process_status_for_pid(session=object(), pid=99, status="[BROKEN] nope")

    assert json.loads(target.read_text()) == original



def test_dispatch_background_spawn_failure_reports_critical(monkeypatch, capsys):
    class FakeSession:
        def to_dict(self):
            return {"username": "u", "mundo": "1", "servidor": "en"}

    class FakeEvent:
        def wait(self, timeout=0):
            return False

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            pass

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = None

        def start(self):
            raise OSError("no fork")

    monkeypatch.setattr(menu.multiprocessing, "Event", FakeEvent)
    monkeypatch.setattr(menu.multiprocessing, "Queue", FakeQueue)
    monkeypatch.setattr(menu.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: 0)
    reports = []
    monkeypatch.setattr(menu, "report_critical_error", lambda *args: reports.append(args))

    menu._dispatch_background(
        FakeSession(),
        {"name": "BgSpawn", "func": lambda *_: None, "background": True},
    )

    out = capsys.readouterr().out
    assert "BG_START_SPAWN_FAIL" in out
    assert reports and reports[-1][2].startswith("BG_START_SPAWN_FAIL:")


def test_dispatch_module_auto_spawn_failure_reports_critical(monkeypatch, capsys):
    class FakeSession:
        def to_dict(self):
            return {"username": "u", "mundo": "1", "servidor": "en"}

    class FakeEvent:
        def wait(self, timeout=0):
            return False

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            pass

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = None

        def start(self):
            raise OSError("no fork")

    monkeypatch.setattr(menu.multiprocessing, "Event", FakeEvent)
    monkeypatch.setattr(menu.multiprocessing, "Queue", FakeQueue)
    monkeypatch.setattr(menu.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: 0)
    reports = []
    monkeypatch.setattr(menu, "report_critical_error", lambda *args: reports.append(args))

    result = menu.dispatch_module_auto(
        FakeSession(),
        {"name": "AutoSpawn", "func": lambda *_: None, "background": True},
        [],
    )

    out = capsys.readouterr().out
    assert result is False
    assert "BG_AUTOLOAD_SPAWN_FAIL" in out
    assert reports and reports[-1][2].startswith("BG_AUTOLOAD_SPAWN_FAIL:")



def test_dispatch_background_initial_status_uses_processing_prefix(monkeypatch):
    class FakeSession:
        def to_dict(self):
            return {"username": "u", "mundo": "1", "servidor": "en"}

    class FakeEvent:
        def wait(self, timeout=0):
            return True

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            pass

        def get_nowait(self):
            raise Exception("empty")

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 991
            self.exitcode = 0

        def start(self):
            return None

        def is_alive(self):
            return True

        def terminate(self):
            return None

    captured = {}

    def fake_update(_session, new_processes=None):
        captured["entry"] = new_processes[0]
        return []

    monkeypatch.setattr(menu, "update_process_list", fake_update)
    monkeypatch.setattr(menu, "update_process_status_for_pid", lambda *args, **kwargs: None)
    monkeypatch.setattr(menu.multiprocessing, "Event", FakeEvent)
    monkeypatch.setattr(menu.multiprocessing, "Queue", FakeQueue)
    monkeypatch.setattr(menu.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: 0)

    menu._dispatch_background(
        FakeSession(),
        {"name": "BgCfg", "func": lambda *_: None, "background": True},
    )

    assert captured["entry"]["status"].startswith("[PROCESSING]")


def test_dispatch_module_auto_initial_status_uses_processing_prefix(monkeypatch):
    class FakeSession:
        def to_dict(self):
            return {"username": "u", "mundo": "1", "servidor": "en"}

    class FakeEvent:
        def wait(self, timeout=0):
            return True

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            pass

        def get_nowait(self):
            raise Exception("empty")

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 992
            self.exitcode = 0

        def start(self):
            return None

        def is_alive(self):
            return True

        def terminate(self):
            return None

    captured = {}

    def fake_update(_session, new_processes=None):
        captured["entry"] = new_processes[0]
        return []

    monkeypatch.setattr(menu, "update_process_list", fake_update)
    monkeypatch.setattr(menu, "update_process_status_for_pid", lambda *args, **kwargs: None)
    monkeypatch.setattr(menu.multiprocessing, "Event", FakeEvent)
    monkeypatch.setattr(menu.multiprocessing, "Queue", FakeQueue)
    monkeypatch.setattr(menu.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: 0)

    ok = menu.dispatch_module_auto(
        FakeSession(),
        {"name": "AutoCfg", "func": lambda *_: None, "background": True},
        [],
    )

    assert ok is True
    assert captured["entry"]["status"].startswith("[PROCESSING]")



def test_dispatch_module_auto_escape_prints_compact_notice(monkeypatch, capsys):
    class FakeSession:
        def to_dict(self):
            return {"username": "u", "mundo": "1", "servidor": "en"}

    class FakeEvent:
        def wait(self, timeout=0):
            return False

    class FakeQueue:
        def __init__(self, *args, **kwargs):
            self.first = True

        def get_nowait(self):
            if self.first:
                self.first = False
                return "escaped"
            raise Exception("empty")

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.pid = 993
            self.exitcode = 0

        def start(self):
            return None

        def is_alive(self):
            return True

        def terminate(self):
            raise AssertionError("should not terminate escaped startup")

    monkeypatch.setattr(menu, "update_process_list", lambda session, new_processes=None: [])
    monkeypatch.setattr(menu, "update_process_status_for_pid", lambda *args, **kwargs: None)
    monkeypatch.setattr(menu.multiprocessing, "Event", FakeEvent)
    monkeypatch.setattr(menu.multiprocessing, "Queue", FakeQueue)
    monkeypatch.setattr(menu.multiprocessing, "Process", FakeProcess)
    monkeypatch.setattr(menu.sys.stdin, "fileno", lambda: 0)

    ok = menu.dispatch_module_auto(
        FakeSession(),
        {"name": "AutoEsc", "func": lambda *_: None, "background": True},
        [],
    )

    out = capsys.readouterr().out
    assert ok is False
    assert "BG_AUTOLOAD_ESCAPED" in out



def test_rtm_describe_lock_holder_reads_metadata(tmp_path, monkeypatch):
    fake = type("S", (), {"servidor": "en", "username": "u"})()
    lock_file = tmp_path / "lock.json"
    lock_file.write_text(json.dumps({"pid": 42, "timestamp": 900.0, "username": "holder"}))

    monkeypatch.setattr(rtm_mod, "get_lock_file_path", lambda session, use_freighters=False: str(lock_file))
    monkeypatch.setattr(rtm_mod.time, "time", lambda: 1_000.0)

    info = rtm_mod._describe_lock_holder(fake, use_freighters=False)
    assert "pid=42" in info
    assert "user=holder" in info
    assert "age=100s" in info


def test_rtm_acquire_shipping_lock_wait_context_updates_waiting_status(monkeypatch, tmp_path):
    fake = type("S", (), {"servidor": "en", "username": "u", "statuses": []})()
    fake.setStatus = lambda status: fake.statuses.append(status)

    lock_file = tmp_path / "ship.lock"
    lock_file.write_text(json.dumps({"pid": 55, "timestamp": 1_000.0, "username": "holder"}))

    monkeypatch.setattr(rtm_mod, "get_lock_file_path", lambda session, use_freighters=False: str(lock_file))

    clock = {"t": 1_100.0}

    def fake_time():
        val = clock["t"]
        clock["t"] += 1.0
        return val

    monkeypatch.setattr(rtm_mod.time, "time", fake_time)
    monkeypatch.setattr(rtm_mod, "sleep_with_heartbeat", lambda *args, **kwargs: None)

    ok = rtm_mod.acquire_shipping_lock(fake, timeout=5, wait_context="route")

    assert ok is False
    assert fake.statuses
    assert fake.statuses[-1].startswith("[WAITING] route")



def test_resource_transport_manager_escape_sets_event(monkeypatch):
    class FakeEvent:
        def __init__(self):
            self.called = 0

        def set(self):
            self.called += 1

    monkeypatch.setattr(rtm_mod.os, "fdopen", lambda _fd: __import__("io").StringIO(""))
    monkeypatch.setattr(rtm_mod, "checkTelegramData", lambda _session: True)
    monkeypatch.setattr(rtm_mod, "read", lambda *args, **kwargs: (_ for _ in ()).throw(ReturnToMainMenu()))

    fake_event = FakeEvent()
    rtm_mod.resourceTransportManager(session=object(), event=fake_event, stdin_fd=0)

    assert fake_event.called == 1


def test_rtm_config_modes_return_none_on_global_escape(monkeypatch):
    monkeypatch.setattr(rtm_mod, "read", lambda *args, **kwargs: (_ for _ in ()).throw(ReturnToMainMenu()))

    assert rtm_mod.consolidateMode(session=object(), telegram_enabled=False) is None
    assert rtm_mod.distributeMode(session=object(), telegram_enabled=False) is None
    assert rtm_mod.evenDistributionMode(session=object(), telegram_enabled=False) is None
    assert rtm_mod.autoSendMode(session=object(), telegram_enabled=False) is None



def test_construction_manager_escape_sets_event(monkeypatch):
    class FakeEvent:
        def __init__(self):
            self.called = 0

        def set(self):
            self.called += 1

    monkeypatch.setattr(cm_mod.os, "fdopen", lambda _fd: __import__("io").StringIO(""))
    monkeypatch.setattr(cm_mod, "chooseCity", lambda _session: (_ for _ in ()).throw(ReturnToMainMenu()))

    fake_event = FakeEvent()
    cm_mod.constructionManager(session=object(), event=fake_event, stdin_fd=0)

    assert fake_event.called == 1


def test_activate_miracle_escape_sets_event(monkeypatch):
    class FakeEvent:
        def __init__(self):
            self.called = 0

        def set(self):
            self.called += 1

    monkeypatch.setattr(am_mod.os, "fdopen", lambda _fd: __import__("io").StringIO(""))
    monkeypatch.setattr(am_mod, "obtainMiraclesAvailable", lambda _session: [{"wonderName": "X", "available": True}])
    monkeypatch.setattr(am_mod, "chooseIsland", lambda _islands: (_ for _ in ()).throw(ReturnToMainMenu()))

    fake_event = FakeEvent()
    am_mod.activateMiracle(session=object(), event=fake_event, stdin_fd=0)

    assert fake_event.called == 1




def test_wait_for_construction_waiting_status_prefix(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.statuses = []

        def setStatus(self, status):
            self.statuses.append(status)

        def get(self, _url):
            return "html"

    fake = FakeSession()

    monkeypatch.setattr(cm_mod, "getCity", lambda _html: {
        "cityName": "City",
        "position": [{"name": "Town Hall", "level": 1, "completed": "200"}],
    })
    monkeypatch.setattr(cm_mod.time, "time", lambda: 100)
    monkeypatch.setattr(cm_mod, "getDateTime", lambda *_args, **_kwargs: "DATE")
    monkeypatch.setattr(
        cm_mod,
        "sleep_with_heartbeat",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stop")),
    )

    with pytest.raises(RuntimeError, match="stop"):
        cm_mod._wait_for_construction(fake, city_id=1, final_lvl=2)

    assert fake.statuses and fake.statuses[-1].startswith("[WAITING] Waiting until")


def test_expand_building_processing_status_prefix(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.statuses = []

        def setStatus(self, status):
            self.statuses.append(status)

        def post(self, *args, **kwargs):
            return "[]"

    fake = FakeSession()

    monkeypatch.setattr(
        cm_mod,
        "_wait_for_construction",
        lambda *_args, **_kwargs: {
            "cityName": "City",
            "position": [{"level": 1, "canUpgrade": True, "building": "townHall"}],
        },
    )
    monkeypatch.setattr(cm_mod, "report_critical_error", lambda *args, **kwargs: None)

    cm_mod._expand_building(
        fake,
        city_id="1",
        building={"level": 1, "position": 0, "name": "Town Hall", "upgradeTo": 2},
        wait_for_resources=False,
    )

    assert any(st.startswith("[PROCESSING] Upgrading Town Hall") for st in fake.statuses)

def test_construction_execute_transport_passes_lock_wait_context(monkeypatch):
    routes = [({"name": "A"}, {"name": "B"}, "i", 1, 0, 0, 0, 0)]

    class FakeSession:
        def __init__(self):
            self.statuses = []

        def setStatus(self, status):
            self.statuses.append(status)

    fake = FakeSession()

    seen = {}

    def fake_acquire(session, use_freighters=False, timeout=0, wait_context=None):
        seen["wait_context"] = wait_context
        return True

    monkeypatch.setattr(cm_mod, "acquire_shipping_lock", fake_acquire)
    monkeypatch.setattr(cm_mod, "release_shipping_lock", lambda *args, **kwargs: None)
    monkeypatch.setattr(cm_mod, "getAvailableShips", lambda _session: 1)
    monkeypatch.setattr(cm_mod, "executeRoutes", lambda *args, **kwargs: None)

    cm_mod._execute_transport(fake, {"routes": routes, "useFreighters": False})

    assert seen["wait_context"] == "Construction transport"
    assert any(st.startswith("[WAITING]") for st in fake.statuses)
    assert any(st.startswith("[PROCESSING]") for st in fake.statuses)


def test_construction_execute_transport_reports_error_when_lock_unavailable(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.statuses = []

        def setStatus(self, _status):
            self.statuses.append(_status)
            return None

    reports = []
    monkeypatch.setattr(cm_mod, "acquire_shipping_lock", lambda *args, **kwargs: False)
    monkeypatch.setattr(cm_mod, "sleep_with_heartbeat", lambda *args, **kwargs: None)
    monkeypatch.setattr(cm_mod, "report_critical_error", lambda *args: reports.append(args))

    fake = FakeSession()
    cm_mod._execute_transport(fake, {"routes": [], "useFreighters": False})

    assert reports and "Could not acquire shipping lock" in reports[-1][2]
    assert any(st.startswith("[WAITING] Could not acquire shipping lock") for st in fake.statuses)




def test_rtm_auto_send_status_prefixes(monkeypatch):
    routes = [({"id": 1, "name": "Origin"}, {"id": 2, "name": "Dest"}, "island", 1, 0, 0, 0, 0)]

    class FakeSession:
        def __init__(self):
            self.statuses = []
            self.username = "u"

        def setStatus(self, status):
            self.statuses.append(status)

    fake = FakeSession()

    monkeypatch.setattr(rtm_mod, "getAvailableShips", lambda _session: 1)
    monkeypatch.setattr(rtm_mod, "acquire_shipping_lock", lambda *args, **kwargs: True)
    monkeypatch.setattr(rtm_mod, "release_shipping_lock", lambda *args, **kwargs: None)
    monkeypatch.setattr(rtm_mod, "executeRoutes", lambda *args, **kwargs: None)

    rtm_mod.do_it_auto_send(fake, routes, useFreighters=False, telegram_enabled=False)

    assert any(st.startswith("[WAITING] Auto Send [1/1]") for st in fake.statuses)
    assert any(st.startswith("[PROCESSING] Auto Send [1/1]") for st in fake.statuses)
    assert any(st.startswith("[WAITING] Auto Send complete") for st in fake.statuses)


def test_rtm_one_time_shipment_status_prefix(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.statuses = []
            self.username = "u"

        def setStatus(self, status):
            self.statuses.append(status)

        def get(self, url):
            return url

    fake = FakeSession()

    cities = {
        1: {
            "id": 1,
            "name": "Origin",
            "availableResources": [0, 0, 0, 0, 0],
            "islandId": 10,
            "freeSpaceForResources": [100, 100, 100, 100, 100],
            "isOwnCity": True,
        },
        2: {
            "id": 2,
            "name": "Dest",
            "availableResources": [0, 0, 0, 0, 0],
            "islandId": 20,
            "freeSpaceForResources": [100, 100, 100, 100, 100],
            "isOwnCity": True,
        },
    }

    monkeypatch.setattr(rtm_mod, "getCity", lambda html: dict(cities[int(html.split("=")[-1])]))

    rtm_mod.do_it(
        fake,
        origin_cities=[{"id": 1, "name": "Origin"}],
        destination_city={"id": 2, "name": "Dest"},
        island={"id": 99, "x": 1, "y": 2},
        interval_hours=0,
        resource_config=[None, None, None, None, None],
        useFreighters=False,
        send_mode=1,
        telegram_enabled=False,
        notify_on_start=False,
    )

    assert fake.statuses[-1].startswith("[WAITING] One-time shipment completed")


def test_rtm_one_time_distribution_status_prefix(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.statuses = []
            self.username = "u"

        def setStatus(self, status):
            self.statuses.append(status)

        def get(self, url):
            return url

    fake = FakeSession()

    cities = {
        1: {
            "id": 1,
            "name": "Origin",
            "availableResources": [0, 0, 0, 0, 0],
            "islandId": 10,
            "freeSpaceForResources": [100, 100, 100, 100, 100],
            "isOwnCity": True,
        },
        2: {
            "id": 2,
            "name": "Dest",
            "availableResources": [0, 0, 0, 0, 0],
            "islandId": 20,
            "freeSpaceForResources": [100, 100, 100, 100, 100],
            "isOwnCity": True,
        },
    }

    monkeypatch.setattr(rtm_mod, "getCity", lambda html: dict(cities[int(html.split("=")[-1])]))
    monkeypatch.setattr(rtm_mod, "getIsland", lambda _html: {"id": 1, "x": 1, "y": 2})

    rtm_mod.do_it_distribute(
        fake,
        origin_city={"id": 1, "name": "Origin"},
        destination_cities=[{"id": 2, "name": "Dest"}],
        interval_hours=0,
        resource_config=[0, 0, 0, 0, 0],
        useFreighters=False,
        telegram_enabled=False,
        notify_on_start=False,
    )

    assert fake.statuses[-1].startswith("[WAITING] One-time distribution completed")


def test_rtm_auto_send_lock_failure_sets_waiting_status(monkeypatch):
    routes = [({"id": 1, "name": "Origin"}, {"id": 2, "name": "Dest"}, "island", 1, 0, 0, 0, 0)]

    class FakeSession:
        def __init__(self):
            self.statuses = []
            self.username = "u"

        def setStatus(self, status):
            self.statuses.append(status)

    fake = FakeSession()

    monkeypatch.setattr(rtm_mod, "getAvailableShips", lambda _session: 1)
    monkeypatch.setattr(rtm_mod, "acquire_shipping_lock", lambda *args, **kwargs: False)
    monkeypatch.setattr(rtm_mod, "sleep_with_heartbeat", lambda *args, **kwargs: None)

    rtm_mod.do_it_auto_send(fake, routes, useFreighters=False, telegram_enabled=False)

    assert any("Could not acquire shipping lock" in st for st in fake.statuses)
    assert any(st.startswith("[WAITING] Auto Send [1/1]") for st in fake.statuses)


def test_rtm_consolidate_lock_failure_sets_waiting_status(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.statuses = []
            self.username = "u"

        def setStatus(self, status):
            self.statuses.append(status)

        def get(self, url):
            return url

    fake = FakeSession()

    cities = {
        1: {
            "id": 1,
            "name": "Origin",
            "availableResources": [10, 0, 0, 0, 0],
            "islandId": 10,
            "freeSpaceForResources": [100, 100, 100, 100, 100],
            "isOwnCity": True,
        },
        2: {
            "id": 2,
            "name": "Dest",
            "availableResources": [0, 0, 0, 0, 0],
            "islandId": 20,
            "freeSpaceForResources": [100, 100, 100, 100, 100],
            "isOwnCity": True,
        },
    }

    monkeypatch.setattr(rtm_mod, "getCity", lambda html: dict(cities[int(html.split("=")[-1])]))
    monkeypatch.setattr(rtm_mod, "getAvailableShips", lambda _session: 1)
    monkeypatch.setattr(rtm_mod, "acquire_shipping_lock", lambda *args, **kwargs: False)
    monkeypatch.setattr(rtm_mod, "sleep_with_heartbeat", lambda *args, **kwargs: None)

    rtm_mod.do_it(
        fake,
        origin_cities=[{"id": 1, "name": "Origin"}],
        destination_city={"id": 2, "name": "Dest"},
        island={"id": 99, "x": 1, "y": 2},
        interval_hours=0,
        resource_config=[1, None, None, None, None],
        useFreighters=False,
        send_mode=2,
        telegram_enabled=False,
        notify_on_start=False,
    )

    assert any(st.startswith("[WAITING] Origin -> Dest | Could not acquire shipping lock") for st in fake.statuses)


def test_rtm_distribute_lock_failure_sets_waiting_status(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.statuses = []
            self.username = "u"

        def setStatus(self, status):
            self.statuses.append(status)

        def get(self, url):
            return url

    fake = FakeSession()

    cities = {
        1: {
            "id": 1,
            "name": "Origin",
            "availableResources": [10, 0, 0, 0, 0],
            "islandId": 10,
            "freeSpaceForResources": [100, 100, 100, 100, 100],
            "isOwnCity": True,
        },
        2: {
            "id": 2,
            "name": "Dest",
            "availableResources": [0, 0, 0, 0, 0],
            "islandId": 20,
            "freeSpaceForResources": [100, 100, 100, 100, 100],
            "isOwnCity": True,
        },
    }

    monkeypatch.setattr(rtm_mod, "getCity", lambda html: dict(cities[int(html.split("=")[-1])]))
    monkeypatch.setattr(rtm_mod, "getIsland", lambda _html: {"id": 1, "x": 1, "y": 2})
    monkeypatch.setattr(rtm_mod, "getAvailableShips", lambda _session: 1)
    monkeypatch.setattr(rtm_mod, "acquire_shipping_lock", lambda *args, **kwargs: False)
    monkeypatch.setattr(rtm_mod, "sleep_with_heartbeat", lambda *args, **kwargs: None)

    rtm_mod.do_it_distribute(
        fake,
        origin_city={"id": 1, "name": "Origin"},
        destination_cities=[{"id": 2, "name": "Dest"}],
        interval_hours=0,
        resource_config=[1, 0, 0, 0, 0],
        useFreighters=False,
        telegram_enabled=False,
        notify_on_start=False,
    )

    assert any(st.startswith("[WAITING] Origin -> Dest | Could not acquire shipping lock") for st in fake.statuses)

def test_activate_miracle_do_it_status_prefixes(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.statuses = []

        def setStatus(self, status):
            self.statuses.append(status)

    fake = FakeSession()
    monkeypatch.setattr(am_mod, "wait_for_miracle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(am_mod, "activateMiracleHttpCall", lambda *_args, **_kwargs: [None, [None, ["ok"]], None])

    am_mod.do_it(fake, {"wonderName": "Hephaistos"}, iterations=1)

    assert any(st.startswith("[WAITING] Waiting to activate") for st in fake.statuses)
    assert any(st.startswith("[PROCESSING] Activating") for st in fake.statuses)
    assert any(st.startswith("[WAITING] Activated") for st in fake.statuses)


def test_wait_for_miracle_waiting_status_prefix(monkeypatch):
    class FakeSession:
        def __init__(self):
            self.statuses = []

        def setStatus(self, status):
            self.statuses.append(status)

        def post(self, *args, **kwargs):
            return json.dumps([None, None, [None, {"x": {"countdown": {"enddate": "200", "currentdate": "100"}}}]])

    fake = FakeSession()
    monkeypatch.setattr(am_mod, "getDateTime", lambda *_args, **_kwargs: "DATE")
    monkeypatch.setattr(am_mod, "sleep_with_heartbeat", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stop")))

    with pytest.raises(RuntimeError, match="stop"):
        am_mod.wait_for_miracle(fake, {"id": 1, "wonderName": "Athena", "ciudad": {"id": 1, "pos": 0}})

    assert fake.statuses and fake.statuses[-1].startswith("[WAITING] Miracle Athena activated.")
