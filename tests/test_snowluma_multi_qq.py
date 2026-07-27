"""SnowLuma 多 QQ 进程启停单元测试。"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "src" / "pallas_plugin_protocol"
_PKG = "pallas_plugin_protocol_multi_qq_test"


def load_module(qualified: str, filename: str):
    path = _ROOT / filename
    spec = importlib.util.spec_from_file_location(qualified, path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = mod
    spec.loader.exec_module(mod)
    return mod


if _PKG not in sys.modules:
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_ROOT)]
    sys.modules[_PKG] = pkg

load_module(f"{_PKG}.linux_docker", "linux_docker.py")
multi = load_module(f"{_PKG}.snowluma_multi_qq", "snowluma_multi_qq.py")


def test_stop_snowluma_qq_process_kills_resolved_pid(monkeypatch) -> None:
    calls: list[list[str]] = []
    homes = {"/app/qq-homes/10002": 4242}

    def fake_exec(container: str, argv: list[str], *, display: str = ":1") -> str:
        del container, display
        calls.append(list(argv))
        joined = " ".join(argv)
        if "cmdline" in joined or "/proc/" in joined:
            return "\n".join(f"{pid} {home}" for home, pid in homes.items())
        if "KILL" in joined or "kill -KILL" in joined or "kill -9" in joined:
            homes.clear()
            return ""
        if "TERM" in joined or "kill -TERM" in joined:
            # Electron 吞 TERM：进程仍在
            return ""
        if "kill" in joined:
            return ""
        return ""

    monkeypatch.setattr(multi, "_docker_exec_text", fake_exec)
    monkeypatch.setattr(multi.time, "sleep", lambda _s: None)

    ok, pid, err = multi.stop_snowluma_qq_process_for_uin(
        "ctr",
        "10002",
        member_uins=["10001", "10002"],
        primary_uin="10001",
    )
    assert ok is True
    assert pid == 4242
    assert err == ""
    joined_calls = [" ".join(c) for c in calls]
    assert any("TERM" in j or "kill -TERM" in j for j in joined_calls)
    assert any("KILL" in j or "kill -9" in j for j in joined_calls)


def test_stop_snowluma_qq_process_noop_when_missing(monkeypatch) -> None:
    def fake_exec(container: str, argv: list[str], *, display: str = ":1") -> str:
        del container, argv, display
        return ""

    monkeypatch.setattr(multi, "_docker_exec_text", fake_exec)
    ok, pid, err = multi.stop_snowluma_qq_process_for_uin(
        "ctr",
        "10002",
        member_uins=["10001", "10002"],
        primary_uin="10001",
    )
    assert ok is True
    assert pid is None
    assert err == ""


def test_stop_primary_qq_uses_supervisorctl(monkeypatch) -> None:
    homes = {"/app": 9001}
    supervisor_calls: list[tuple[str, str]] = []

    def fake_exec(container: str, argv: list[str], *, display: str = ":1") -> str:
        del container, display
        joined = " ".join(argv)
        if "cmdline" in joined or "/proc/" in joined:
            return "\n".join(f"{pid} {home}" for home, pid in homes.items())
        return ""

    def fake_supervisor(
        container: str,
        action: str,
        *,
        program: str = "qq",
        timeout: float = 30.0,
    ) -> tuple[bool, str]:
        del container, timeout
        supervisor_calls.append((action, program))
        if action == "stop":
            homes.clear()
        return True, "qq: stopped"

    monkeypatch.setattr(multi, "_docker_exec_text", fake_exec)
    monkeypatch.setattr(multi, "supervisorctl_qq", fake_supervisor)
    monkeypatch.setattr(multi.time, "sleep", lambda _s: None)

    ok, pid, err = multi.stop_snowluma_qq_process_for_uin(
        "ctr",
        "10001",
        member_uins=["10001", "10002"],
        primary_uin="10001",
    )
    assert ok is True
    assert pid == 9001
    assert err == ""
    assert supervisor_calls == [("stop", "qq")]


def test_stop_extra_qq_uses_supervisorctl_program(monkeypatch) -> None:
    homes = {"/app/qq-homes/10002": 4242}
    supervisor_calls: list[tuple[str, str]] = []

    def fake_exec(container: str, argv: list[str], *, display: str = ":1") -> str:
        del container, display
        joined = " ".join(argv)
        if "extra-qq.conf" in joined:
            return '[program:qq-extra-1]\nenvironment=HOME="/app/qq-homes/10002",DISPLAY=":1"\n'
        if "cmdline" in joined or "/proc/" in joined:
            return "\n".join(f"{pid} {home}" for home, pid in homes.items())
        return ""

    def fake_supervisor(
        container: str,
        action: str,
        *,
        program: str = "qq",
        timeout: float = 30.0,
    ) -> tuple[bool, str]:
        del container, timeout
        supervisor_calls.append((action, program))
        if action == "stop":
            homes.clear()
        return True, f"{program}: stopped"

    monkeypatch.setattr(multi, "_docker_exec_text", fake_exec)
    monkeypatch.setattr(multi, "supervisorctl_qq", fake_supervisor)
    monkeypatch.setattr(multi.time, "sleep", lambda _s: None)

    ok, pid, err = multi.stop_snowluma_qq_process_for_uin(
        "ctr",
        "10002",
        member_uins=["10001", "10002"],
        primary_uin="10001",
    )
    assert ok is True
    assert pid == 4242
    assert err == ""
    assert supervisor_calls == [("stop", "qq-extra-1")]


def test_ensure_primary_qq_uses_supervisorctl_start(monkeypatch) -> None:
    homes: dict[str, int] = {}
    supervisor_calls: list[tuple[str, str]] = []

    def fake_exec(container: str, argv: list[str], *, display: str = ":1") -> str:
        del container, display
        joined = " ".join(argv)
        if "cmdline" in joined or "/proc/" in joined:
            return "\n".join(f"{pid} {home}" for home, pid in homes.items())
        return ""

    def fake_supervisor(
        container: str,
        action: str,
        *,
        program: str = "qq",
        timeout: float = 30.0,
    ) -> tuple[bool, str]:
        del container, timeout
        supervisor_calls.append((action, program))
        if action == "start":
            homes["/app"] = 8800
        return True, "qq: started"

    monkeypatch.setattr(multi, "_docker_exec_text", fake_exec)
    monkeypatch.setattr(multi, "supervisorctl_qq", fake_supervisor)
    monkeypatch.setattr(multi.time, "sleep", lambda _s: None)

    ok, pid, err = multi.ensure_snowluma_qq_process_for_uin(
        "ctr",
        "10001",
        member_uins=["10001"],
        primary_uin="10001",
    )
    assert ok is True
    assert pid == 8800
    assert err == ""
    assert supervisor_calls == [("start", "qq")]


def test_list_qq_main_pids_ignores_crashpad(monkeypatch) -> None:
    def fake_exec(container: str, argv: list[str], *, display: str = ":1") -> str:
        del container, display
        # 模拟容器内脚本输出：仅主 qq（crashpad 已在脚本侧过滤）
        joined = " ".join(argv)
        assert "*/qq" in joined or "qq\\ *" in joined or "qq)" in joined
        return "487 /app/qq-homes/2927116873\n95436 /app/qq-homes/3234802804\n"

    monkeypatch.setattr(multi, "_docker_exec_text", fake_exec)
    mapping = multi.list_qq_main_pids_by_home("ctr")
    assert mapping["/app/qq-homes/2927116873"] == 487
    assert mapping["/app/qq-homes/3234802804"] == 95436
