#!/usr/bin/env python3
import json
import os
import subprocess
from pathlib import Path

APP_VERSION = "v0.1.3"
CORE_VERSION = "v0.1.3"

API_BASE = os.getenv("EDGESWARM_API_BASE", "https://api.edgeswarm.io")
SERVICE_NAME = "edgeswarm-node"

ENV_PATH = Path("/etc/edgeswarm-node.env")
AUTH_PATH = Path("/etc/edgeswarm-node-auth.json")
STATUS_PATH = Path("/var/lib/edgeswarm-node/ui_status.json")
RELEASE_METADATA_PATH = Path("/opt/edgeswarm-node/RELEASE_METADATA.json")
MODEL_DIR = Path("/var/lib/edgeswarm-node/models")


def load_env_file(path=ENV_PATH):
    env = {}
    if not path.exists():
        return env

    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")

    return env


def read_json(path: Path):
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


def write_status_patch(patch: dict):
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = read_json(STATUS_PATH)
    current.update(patch)
    STATUS_PATH.write_text(json.dumps(current, indent=2))
    return current


def run_cmd(args, timeout=8):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def pkexec(args, timeout=60):
    return run_cmd(["pkexec"] + args, timeout=timeout)


def service_active():
    code, out, _ = run_cmd(["systemctl", "is-active", SERVICE_NAME])
    return code == 0 and out.strip() == "active"


def short_middle(value, left=12, right=6):
    value = str(value or "")
    if len(value) <= left + right + 3:
        return value
    return f"{value[:left]}...{value[-right:]}"


def get_latest_logs():
    code, out, err = run_cmd(
        ["journalctl", "-u", SERVICE_NAME, "-n", "80", "--no-pager", "-l"],
        timeout=6,
    )

    text = out or err or ""
    lines = []

    for line in text.splitlines()[-22:]:
        if "python[" in line:
            lines.append(line.split("]:", 1)[-1].strip())
        elif "edgeswarm-node" in line:
            lines.append(line)

    return "\n".join(lines[-14:]) or "[No logs available yet.]"


def detect_model_status():
    qwen7b = MODEL_DIR / "Qwen2.5-7B-Instruct-Q4_K_M.gguf"

    if qwen7b.exists():
        return {
            "level": "Level 3 Node",
            "modelId": "qwen2.5:7b",
            "capability": "Neural-Inference-7B",
            "neural": True,
        }

    ggufs = list(MODEL_DIR.glob("*.gguf")) if MODEL_DIR.exists() else []
    if ggufs:
        return {
            "level": "Model Ready",
            "modelId": ggufs[0].name,
            "capability": "Neural-Inference",
            "neural": True,
        }

    return {
        "level": "Level 1 Node",
        "modelId": "none",
        "capability": None,
        "neural": False,
    }


def get_hardware_id():
    status = read_json(STATUS_PATH)
    if status.get("hardwareId"):
        return status["hardwareId"]

    code, out, _ = run_cmd(["bash", "-lc", "cat /etc/machine-id 2>/dev/null || hostname"])
    return out.strip() or "unknown"


def get_provider_email():
    status = read_json(STATUS_PATH)
    if status.get("providerEmail"):
        return status["providerEmail"]

    auth = read_json(AUTH_PATH)
    if auth.get("providerEmail"):
        return auth["providerEmail"]

    return "Not signed in"


def get_ledger_defaults(model):
    status = read_json(STATUS_PATH)
    return {
        "balance": status.get("ledgerBalance", "— SWARM"),
        "usd": status.get("ledgerUsd", "—"),
        "rewards": status.get(
            "rewards",
            "Level 3 enabled" if model.get("neural") else "Level 1 enabled",
        ),
        "lastSync": status.get("lastLedgerSync", "Not synced"),
    }
