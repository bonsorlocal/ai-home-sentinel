"""One-shot helper: merge /tmp/_dash_token.tmp into Pi secrets.yaml."""

from pathlib import Path

import yaml

root = Path("/home/sentinel/ai-home-sentinel")
secrets_path = root / "secrets.yaml"
token_path = Path("/tmp/_dash_token.tmp")
token = token_path.read_text(encoding="utf-8").strip()
token_path.unlink(missing_ok=True)
if not token:
    raise SystemExit("empty token")

data = {}
if secrets_path.exists():
    loaded = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
    if isinstance(loaded, dict):
        data = loaded
data["dashboard_token"] = token
secrets_path.write_text(
    yaml.safe_dump(data, default_flow_style=False, sort_keys=False),
    encoding="utf-8",
)
print("ok")
