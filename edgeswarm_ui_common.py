#!/usr/bin/env python3
import json
import os
import platform
import subprocess
from pathlib import Path

try:
    from edgeswarm_linux_neural import build_linux_neural_readiness
except Exception:
    build_linux_neural_readiness = None

API_BASE = os.getenv("EDGESWARM_API_BASE", "https://api.edgeswarm.io")
SERVICE_NAME = "edgeswarm-node"

ENV_PATH = Path("/etc/edgeswarm-node.env")
AUTH_PATH = Path("/etc/edgeswarm-node-auth.json")
STATUS_PATH = Path("/var/lib/edgeswarm-node/ui_status.json")
VERSION_PATH = Path("/opt/edgeswarm-node/VERSION")
RELEASE_METADATA_PATH = Path("/opt/edgeswarm-node/RELEASE_METADATA.json")
MODEL_DIR = Path("/var/lib/edgeswarm-node/models")


def get_installed_version():
    version = ""

    try:
        if VERSION_PATH.exists():
            version = VERSION_PATH.read_text(errors="ignore").strip()
    except Exception:
        version = ""

    if not version:
        try:
            metadata = json.loads(RELEASE_METADATA_PATH.read_text())
            version = str(
                metadata.get("appVersion")
                or metadata.get("version")
                or ""
            ).strip()
        except Exception:
            version = ""

    version = version.lstrip("v")
    return f"v{version}" if version else "Unknown"


APP_VERSION = get_installed_version()
CORE_VERSION = APP_VERSION


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


def _ui_normalize_architecture_v1(value):
    value = str(value or "").strip().lower()
    if value in ("x86_64", "amd64", "x64"):
        return "x64"
    if value in ("aarch64", "arm64"):
        return "arm64"
    return value or "unknown"


def _ui_level_for_capability_v1(capability):
    capability = str(capability or "").strip()
    if capability == "Neural-Inference-3B":
        return 2
    if capability in ("Neural-Inference-7B", "Neural-Inference-8B"):
        return 3
    if capability == "Neural-Inference-14B":
        return 4
    if capability in (
        "Neural-Inference-24B",
        "Neural-Inference-27B",
        "Neural-Inference-30B",
    ):
        return 5
    return 1


def detect_model_status():
    fallback_arch = _ui_normalize_architecture_v1(platform.machine())

    if build_linux_neural_readiness is None:
        return {
            "level": "Level 1 Node",
            "edgeLevel": 1,
            "modelId": "none",
            "capability": None,
            "neural": False,
            "architecture": fallback_arch,
            "fallbackModels": [],
        }

    try:
        readiness = build_linux_neural_readiness()
        primary = readiness.get("primaryModelId")
        model_status = readiness.get("modelStatus") or {}
        primary_info = model_status.get(primary) or {} if primary else {}
        capability = primary_info.get("capability")
        level = _ui_level_for_capability_v1(capability)
        profile = readiness.get("hardwareProfile") or {}
        architecture = _ui_normalize_architecture_v1(
            profile.get("architecture") or fallback_arch
        )

        return {
            "level": f"Level {level} Node",
            "edgeLevel": level,
            "modelId": primary or "none",
            "capability": capability,
            "neural": bool(primary and readiness.get("neuralEligible")),
            "architecture": architecture,
            "fallbackModels": readiness.get("fallbackModels") or [],
            "missingRequiredModels": readiness.get("missingRequiredModels") or [],
            "level4Ready": bool(readiness.get("level4Ready")),
            "runtime": readiness.get("runtime"),
            "runtimeAcceleration": readiness.get("runtimeAcceleration"),
        }
    except Exception:
        return {
            "level": "Level 1 Node",
            "edgeLevel": 1,
            "modelId": "none",
            "capability": None,
            "neural": False,
            "architecture": fallback_arch,
            "fallbackModels": [],
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
            f"{str(model.get('level') or 'Level 1 Node').replace(' Node', '')} enabled",
        ),
        "lastSync": status.get("lastLedgerSync", "Not synced"),
    }
