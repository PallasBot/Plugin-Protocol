"""SnowLuma QR 截屏识别单元测试。"""

from __future__ import annotations

import importlib.util
import io
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parents[1] / "src" / "pallas_plugin_protocol"
_PKG = "pallas_plugin_protocol_qr_capture_test"


def load_module(qualified: str, filename: str):
    path = _ROOT / filename
    spec = importlib.util.spec_from_file_location(qualified, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = mod
    spec.loader.exec_module(mod)
    return mod


if _PKG not in sys.modules:
    pkg = types.ModuleType(_PKG)
    pkg.__path__ = [str(_ROOT)]
    sys.modules[_PKG] = pkg

load_module(f"{_PKG}.contract", "contract.py")

snowluma_docker_stub = types.ModuleType(f"{_PKG}.snowluma_docker")
snowluma_docker_stub.snowluma_docker_container_name = lambda account: (
    f"pallas-proto-sl-{account.get('id', 'x')}"
)
snowluma_docker_stub.snowluma_docker_container_name_for_runtime = lambda runtime: (
    f"pallas-proto-sl-{runtime.get('id', 'x')}"
)
sys.modules[f"{_PKG}.snowluma_docker"] = snowluma_docker_stub

linux_docker_stub = types.ModuleType(f"{_PKG}.linux_docker")
linux_docker_stub.sanitize_docker_name_suffix = lambda value: str(value or "x")
sys.modules[f"{_PKG}.linux_docker"] = linux_docker_stub

multi_qq = load_module(f"{_PKG}.snowluma_multi_qq", "snowluma_multi_qq.py")

qr_capture = load_module(
    f"{_PKG}.snowluma_qr_capture", "snowluma_qr_capture.py"
)
account_uses_snowluma_docker = qr_capture.account_uses_snowluma_docker
capture_snowluma_qrcode_once = qr_capture.capture_snowluma_qrcode_once
extract_qr_png_from_screen = qr_capture.extract_qr_png_from_screen
find_qq_login_window = qr_capture.find_qq_login_window
list_qq_login_windows = qr_capture.list_qq_login_windows
pick_qq_login_window = qr_capture.pick_qq_login_window
is_known_qq_login_failure_text = qr_capture.is_known_qq_login_failure_text
is_known_qq_expired_qrcode_text = qr_capture.is_known_qq_expired_qrcode_text
is_known_dismissable_xmessage_text = qr_capture.is_known_dismissable_xmessage_text
is_valid_qq_login_qr_payload = qr_capture.is_valid_qq_login_qr_payload
qq_auto_login_checkbox_is_checked = qr_capture.qq_auto_login_checkbox_is_checked
write_qrcode_cache = qr_capture.write_qrcode_cache
qrcode_cache_looks_valid = qr_capture.qrcode_cache_looks_valid


def test_is_valid_qq_login_qr_payload() -> None:
    assert is_valid_qq_login_qr_payload(b"https://txz.qq.com/p?k=abc&f=1600001615")
    assert not is_valid_qq_login_qr_payload(b"https://example.com/")
    assert not is_valid_qq_login_qr_payload(b"not-a-url")


def test_find_qq_login_window() -> None:
    tree = """
     0xa00001 "qq": ("qq" "Qq")  10x10+10+10  +10+10
        0x800003 "QQ": ("qq" "QQ")  320x460+0+0  +800+300
        0x600032 "xmessage": ("xmessage" "Xmessage")  578x96+0+19  +671+500
    """
    found = find_qq_login_window(tree)
    assert found == ("0x800003", 320, 460)


def test_pick_qq_login_window_prefers_matching_pid() -> None:
    tree = """
        0x800001 "QQ": ("qq" "QQ")  320x460+0+0  +100+100
        0x800002 "QQ": ("qq" "QQ")  400x500+0+0  +200+200
    """
    windows = list_qq_login_windows(tree)
    assert [item[0] for item in windows] == ["0x800001", "0x800002"]

    def pid_of(window_id: str) -> int | None:
        return {"0x800001": 111, "0x800002": 222}.get(window_id)

    picked = pick_qq_login_window(tree, prefer_pid=222, window_pid_of=pid_of)
    assert picked == ("0x800002", 400, 500)
    fallback = pick_qq_login_window(tree, prefer_pid=999, window_pid_of=pid_of)
    assert fallback == ("0x800001", 320, 460)


def test_known_qq_login_failure_text_matches_s26_dialog() -> None:
    assert is_known_qq_login_failure_text("身份验证失败，请你重新登录。(s26)")


def test_known_qq_login_failure_text_matches_offline_notice() -> None:
    assert is_known_qq_login_failure_text(
        "下线通知\n你的账号当前登录已失效，请重新登录。"
    )


def test_known_dismissable_xmessage_text_matches_fbsetbg_warning() -> None:
    assert is_known_dismissable_xmessage_text(
        "fbsetbg: Something went wrong while setting wallpaper"
    )
    assert not is_known_dismissable_xmessage_text("删除所有账号数据")


def test_known_qq_expired_qrcode_text_matches_refresh_prompt() -> None:
    assert is_known_qq_expired_qrcode_text("当前二维码已过期\n刷新")
    assert not is_known_qq_expired_qrcode_text("手机QQ扫码登录")


def test_qq_auto_login_checkbox_detects_checked_blue_control() -> None:
    unchecked = Image.new("RGB", (320, 460), color=(242, 242, 242))
    unchecked.putpixel((132, 372), (188, 188, 188))
    unchecked_buf = io.BytesIO()
    unchecked.save(unchecked_buf, format="PNG")

    checked = Image.new("RGB", (320, 460), color=(242, 242, 242))
    for x in range(126, 139):
        for y in range(366, 379):
            checked.putpixel((x, y), (0, 141, 235))
    checked_buf = io.BytesIO()
    checked.save(checked_buf, format="PNG")

    assert not qq_auto_login_checkbox_is_checked(unchecked_buf.getvalue(), 320, 460)
    assert qq_auto_login_checkbox_is_checked(checked_buf.getvalue(), 320, 460)


def test_account_uses_snowluma_docker() -> None:
    assert account_uses_snowluma_docker(
        {"protocol_backend": "snowluma", "snowluma_linux_docker": True}
    )
    assert not account_uses_snowluma_docker(
        {"protocol_backend": "napcat", "snowluma_linux_docker": True}
    )
    assert not account_uses_snowluma_docker(
        {"protocol_backend": "snowluma", "snowluma_linux_docker": False}
    )


def test_extract_qr_png_from_screen_with_mock_decode() -> None:
    base = Image.new("RGB", (400, 300), color=(30, 30, 30))
    buf = io.BytesIO()
    base.save(buf, format="PNG")
    screen = buf.getvalue()

    class FakeCode:
        rect = SimpleNamespace(left=100, top=80, width=120, height=120)
        data = b"https://txz.qq.com/p?k=test&f=1"

    fake_pyzbar_inner = types.ModuleType("pyzbar.pyzbar")
    fake_pyzbar_inner.decode = lambda img: [FakeCode()]
    fake_pyzbar = types.ModuleType("pyzbar")
    fake_pyzbar.pyzbar = fake_pyzbar_inner

    with patch.dict(
        sys.modules,
        {"pyzbar": fake_pyzbar, "pyzbar.pyzbar": fake_pyzbar_inner},
    ):
        out = extract_qr_png_from_screen(screen)
    assert out is not None
    img = Image.open(io.BytesIO(out))
    assert img.width >= 120
    assert img.height >= 120


def test_capture_snowluma_qrcode_once_writes_cache(tmp_path: Path) -> None:
    ad = tmp_path / "inst" / "snowluma"
    ad.mkdir(parents=True)
    account = {
        "id": "acc1",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
        "account_data_dir": str(ad),
    }
    fake_screen = io.BytesIO()
    Image.new("RGB", (640, 480), color=(20, 20, 20)).save(fake_screen, format="PNG")
    fake_qr = b"fake-qr-png"

    def fake_window_tree(*_a, **_k) -> str:
        return '     0x800003 "QQ": ("qq" "QQ")  320x460+0+0  +800+300'

    with (
        patch.object(
            qr_capture,
            "capture_screen_png_from_container",
            return_value=fake_screen.getvalue(),
        ),
        patch.object(qr_capture, "extract_qr_png_from_screen", return_value=fake_qr),
        patch.object(qr_capture, "click_qq_login_window", return_value=True),
        patch.object(
            qr_capture, "ensure_qq_auto_login_checked", return_value=True
        ) as auto_login,
        patch.object(
            qr_capture, "confirm_known_qq_login_failure_dialog", return_value=False
        ),
    ):
        out = capture_snowluma_qrcode_once(
            account,
            run_exec_text=fake_window_tree,
        )
    assert out is not None
    assert out == ad / "cache" / "qrcode.png"
    assert out.read_bytes() == fake_qr
    auto_login.assert_called_once()


def test_capture_snowluma_qrcode_once_skips_invalid_screen(tmp_path: Path) -> None:
    ad = tmp_path / "inst" / "snowluma"
    ad.mkdir(parents=True)
    account = {
        "id": "acc1",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
        "account_data_dir": str(ad),
    }
    fake_screen = io.BytesIO()
    Image.new("RGB", (640, 480), color=(20, 20, 20)).save(fake_screen, format="PNG")

    def fake_window_tree(*_a, **_k) -> str:
        return '     0x800003 "QQ": ("qq" "QQ")  320x460+0+0  +800+300'

    with (
        patch.object(
            qr_capture,
            "capture_screen_png_from_container",
            return_value=fake_screen.getvalue(),
        ),
        patch.object(qr_capture, "extract_qr_png_from_screen", return_value=None),
        patch.object(qr_capture, "click_qq_login_window", return_value=True),
        patch.object(
            qr_capture, "confirm_known_qq_login_failure_dialog", return_value=False
        ),
    ):
        out = capture_snowluma_qrcode_once(
            account,
            run_exec_text=fake_window_tree,
        )
    assert out is None
    assert not (ad / "cache" / "qrcode.png").exists()


def test_capture_snowluma_qrcode_once_refreshes_expired_qrcode(tmp_path: Path) -> None:
    ad = tmp_path / "inst" / "snowluma"
    ad.mkdir(parents=True)
    account = {
        "id": "expired-qrcode",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
        "account_data_dir": str(ad),
    }
    fake_qr = b"refreshed-qr"

    with (
        patch.object(
            qr_capture,
            "locate_qq_login_window",
            return_value=("0x800003", 320, 460),
        ),
        patch.object(
            qr_capture,
            "capture_screen_png_from_container",
            side_effect=[b"expired", b"fresh"],
        ),
        patch.object(
            qr_capture,
            "extract_qr_png_from_screen",
            side_effect=[None, fake_qr],
        ),
        patch.object(
            qr_capture, "click_known_qq_expired_qrcode_refresh", return_value=True
        ) as refresh,
    ):
        out = capture_snowluma_qrcode_once(account)

    assert out == ad / "cache" / "qrcode.png"
    assert out.read_bytes() == fake_qr
    refresh.assert_called_once_with(
        "pallas-proto-sl-expired-qrcode",
        "0x800003",
        320,
        460,
        display=qr_capture.DEFAULT_DISPLAY,
        run_exec=None,
        run_exec_text=None,
    )


def test_click_qq_auto_login_checkbox_coords() -> None:
    calls: list[str] = []

    def fake_exec(container: str, cmd_tail: list[str], *, display: str) -> int:
        if cmd_tail[:2] == ["sh", "-c"]:
            calls.append(cmd_tail[2])
        return 0

    def fake_text(container: str, cmd_tail: list[str], *, display: str) -> str:
        if cmd_tail[0] == "sh":
            return "/usr/bin/xdotool"
        return ""

    ok = qr_capture.click_qq_auto_login_checkbox(
        "pallas-proto-sl-x",
        "0x800003",
        320,
        460,
        run_exec=fake_exec,
        run_exec_text=fake_text,
    )
    assert ok
    assert calls
    # 320*0.415≈132, 460*0.81≈372
    assert "132 372" in calls[-1]
    assert "click 1" in calls[-1]


def test_restore_snowluma_qq_login_prefer_quick_login(tmp_path: Path) -> None:
    ad = tmp_path / "inst" / "snowluma"
    ad.mkdir(parents=True)
    account = {
        "id": "acc-auto",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
        "account_data_dir": str(ad),
    }
    calls: list[str] = []

    def fake_quick(*args: object, **kwargs: object) -> bool:
        calls.append("quick")
        return True

    def fake_capture(*args: object, **kwargs: object) -> Path | None:
        calls.append("capture")
        return None

    with (
        patch.object(
            qr_capture, "attempt_snowluma_quick_login", side_effect=fake_quick
        ),
        patch.object(
            qr_capture, "capture_snowluma_qrcode_once", side_effect=fake_capture
        ),
    ):
        out = qr_capture.restore_snowluma_qq_login(account, prefer_quick_login=True)
    assert out["mode"] == "quick_login"
    assert calls == ["capture", "quick", "capture"]


def test_restore_snowluma_qq_login_captures_qrcode_after_quick_login(
    tmp_path: Path,
) -> None:
    ad = tmp_path / "inst" / "snowluma"
    ad.mkdir(parents=True)
    account = {
        "id": "acc-after-quick-login",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
        "account_data_dir": str(ad),
    }
    qrcode_path = ad / "cache" / "qrcode.png"
    calls: list[str] = []

    def fake_capture(*args: object, **kwargs: object) -> Path | None:
        calls.append("capture")
        return qrcode_path if calls.count("capture") == 2 else None

    def fake_quick(*args: object, **kwargs: object) -> bool:
        calls.append("quick")
        return True

    with (
        patch.object(
            qr_capture, "capture_snowluma_qrcode_once", side_effect=fake_capture
        ),
        patch.object(qr_capture, "ensure_qq_auto_login_checked", return_value=True),
        patch.object(
            qr_capture, "attempt_snowluma_quick_login", side_effect=fake_quick
        ),
    ):
        out = qr_capture.restore_snowluma_qq_login(account, prefer_quick_login=True)

    assert out == {"mode": "qrcode", "qrcode_path": str(qrcode_path)}
    assert calls == ["capture", "quick", "capture"]


@pytest.mark.asyncio
async def test_wait_and_restore_snowluma_qq_login_polls() -> None:
    account = {
        "id": "acc-wait",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
        "account_data_dir": "/tmp/unused",
    }
    n = {"i": 0}

    def fake_restore(*args: object, **kwargs: object) -> dict:
        n["i"] += 1
        if n["i"] < 2:
            return {"mode": "failed", "message": "not yet"}
        return {"mode": "quick_login", "message": "ok"}

    with patch.object(
        qr_capture, "restore_snowluma_qq_login", side_effect=fake_restore
    ):
        out = await qr_capture.wait_and_restore_snowluma_qq_login(
            account,
            timeout_sec=5,
            initial_delay_sec=0,
            poll_interval_sec=0.01,
            prefer_quick_login=True,
        )
    assert out["mode"] == "quick_login"
    assert n["i"] >= 2


def test_restore_snowluma_qq_login_quick_login(tmp_path: Path) -> None:
    ad = tmp_path / "inst" / "snowluma"
    ad.mkdir(parents=True)
    account = {
        "id": "acc1",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
        "account_data_dir": str(ad),
    }
    fake_screen = io.BytesIO()
    Image.new("RGB", (640, 480), color=(20, 20, 20)).save(fake_screen, format="PNG")
    click_calls: list[str] = []

    def fake_exec(container: str, cmd_tail: list[str], *, display: str) -> int:
        if cmd_tail[:2] == ["sh", "-c"] and "click 1" in cmd_tail[2]:
            click_calls.append(cmd_tail[2])
        return 0

    def fake_cp(container: str, remote: str) -> bytes | None:
        return fake_screen.getvalue()

    def fake_window_tree(container: str, cmd_tail: list[str], *, display: str) -> str:
        if cmd_tail[0] == "sh":
            return "/usr/bin/xdotool"
        return '     0x800003 "QQ": ("qq" "QQ")  320x460+0+0  +800+300'

    with (
        patch.object(
            qr_capture,
            "capture_screen_png_from_container",
            return_value=fake_screen.getvalue(),
        ),
        patch.object(qr_capture, "extract_qr_png_from_screen", return_value=None),
        patch.object(qr_capture, "_command_available_in_container", return_value=True),
    ):
        out = qr_capture.restore_snowluma_qq_login(
            account,
            run_exec=fake_exec,
            run_exec_text=fake_window_tree,
        )
    assert out["mode"] == "quick_login"
    assert click_calls
    assert "mousemove" in click_calls[-1]
    assert "click 1" in click_calls[-1]


def test_write_qrcode_cache(tmp_path: Path) -> None:
    ad = tmp_path / "data"
    path = write_qrcode_cache(ad, b"\x89PNG")
    assert path.is_file()
    assert path.parent.name == "cache"
    assert path.name == "qrcode.png"


def test_write_qrcode_cache_per_uin(tmp_path: Path) -> None:
    ad = tmp_path / "data"
    path = write_qrcode_cache(ad, b"\x89PNG-uin", qq="3803448823")
    assert path == ad / "cache" / "qrcode_3803448823.png"
    assert path.read_bytes() == b"\x89PNG-uin"
    assert (ad / "cache" / "qrcode.png").read_bytes() == b"\x89PNG-uin"


def test_resolve_account_qq_home_primary_vs_extra() -> None:
    members = ["3879348674", "3803448823"]
    assert multi_qq.resolve_account_qq_home(
        "3879348674",
        member_uins=members,
        primary_uin="3879348674",
    ) == "/app"
    assert multi_qq.resolve_account_qq_home(
        "3803448823",
        member_uins=members,
        primary_uin="3879348674",
    ) == "/app/qq-homes/3803448823"


def test_append_snowluma_multi_qq_docker_args(tmp_path: Path) -> None:
    argv: list[str] = []
    extras = multi_qq.append_snowluma_multi_qq_docker_args(
        argv,
        data_dir=tmp_path,
        member_uins=["111", "222"],
        primary_uin="111",
    )
    assert extras == ["/app/qq-homes/222"]
    joined = " ".join(argv)
    assert f"{tmp_path.resolve()}/docker/snowluma/qq-homes:/app/qq-homes" in joined
    assert "SNOWLUMA_EXTRA_QQ_HOMES=/app/qq-homes/222" in joined
    assert (tmp_path / "docker" / "snowluma" / "qq-homes" / "222").is_dir()


def test_capture_screen_png_from_window_id() -> None:
    fake_png = b"\x89PNG-window"

    def fake_exec(container: str, cmd_tail: list[str], *, display: str) -> int:
        assert container == "pallas-proto-sl-acc3"
        if cmd_tail[:3] == ["xwd", "-id", "0x800003"]:
            return 0
        return 1

    def fake_cp(container: str, remote: str) -> bytes | None:
        return b"fake-xwd"

    def fake_convert(xwd: bytes) -> bytes | None:
        return fake_png

    out = qr_capture.capture_screen_png_from_container(
        "pallas-proto-sl-acc3",
        window_id="0x800003",
        run_exec=fake_exec,
        run_cp=fake_cp,
        xwd_to_png=fake_convert,
    )
    assert out == fake_png


def test_capture_screen_png_from_xwd_fallback() -> None:
    fake_png = b"\x89PNG-xwd-fallback"

    def fake_exec(container: str, cmd_tail: list[str], *, display: str) -> int:
        assert container == "pallas-proto-sl-acc2"
        if cmd_tail[0] in {"import", "scrot"}:
            return 1
        assert cmd_tail[:3] == ["xwd", "-root", "-silent"]
        return 0

    def fake_cp(container: str, remote: str) -> bytes | None:
        assert remote.endswith(".xwd")
        return b"fake-xwd"

    def fake_convert(xwd: bytes) -> bytes | None:
        assert xwd == b"fake-xwd"
        return fake_png

    out = qr_capture.capture_screen_png_from_container(
        "pallas-proto-sl-acc2",
        run_exec=fake_exec,
        run_cp=fake_cp,
        xwd_to_png=fake_convert,
    )
    assert out == fake_png


def test_attempt_quick_login_dismisses_only_expired_session_prompt() -> None:
    account = {
        "id": "expired",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
    }
    calls: list[tuple[list[str], str]] = []
    trees = iter(
        (
            ' 0x1 "xmessage": ("xmessage" "Xmessage") 578x96+0+0\n'
            ' 0x2 "QQ": ("qq" "QQ") 320x460+0+0',
            ' 0x2 "QQ": ("qq" "QQ") 320x460+0+0',
        )
    )

    def text_runner(_container: str, cmd: list[str], *, display: str) -> str:
        if cmd == ["xwininfo", "-root", "-tree"]:
            return next(trees)
        if cmd == ["xprop", "-id", "0x1", "WM_NAME"]:
            return 'WM_NAME(STRING) = "账号当前登录已失效"'
        assert cmd[:2] == ["sh", "-c"]
        return qr_capture.OCR_UNAVAILABLE_SENTINEL

    def exec_runner(_container: str, cmd: list[str], *, display: str) -> int:
        calls.append((cmd, display))
        return 0

    with (
        patch.object(qr_capture, "_command_available_in_container", return_value=True),
        patch.object(qr_capture, "xmessage_is_known_dismissable", return_value=True),
        patch.object(qr_capture, "ensure_qq_auto_login_checked", return_value=True),
        patch.object(qr_capture, "resolve_prefer_qq_pid_for_account", return_value=None),
        patch.object(
            qr_capture, "confirm_known_qq_login_failure_dialog", return_value=False
        ),
    ):
        assert qr_capture.attempt_snowluma_quick_login(
            account, run_exec=exec_runner, run_exec_text=text_runner
        )
    assert calls[0][0] == ["xkill", "-id", "0x1"]
    assert "click 1" in calls[1][0][2]


def test_attempt_quick_login_does_not_dismiss_unrecognized_xmessage() -> None:
    account = {
        "id": "other-message",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
    }
    calls: list[list[str]] = []

    def text_runner(_container: str, cmd: list[str], *, display: str) -> str:
        if cmd == ["xwininfo", "-root", "-tree"]:
            return (
                ' 0x1 "xmessage": ("xmessage" "Xmessage") 578x96+0+0\n'
                ' 0x2 "QQ": ("qq" "QQ") 320x460+0+0'
            )
        assert cmd == ["xprop", "-id", "0x1", "WM_NAME"]
        return 'WM_NAME(STRING) = "其他提示"'

    def exec_runner(_container: str, cmd: list[str], *, display: str) -> int:
        calls.append(cmd)
        return 0

    with patch.object(qr_capture, "xmessage_is_known_dismissable", return_value=False):
        assert not qr_capture.attempt_snowluma_quick_login(
            account, run_exec=exec_runner, run_exec_text=text_runner
        )
    assert calls == []


def test_attempt_quick_login_dismisses_recognized_fbsetbg_xmessage() -> None:
    account = {
        "id": "fbsetbg-warning",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
    }
    trees = iter(
        (
            ' 0x1 "xmessage": ("xmessage" "Xmessage") 921x68+0+0\n'
            ' 0x2 "QQ": ("qq" "QQ") 320x460+0+0',
            ' 0x2 "QQ": ("qq" "QQ") 320x460+0+0',
        )
    )
    calls: list[list[str]] = []

    def text_runner(_container: str, cmd: list[str], *, display: str) -> str:
        if cmd == ["xwininfo", "-root", "-tree"]:
            return next(trees)
        raise AssertionError(cmd)

    def exec_runner(_container: str, cmd: list[str], *, display: str) -> int:
        calls.append(cmd)
        return 0

    with (
        patch.object(qr_capture, "_command_available_in_container", return_value=True),
        patch.object(qr_capture, "xmessage_is_known_dismissable", return_value=True),
        patch.object(qr_capture, "ensure_qq_auto_login_checked", return_value=True),
        patch.object(qr_capture, "resolve_prefer_qq_pid_for_account", return_value=None),
        patch.object(
            qr_capture, "confirm_known_qq_login_failure_dialog", return_value=False
        ),
    ):
        assert qr_capture.attempt_snowluma_quick_login(
            account, run_exec=exec_runner, run_exec_text=text_runner
        )
    assert calls[0] == ["xkill", "-id", "0x1"]
    assert "click 1" in calls[1][2]


def test_restore_stops_at_recognized_qrcode_without_click(tmp_path: Path) -> None:
    account = {
        "id": "qr",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
        "account_data_dir": str(tmp_path),
    }
    with (
        patch.object(
            qr_capture,
            "capture_snowluma_qrcode_once",
            return_value=tmp_path / "cache" / "qrcode.png",
        ),
        patch.object(qr_capture, "ensure_qq_auto_login_checked") as auto_login,
        patch.object(qr_capture, "attempt_snowluma_quick_login") as quick_login,
    ):
        out = qr_capture.restore_snowluma_qq_login(account, prefer_quick_login=False)
    assert out["mode"] == "qrcode"
    auto_login.assert_not_called()
    quick_login.assert_not_called()


def test_qr_capture_has_no_runtime_package_install() -> None:
    source = Path(qr_capture.__file__).read_text()
    assert "ensure_container_xdotool" not in source
    assert "apt-get install" not in source


def test_known_qq_login_failure_text_matches_only_supported_dialogs() -> None:
    matches = qr_capture.is_known_qq_login_failure_text
    assert matches("安全提醒\n身份验证失效，请你重新登录。(s26)")
    assert matches("登录失败\n你的用户身份已失效，为保证账号安全，请你重新登录。")
    assert not matches("登录失败，请稍后重试")


def test_attempt_quick_login_confirms_recognized_qq_failure_dialog() -> None:
    account = {
        "id": "s26",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
    }
    calls: list[list[str]] = []

    def text_runner(_container: str, cmd: list[str], *, display: str) -> str:
        if cmd == ["xwininfo", "-root", "-tree"]:
            return ' 0x2 "QQ": ("qq" "QQ") 320x460+0+0'
        if cmd[:2] == ["xprop", "-id"]:
            return ""
        assert cmd[:2] == ["sh", "-c"]
        return "身份验证失效，请你重新登录。(s26)"

    def exec_runner(_container: str, cmd: list[str], *, display: str) -> int:
        calls.append(cmd)
        return 0

    with (
        patch.object(qr_capture, "_command_available_in_container", return_value=True),
        patch.object(qr_capture, "ensure_qq_auto_login_checked", return_value=True),
        patch.object(qr_capture, "resolve_prefer_qq_pid_for_account", return_value=None),
    ):
        assert qr_capture.attempt_snowluma_quick_login(
            account, run_exec=exec_runner, run_exec_text=text_runner
        )
    assert calls[0][:3] == ["xwd", "-id", "0x2"]
    assert "mousemove --window 0x2 240 276 click 1" in calls[1][2]
    assert "click 1" in calls[2][2]


def test_attempt_quick_login_stops_when_failure_dialog_cannot_be_recognized() -> None:
    account = {
        "id": "unavailable-ocr",
        "protocol_backend": "snowluma",
        "snowluma_linux_docker": True,
    }
    calls: list[list[str]] = []

    def text_runner(_container: str, cmd: list[str], *, display: str) -> str:
        if cmd == ["xwininfo", "-root", "-tree"]:
            return ' 0x2 "QQ": ("qq" "QQ") 320x460+0+0'
        return qr_capture.OCR_UNAVAILABLE_SENTINEL

    def exec_runner(_container: str, cmd: list[str], *, display: str) -> int:
        calls.append(cmd)
        return 0

    assert not qr_capture.attempt_snowluma_quick_login(
        account, run_exec=exec_runner, run_exec_text=text_runner
    )
    assert calls == [
        ["xwd", "-id", "0x2", "-silent", "-out", qr_capture.REMOTE_XWD_PATH]
    ]
