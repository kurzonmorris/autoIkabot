import threading
import json
from collections import deque
from contextlib import contextmanager

import pytest
import requests
from requests.cookies import RequestsCookieJar

from autoIkabot.ui.prompts import ReturnToMainMenu, read, read_input
import autoIkabot.ui.prompts as prompts
from autoIkabot.ui import menu
from autoIkabot.modules import autoLoader
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
    menu._RUNTIME_CHILD_PIDS.clear()

    menu._dispatch_background(
        FakeSession(),
        {"name": "BgSlow", "func": lambda *_: None, "background": True},
    )

    out = capsys.readouterr().out
    assert "BG_START_TIMEOUT" in out
    assert terminated["value"] is True


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

    menu._dispatch_background(
        FakeSession(),
        {"name": "BgDead", "func": lambda *_: None, "background": True},
    )

    out = capsys.readouterr().out
    assert "BG_START_FAIL" in out


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
