#!/usr/bin/env python3
"""Run a remote command on the Pi with password auth (env PI_PASS).

Tries PI_USER first, then common fallbacks (CHAT, sentinel).

Usage:
  set PI_PASS=...
  python scripts/pi_remote.py [--sudo] "df -h"
  python scripts/pi_remote.py --put localpath remotepath
"""
from __future__ import annotations

import argparse
import os
import shlex
import sys
import time
from pathlib import Path

import paramiko

DEFAULT_USERS = ("CHAT", "chat", "sentinel")


def connect(host: str, users: list[str], password: str) -> tuple[paramiko.SSHClient, str]:
    last: Exception | None = None
    for user in users:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        for attempt in range(3):
            try:
                client.connect(
                    host,
                    username=user,
                    password=password,
                    timeout=25,
                    allow_agent=False,
                    look_for_keys=False,
                    banner_timeout=40,
                )
                print(f"connected as {user}", flush=True)
                return client, user
            except Exception as exc:  # noqa: BLE001
                last = exc
                print(f"auth retry user={user} attempt={attempt}: {exc}", flush=True)
                time.sleep(2)
        try:
            client.close()
        except Exception:
            pass
    raise SystemExit(f"connect failed for users {users}: {last}")


def run(client: paramiko.SSHClient, password: str, remote: str, sudo: bool = False) -> int:
    cmd = remote
    if sudo:
        cmd = f"echo {shlex.quote(password)} | sudo -S -p '' bash -lc {shlex.quote(remote)}"
    shown = f"[sudo] {remote}" if sudo else remote
    print(f">>> {shown[:180]}", flush=True)
    _stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=900)
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    code = stdout.channel.recv_exit_status()
    # Windows consoles are often cp1252; never crash on remote unicode.
    sys.stdout.buffer.write(out.encode("utf-8", errors="replace"))
    if err:
        sys.stderr.buffer.write(err.encode("utf-8", errors="replace"))
    if not (out + err).endswith("\n"):
        print()
    print(f"exit={code}", flush=True)
    return code


def put(client: paramiko.SSHClient, local: str, remote: str) -> int:
    path = Path(local)
    print(f">>> put {path} -> {remote}", flush=True)
    sftp = client.open_sftp()
    try:
        sftp.put(str(path), remote)
    finally:
        sftp.close()
    print("put_ok", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sudo", action="store_true")
    parser.add_argument("--put", nargs=2, metavar=("LOCAL", "REMOTE"))
    parser.add_argument("--host", default=os.environ.get("PI_HOST_IP", "192.168.1.243"))
    parser.add_argument("--user", default=os.environ.get("PI_USER", ""))
    parser.add_argument("command", nargs="?")
    args = parser.parse_args()
    password = os.environ.get("PI_PASS")
    if not password:
        raise SystemExit("Set PI_PASS in the environment (do not commit it).")

    users: list[str] = []
    if args.user:
        users.append(args.user)
    for candidate in DEFAULT_USERS:
        if candidate not in users:
            users.append(candidate)

    client, _user = connect(args.host, users, password)
    try:
        if args.put:
            return put(client, args.put[0], args.put[1])
        if not args.command:
            raise SystemExit("command required unless --put is used")
        return run(client, password, args.command, sudo=args.sudo)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
