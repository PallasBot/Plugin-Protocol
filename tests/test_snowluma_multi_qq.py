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
        if "cmdline" in joined:
            return "\n".join(f"{pid} {home}" for home, pid in homes.items())
        if "kill" in joined:
            homes.clear()
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
    assert any("kill" in " ".join(c) for c in calls)


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
