"""SnowLuma 内置 WebUI HTTP 辅助（登录、EULA 同意、进程列表）。"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SnowlumaWebuiLogin:
    headers: dict[str, str]
    must_change_password: bool = False


def snowluma_consent_config_path(account: dict) -> Path | None:
    if not bool(account.get("snowluma_linux_docker")):
        return None
    from .snowluma_docker import snowluma_docker_volume_paths

    data_dir, _, _ = snowluma_docker_volume_paths(account)
    return data_dir / "config" / "consent.json"


def write_snowluma_consent_record(account: dict, version: str) -> Path | None:
    path = snowluma_consent_config_path(account)
    if path is None or not version.strip():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": version.strip(),
        "acceptedAt": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def generate_snowluma_managed_webui_password() -> str:
    """生成满足 SnowLuma WebUI 强度要求的管理口令（≥10、大小写、特殊符号）。"""
    core = secrets.token_hex(6)
    return f"Pa!{core}Xy9"


def snowluma_webui_password_candidates(account: dict, log_lines: list[str] | None) -> list[str]:
    from .snowluma_config import resolve_snowluma_webui_temp_password

    out: list[str] = []
    managed = str(account.get("snowluma_managed_webui_password") or "").strip()
    if managed:
        out.append(managed)
    bootstrap = resolve_snowluma_webui_temp_password(account, log_lines)
    if bootstrap and bootstrap not in out:
        out.append(bootstrap)
    return out


def snowluma_webui_403_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        msg = str(body.get("message") or body.get("status") or "").strip()
        if msg:
            return msg
        if body.get("mustChangePassword"):
            return "请先修改 SnowLuma WebUI 密码"
        if body.get("consentRequired"):
            return "SnowLuma WebUI 尚未同意 EULA/Privacy"
    return response.text.strip() or f"HTTP {response.status_code}"


async def snowluma_accept_webui_consent_if_needed(
    client: httpx.AsyncClient,
    base: str,
    *,
    headers: dict[str, str] | None = None,
    account: dict | None = None,
) -> bool:
    """若 SnowLuma WebUI 要求 EULA/Privacy 同意，则自动 record-consent。"""
    hdrs = dict(headers or {})
    ar = await client.get(f"{base}/api/agreements", headers=hdrs)
    if ar.status_code == 401 and hdrs:
        ar = await client.get(f"{base}/api/agreements")
    if ar.status_code == 403:
        try:
            body = ar.json()
        except Exception:
            body = {}
        if isinstance(body, dict) and body.get("consentRequired"):
            version = str(body.get("version") or "").strip()
            if version and account:
                write_snowluma_consent_record(account, version)
            if version:
                cr = await client.post(
                    f"{base}/api/agreements/record-consent",
                    json={"version": version},
                    headers=hdrs,
                )
                if cr.status_code < 400:
                    return True
        return False
    if ar.status_code >= 400:
        return False
    try:
        body = ar.json()
    except Exception:
        return False
    if not isinstance(body, dict):
        return False
    if not body.get("consentRequired"):
        return False
    version = str(body.get("version") or "").strip()
    if not version:
        return False
    cr = await client.post(
        f"{base}/api/agreements/record-consent",
        json={"version": version},
        headers=hdrs,
    )
    if cr.status_code >= 400 and account:
        write_snowluma_consent_record(account, version)
        return True
    cr.raise_for_status()
    if account:
        write_snowluma_consent_record(account, version)
    logger.info("SnowLuma WebUI 已自动确认 EULA/Privacy (version={})", version)
    return True


async def snowluma_webui_login(client: httpx.AsyncClient, base: str, password: str) -> SnowlumaWebuiLogin:
    lr = await client.post(
        f"{base}/api/login",
        json={"username": "admin", "password": password},
    )
    lr.raise_for_status()
    login_body = lr.json()
    if not isinstance(login_body, dict) or not login_body.get("success"):
        msg = ""
        if isinstance(login_body, dict):
            msg = str(login_body.get("message") or login_body.get("status") or "")
        raise ValueError(f"SnowLuma WebUI 登录失败: {msg or lr.text}")
    token = str(login_body.get("token") or "").strip()
    if not token:
        raise ValueError("SnowLuma WebUI 未返回登录 token")
    return SnowlumaWebuiLogin(
        headers={"Authorization": f"Bearer {token}"},
        must_change_password=bool(login_body.get("mustChangePassword")),
    )


async def snowluma_change_webui_password(
    client: httpx.AsyncClient,
    base: str,
    headers: dict[str, str],
    old_password: str,
    new_password: str,
) -> None:
    cr = await client.post(
        f"{base}/api/auth/change-password",
        json={"oldPassword": old_password, "newPassword": new_password},
        headers=headers,
    )
    if cr.status_code >= 400:
        detail = snowluma_webui_403_detail(cr)
        raise ValueError(f"SnowLuma WebUI 改密失败: {detail}")
    try:
        body = cr.json()
    except Exception:
        body = {}
    if isinstance(body, dict) and body.get("success") is False:
        msg = str(body.get("message") or body.get("status") or "").strip()
        raise ValueError(f"SnowLuma WebUI 改密失败: {msg or cr.text}")


async def snowluma_ensure_webui_session(
    client: httpx.AsyncClient,
    base: str,
    account: dict,
    log_lines: list[str] | None,
) -> tuple[dict[str, str], bool]:
    """登录 WebUI、自动改密（若必须）、同意 EULA，返回可用 Authorization headers。"""
    candidates = snowluma_webui_password_candidates(account, log_lines)
    if not candidates:
        raise ValueError(
            "无法获取 SnowLuma WebUI 登录口令：请先启动本实例并在进程日志中查找 "
            "「initial credentials: user=admin password=…」（旧版为「临时密码」）。"
        )

    last_err: Exception | None = None
    account_dirty = False
    for pwd in candidates:
        try:
            login = await snowluma_webui_login(client, base, pwd)
        except Exception as err:
            last_err = err
            continue

        headers = login.headers
        active_pwd = pwd
        # SnowLuma 现要求先同意 EULA，再允许 change-password；顺序反了会 403，
        # mustChangePassword 永远无法落盘，托管口令也就写不进 accounts.json。
        await snowluma_accept_webui_consent_if_needed(client, base, headers=headers, account=account)
        if login.must_change_password:
            new_pwd = generate_snowluma_managed_webui_password()
            await snowluma_change_webui_password(client, base, headers, active_pwd, new_pwd)
            account["snowluma_managed_webui_password"] = new_pwd
            account_dirty = True
            active_pwd = new_pwd
            login = await snowluma_webui_login(client, base, active_pwd)
            headers = login.headers
            if login.must_change_password:
                raise ValueError("SnowLuma WebUI 改密后仍要求修改密码")
            await snowluma_accept_webui_consent_if_needed(client, base, headers=headers, account=account)

        probe = await client.get(f"{base}/api/processes", headers=headers)
        if probe.status_code == 403:
            detail = snowluma_webui_403_detail(probe)
            try:
                body = probe.json()
            except Exception:
                body = {}
            must_change = isinstance(body, dict) and bool(body.get("mustChangePassword"))
            if "密码" in detail or must_change:
                if str(account.get("snowluma_managed_webui_password") or "") == pwd:
                    account.pop("snowluma_managed_webui_password", None)
                    account_dirty = True
                last_err = ValueError(f"SnowLuma WebUI 访问受限: {detail}")
                continue
            raise ValueError(f"SnowLuma WebUI 访问受限: {detail}")
        probe.raise_for_status()
        return headers, account_dirty

    if last_err is not None:
        raise last_err
    raise ValueError("SnowLuma WebUI 登录失败")


async def snowluma_fetch_processes(
    client: httpx.AsyncClient, base: str, headers: dict[str, str]
) -> list[dict[str, Any]]:
    pr = await client.get(f"{base}/api/processes", headers=headers)
    if pr.status_code == 403:
        detail = snowluma_webui_403_detail(pr)
        raise ValueError(f"SnowLuma WebUI 无法列出进程: {detail}")
    pr.raise_for_status()
    plist_raw = pr.json()
    if isinstance(plist_raw, dict):
        procs = plist_raw.get("list") or []
        if isinstance(procs, list):
            return [p for p in procs if isinstance(p, dict)]
    if isinstance(plist_raw, list):
        return [p for p in plist_raw if isinstance(p, dict)]
    return []


__all__ = [
    "SnowlumaWebuiLogin",
    "generate_snowluma_managed_webui_password",
    "snowluma_accept_webui_consent_if_needed",
    "snowluma_change_webui_password",
    "snowluma_consent_config_path",
    "snowluma_ensure_webui_session",
    "snowluma_fetch_processes",
    "snowluma_webui_login",
    "snowluma_webui_password_candidates",
    "write_snowluma_consent_record",
]
