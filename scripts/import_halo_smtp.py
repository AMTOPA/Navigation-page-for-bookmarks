#!/usr/bin/env python3
"""Import Halo notifier SMTP settings into the bookmark navigator .env file.

This script is intended to run on the server host. It reads Halo's database
credentials from /opt/1panel/apps/halo/halo/.env, fetches the notifier secret
from the Halo MySQL extensions table, and writes SMTP_* variables into the
bookmark navigator .env without printing the SMTP password.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
from pathlib import Path


DEFAULT_HALO_ENV = Path("/opt/1panel/apps/halo/halo/.env")
DEFAULT_TARGET_ENV = Path("/opt/bookmark-navigator/.env")
SECRET_NAME = "/registry/secrets/notifier-setting-secret"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"找不到 Halo 环境配置：{path}")
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def mysql_container() -> str:
    output = subprocess.check_output(
        ["docker", "container", "ls", "--format", "{{.Names}}"],
        text=True,
        encoding="utf-8",
    )
    for name in output.splitlines():
        if re.search(r"mysql|mariadb", name, re.I):
            return name
    raise RuntimeError("没有找到 MySQL/MariaDB 容器")


def fetch_secret(halo_env: dict[str, str]) -> dict:
    query = f"select to_base64(data) from extensions where name='{SECRET_NAME}'"
    container = mysql_container()
    raw = subprocess.check_output(
        [
            "docker",
            "exec",
            container,
            "mysql",
            f"-u{halo_env['PANEL_DB_USER']}",
            f"-p{halo_env['PANEL_DB_USER_PASSWORD']}",
            halo_env["PANEL_DB_NAME"],
            "-N",
            "-B",
            "--raw",
            "-e",
            query,
        ],
        text=True,
        encoding="utf-8",
        stderr=subprocess.DEVNULL,
    ).strip()
    if not raw:
        raise RuntimeError(f"Halo extensions 表中没有找到 {SECRET_NAME}")
    secret = json.loads(base64.b64decode(raw).decode("utf-8"))
    nested = secret.get("stringData", {}).get("default-email-notifier.json")
    if isinstance(nested, str):
        secret = json.loads(nested)
    if isinstance(secret.get("sender"), dict):
        return secret["sender"]
    return secret


def set_env_values(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines() if path.exists() else []
    seen: set[str] = set()
    next_lines: list[str] = []
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            next_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            next_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            next_lines.append(line)
    if next_lines and next_lines[-1].strip():
        next_lines.append("")
    for key, value in updates.items():
        if key not in seen:
            next_lines.append(f"{key}={value}")
    path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--halo-env", type=Path, default=DEFAULT_HALO_ENV)
    parser.add_argument("--target-env", type=Path, default=DEFAULT_TARGET_ENV)
    args = parser.parse_args()

    halo_env = read_env(args.halo_env)
    secret = fetch_secret(halo_env)
    encryption = str(secret.get("encryption", "")).upper()
    updates = {
        "SMTP_HOST": str(secret.get("host", "")).strip(),
        "SMTP_PORT": str(secret.get("port", "465")).strip(),
        "SMTP_USERNAME": str(secret.get("username", "")).strip(),
        "SMTP_PASSWORD": str(secret.get("password", "")).strip(),
        "SMTP_FROM": str(secret.get("sender") or secret.get("username") or "").strip(),
        "SMTP_USE_SSL": "true" if encryption == "SSL" else "false",
        "SMTP_USE_TLS": "true" if encryption in {"STARTTLS", "TLS"} else "false",
    }
    missing = [key for key, value in updates.items() if key in {"SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"} and not value]
    if missing:
        raise RuntimeError(f"Halo 邮件配置不完整：{', '.join(missing)}")
    set_env_values(args.target_env, updates)
    print(
        "已同步 Halo SMTP 配置："
        f"host={updates['SMTP_HOST']} port={updates['SMTP_PORT']} "
        f"user={'已设置' if updates['SMTP_USERNAME'] else '未设置'} password=***"
    )


if __name__ == "__main__":
    main()
