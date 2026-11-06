#!/usr/bin/env python3
"""Extract a Windows-built zip sync into a fresh project dir on the Pi."""
from __future__ import annotations

import os
import shutil
import stat
import zipfile
from pathlib import Path

ZIP_PATH = Path("/tmp/sentinel-sync.zip")
NEW_DIR = Path("/home/sentinel/ai-home-sentinel-new")
OLD_DIR = Path("/home/sentinel/ai-home-sentinel")
SECRETS_BAK = Path("/tmp/secrets.yaml.bak")


def _chmod_tree(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | (stat.S_IXUSR if path.is_dir() else 0))


def main() -> None:
    if NEW_DIR.exists():
        shutil.rmtree(NEW_DIR)
    NEW_DIR.mkdir(parents=True)

    with zipfile.ZipFile(ZIP_PATH) as zf:
        zf.extractall(NEW_DIR)

    _chmod_tree(NEW_DIR)

    if SECRETS_BAK.exists():
        shutil.copy2(SECRETS_BAK, NEW_DIR / "secrets.yaml")

    (NEW_DIR / "secrets").mkdir(exist_ok=True)
    (NEW_DIR / "models").mkdir(exist_ok=True)
    (NEW_DIR / "data" / "dvr").mkdir(parents=True, exist_ok=True)

    marker = NEW_DIR / "sentinel" / ".write_ok"
    marker.write_text("ok\n", encoding="utf-8")

    old_backup = Path("/home/sentinel/ai-home-sentinel.old")
    if old_backup.exists():
        # Best effort cleanup of previous backup
        try:
            _chmod_tree(old_backup)
            shutil.rmtree(old_backup)
        except OSError:
            pass

    if OLD_DIR.exists():
        try:
            _chmod_tree(OLD_DIR)
        except OSError:
            pass
        # Preserve secrets folder from old tree if present
        old_secrets = OLD_DIR / "secrets"
        if old_secrets.is_dir():
            for item in old_secrets.iterdir():
                dest = NEW_DIR / "secrets" / item.name
                if item.is_file() and not dest.exists():
                    shutil.copy2(item, dest)
        try:
            OLD_DIR.rename(old_backup)
        except OSError:
            # Fall back to copying over locked trees
            for src in NEW_DIR.rglob("*"):
                if src.is_dir():
                    continue
                rel = src.relative_to(NEW_DIR)
                dest = OLD_DIR / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dest)
                except OSError as exc:
                    print(f"copy fail {rel}: {exc}")
            print("PARTIAL_OVERLAY")
            print("sentinel_files", len(list((OLD_DIR / "sentinel").glob("*"))))
            return

    NEW_DIR.rename(OLD_DIR)
    print("SWAP_OK")
    print("sentinel_files", len(list((OLD_DIR / "sentinel").glob("*"))))
    print("bootstrap", (OLD_DIR / "scripts" / "bootstrap_fresh_pi.sh").exists())
    print("secrets", (OLD_DIR / "secrets.yaml").exists())


if __name__ == "__main__":
    main()
