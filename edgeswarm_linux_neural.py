#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


EDGE_SWARM_LINUX_NEURAL_READINESS_VERSION = "linux_neural_readiness_v1"

XDG_DATA_HOME = Path(
    os.getenv(
        "XDG_DATA_HOME",
        Path.home() / ".local" / "share",
    )
)
APP_SUPPORT_DIR = XDG_DATA_HOME / "EdgeSwarm"
MODEL_DIR = Path(
    os.getenv(
        "EDGESWARM_MODEL_DIR",
        "/var/lib/edgeswarm-node/models",
    )
).expanduser()
SMOKE_DIR = APP_SUPPORT_DIR / "model_smoke_tests"

MODEL_DIR.mkdir(parents=True, exist_ok=True)
SMOKE_DIR.mkdir(parents=True, exist_ok=True)


MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "qwen2.5:3b": {
        "modelId": "qwen2.5:3b",
        "capability": "Neural-Inference-3B",
        "tier": 2,
        "patterns": ["qwen2.5*3b*.gguf", "Qwen2.5*3B*.gguf", "*qwen*2.5*3b*.gguf"],
        "minRamGb": 8,
        "defaultCtx": 2048,
        "defaultMaxTokens": 256,
    },
    "qwen2.5:7b": {
        "modelId": "qwen2.5:7b",
        "capability": "Neural-Inference-7B",
        "tier": 3,
        "patterns": ["qwen2.5*7b*.gguf", "Qwen2.5*7B*.gguf", "*qwen*2.5*7b*.gguf"],
        "minRamGb": 16,
        "defaultCtx": 4096,
        "defaultMaxTokens": 384,
    },
    "llama3.1:8b": {
        "modelId": "llama3.1:8b",
        "capability": "Neural-Inference-8B",
        "tier": 3,
        "patterns": ["llama*3.1*8b*.gguf", "Llama*3.1*8B*.gguf", "*llama*8b*.gguf"],
        "minRamGb": 16,
        "defaultCtx": 4096,
        "defaultMaxTokens": 384,
    },
    "qwen2.5:14b": {
        "modelId": "qwen2.5:14b",
        "capability": "Neural-Inference-14B",
        "tier": 4,
        "patterns": ["qwen2.5*14b*.gguf", "Qwen2.5*14B*.gguf", "*qwen*14b*.gguf"],
        "minRamGb": 32,
        "defaultCtx": 4096,
        "defaultMaxTokens": 512,
    },
}

CAPABILITY_TO_MODEL_PRIORITY = {
    "Neural-Inference-3B": ["qwen2.5:3b"],
    "Neural-Inference-7B": ["qwen2.5:7b"],
    "Neural-Inference-8B": ["llama3.1:8b"],
    "Neural-Inference-14B": ["qwen2.5:14b"],
    "Neural-Inference": ["qwen2.5:3b", "qwen2.5:7b", "llama3.1:8b", "qwen2.5:14b"],
}


def _safe_shell(cmd: List[str], timeout: int = 6) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=timeout)
        return out.decode("utf-8", "ignore").strip()
    except Exception:
        return ""


def _read(path: str) -> str:
    try:
        return Path(path).read_text(errors="ignore").strip()
    except Exception:
        return ""


def _parse_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _sha256_file(path: Path, max_bytes: Optional[int] = None) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            if max_bytes:
                h.update(f.read(max_bytes))
            else:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _linux_ram_gb() -> Optional[float]:
    text = _read("/proc/meminfo")
    m = re.search(r"MemTotal:\s+(\d+)\s+kB", text)
    if not m:
        return None
    return round(int(m.group(1)) / (1024 * 1024), 1)


def _linux_cpu_name() -> Optional[str]:
    text = _read("/proc/cpuinfo")
    for line in text.splitlines():
        if "model name" in line:
            return line.split(":", 1)[1].strip()
    return platform.processor() or None


def _linux_device_class() -> Tuple[str, str]:
    chassis = _read("/sys/class/dmi/id/chassis_type")
    laptop_types = {"8", "9", "10", "14", "30", "31", "32"}
    desktop_types = {"3", "4", "5", "6", "7", "13", "15", "16"}

    try:
        has_battery = Path("/sys/class/power_supply").exists() and bool(list(Path("/sys/class/power_supply").glob("BAT*")))
    except Exception:
        has_battery = False

    if chassis in laptop_types or has_battery:
        return "laptop", "laptop-node"

    if chassis in desktop_types:
        return "desktop", "desktop-node"

    return "desktop", "desktop-node"


def _gpu_profile() -> Dict[str, Any]:
    nvidia = _safe_shell(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], timeout=5)
    if nvidia:
        first = nvidia.splitlines()[0]
        parts = [p.strip() for p in first.split(",")]
        return {
            "gpuVendor": "nvidia",
            "gpuName": parts[0] if parts else "NVIDIA GPU",
            "gpuMemoryMb": int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None,
            "cudaAvailable": True,
        }

    lspci = _safe_shell(["lspci"], timeout=5)
    gpu_lines = [
        line for line in lspci.splitlines()
        if any(x in line.lower() for x in ["vga", "3d controller", "display controller"])
    ]

    return {
        "gpuVendor": "unknown",
        "gpuName": gpu_lines[0] if gpu_lines else None,
        "gpuMemoryMb": None,
        "cudaAvailable": False,
    }


def get_linux_hardware_profile() -> Dict[str, Any]:
    device_class, node_type = _linux_device_class()
    gpu = _gpu_profile()

    return {
        "profileVersion": "linux_hardware_profile_v1",
        "osType": "linux",
        "platform": "linux",
        "deviceClass": device_class,
        "nodeType": node_type,
        "architecture": platform.machine() or None,
        "chipFamily": "nvidia_cuda" if gpu.get("cudaAvailable") else "cpu",
        "distro": platform.platform(),
        "hostname": platform.node(),
        "cpuName": _linux_cpu_name(),
        "ramGb": _linux_ram_gb(),
        "gpuName": gpu.get("gpuName"),
        "gpuVendor": gpu.get("gpuVendor"),
        "gpuMemoryMb": gpu.get("gpuMemoryMb"),
        "cudaAvailable": bool(gpu.get("cudaAvailable")),
    }


def find_local_model_file(model_id: str) -> Optional[Path]:
    spec = MODEL_REGISTRY.get(model_id)
    if not spec:
        return None

    candidates: List[Path] = []
    for pattern in spec.get("patterns", []):
        candidates.extend(MODEL_DIR.glob(pattern))

    candidates = [p for p in candidates if p.is_file() and p.suffix.lower() == ".gguf"]
    if not candidates:
        return None

    candidates.sort(key=lambda p: p.stat().st_size, reverse=True)
    return candidates[0]


def smoke_marker_path(model_id: str) -> Path:
    safe = model_id.replace(":", "_").replace("/", "_")
    return SMOKE_DIR / f"{safe}.json"


def model_smoke_passed(model_id: str) -> bool:
    marker = smoke_marker_path(model_id)
    model_path = find_local_model_file(model_id)

    if not marker.exists() or not model_path:
        return False

    try:
        data = json.loads(marker.read_text())
        recorded_path = data.get("modelPath")
        recorded_hash = str(
            data.get("modelSha256Prefix") or ""
        ).strip().lower()
        current_hash = _sha256_file(
            model_path,
            max_bytes=16 * 1024 * 1024,
        )

        return bool(
            data.get("smokePassed") is True
            and recorded_path
            and Path(recorded_path).resolve() == model_path.resolve()
            and recorded_hash
            and current_hash
            and recorded_hash == current_hash.lower()
        )
    except Exception:
        return False


def get_installed_model_ids() -> List[str]:
    return [model_id for model_id in MODEL_REGISTRY if find_local_model_file(model_id)]


def _model_strength_key(model_id: str) -> Tuple[int, int, int, int, str]:
    spec = MODEL_REGISTRY.get(model_id) or {}
    capability = str(spec.get("capability") or "")
    match = re.search(r"(\d+)B", capability, re.IGNORECASE)
    parameter_billions = int(match.group(1)) if match else 0

    try:
        tier = int(spec.get("tier") or 0)
    except Exception:
        tier = 0

    coder_preference = 1 if "coder" in model_id.lower() else 0
    stable_preference = 0 if spec.get("experimental") is True else 1

    return (
        tier,
        parameter_billions,
        coder_preference,
        stable_preference,
        model_id,
    )


def get_ready_model_ids() -> List[str]:
    ready = [
        model_id
        for model_id in get_installed_model_ids()
        if model_smoke_passed(model_id)
    ]

    return sorted(
        ready,
        key=_model_strength_key,
        reverse=True,
    )


def get_linux_neural_capabilities() -> List[str]:
    caps = []

    for model_id in get_ready_model_ids():
        cap = MODEL_REGISTRY.get(model_id, {}).get("capability")
        if cap and cap not in caps:
            caps.append(cap)

    if caps and "Neural-Inference" not in caps:
        caps.insert(0, "Neural-Inference")

    return caps


def _hardware_allows_model(model_id: str, profile: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    profile = profile or get_linux_hardware_profile()
    spec = MODEL_REGISTRY.get(model_id)

    if not spec:
        return False, "unknown_model"

    ram_gb = _parse_float(profile.get("ramGb")) or 0
    min_ram = _parse_float(spec.get("minRamGb")) or 999

    if ram_gb < min_ram:
        return False, f"insufficient_ram_{ram_gb}gb_min_{min_ram}gb"

    return True, "hardware_ok"


def build_linux_neural_readiness() -> Dict[str, Any]:
    profile = get_linux_hardware_profile()
    installed = get_installed_model_ids()
    ready = get_ready_model_ids()
    caps = get_linux_neural_capabilities()

    model_status = {}

    for model_id, spec in MODEL_REGISTRY.items():
        path = find_local_model_file(model_id)
        hardware_ok, hardware_reason = _hardware_allows_model(model_id, profile)

        if not path:
            status = "not_installed"
        elif not hardware_ok:
            status = "installed_but_hardware_not_eligible"
        elif model_smoke_passed(model_id):
            status = "ready"
        else:
            status = "installed_pending_smoke_test"

        model_status[model_id] = {
            "status": status,
            "capability": spec.get("capability"),
            "hardwareReason": hardware_reason,
            "modelPath": str(path) if path else None,
        }

    neural_eligible = bool(caps)
    cuda = bool(profile.get("cudaAvailable"))

    return {
        "neuralReadinessVersion": EDGE_SWARM_LINUX_NEURAL_READINESS_VERSION,
        "hardwareProfile": profile,
        "installedModels": installed,
        "readyModels": ready,
        "neuralCapabilities": caps,
        "neuralEligible": neural_eligible,
        "neuralCapabilityActive": neural_eligible,
        "neuralCapabilityAdvertised": neural_eligible,
        "runtime": "llama.cpp",
        "runtimeAcceleration": "cuda" if cuda else "cpu",
        "modelStatus": model_status,
    }


def select_model_for_required_model(required_model: str) -> Optional[str]:
    required = required_model or "Neural-Inference"

    if required == "Neural-Inference":
        candidates = get_ready_model_ids()
    else:
        candidates = CAPABILITY_TO_MODEL_PRIORITY.get(required)

    if not candidates:
        if required.startswith("Neural-Inference-3B"):
            candidates = ["qwen2.5:3b"]
        elif required.startswith("Neural-Inference-7B"):
            candidates = ["qwen2.5:7b"]
        elif required.startswith("Neural-Inference-8B"):
            candidates = ["llama3.1:8b"]
        elif required.startswith("Neural-Inference-14B"):
            candidates = ["qwen2.5:14b"]
        elif required.startswith("Neural-Inference"):
            candidates = get_ready_model_ids()
        else:
            return None

    ready = set(get_ready_model_ids())
    for model_id in candidates:
        if model_id in ready:
            return model_id

    return None


def can_handle_linux_neural_task(required_model: str) -> Dict[str, Any]:
    selected = select_model_for_required_model(required_model)
    readiness = build_linux_neural_readiness()

    if not selected:
        return {
            "ok": False,
            "reason": "no_ready_local_model_for_required_model",
            "requiredModel": required_model,
            "neuralReadiness": readiness,
        }

    return {
        "ok": True,
        "requiredModel": required_model,
        "selectedModel": selected,
        "capability": MODEL_REGISTRY[selected]["capability"],
        "neuralReadiness": readiness,
    }


def _import_llama_cpp():
    try:
        from llama_cpp import Llama  # type: ignore
        return Llama, None
    except Exception as exc:
        return None, str(exc)


def run_smoke_test(model_id: str) -> Dict[str, Any]:
    profile = get_linux_hardware_profile()
    model_path = find_local_model_file(model_id)

    if not model_path:
        return {
            "ok": False,
            "error": "model_not_installed",
            "modelId": model_id,
            "modelDir": str(MODEL_DIR),
        }

    hardware_ok, hardware_reason = _hardware_allows_model(model_id, profile)
    if not hardware_ok:
        return {
            "ok": False,
            "error": "hardware_not_eligible",
            "reason": hardware_reason,
            "modelId": model_id,
            "modelPath": str(model_path),
            "hardwareProfile": profile,
        }

    Llama, import_error = _import_llama_cpp()
    if Llama is None:
        return {
            "ok": False,
            "error": "llama_cpp_not_installed",
            "details": import_error,
        }

    spec = MODEL_REGISTRY[model_id]
    n_gpu_layers = -1 if profile.get("cudaAvailable") else 0
    n_threads = max(2, min(16, os.cpu_count() or 2))

    started = time.time()

    try:
        llm = Llama(
            model_path=str(model_path),
            n_ctx=min(int(spec.get("defaultCtx", 2048)), 2048),
            n_threads=n_threads,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )

        result = llm("Return only the word READY.", max_tokens=8, temperature=0, stop=["\n"])

        try:
            text = result["choices"][0]["text"].strip()
        except Exception:
            text = str(result)[:200]

        elapsed_ms = int((time.time() - started) * 1000)
        ok = bool(text)

        marker = {
            "smokePassed": ok,
            "modelId": model_id,
            "capability": spec.get("capability"),
            "modelPath": str(model_path),
            "modelSha256Prefix": _sha256_file(model_path, max_bytes=16 * 1024 * 1024),
            "responsePreview": text[:200],
            "elapsedMs": elapsed_ms,
            "hardwareProfile": profile,
            "timestamp": int(time.time()),
            "smokeVersion": "linux_model_smoke_v1",
        }

        if ok:
            smoke_marker_path(model_id).write_text(json.dumps(marker, indent=2))

        return {"ok": ok, **marker}

    except Exception as exc:
        return {
            "ok": False,
            "error": "smoke_test_failed",
            "details": str(exc)[:500],
            "modelId": model_id,
            "modelPath": str(model_path),
        }


def run_local_linux_neural_inference(
    prompt: str,
    required_model: str = "Neural-Inference",
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    selected = select_model_for_required_model(required_model)

    if not selected:
        return {
            "ok": False,
            "error": "no_ready_local_model_for_required_model",
            "requiredModel": required_model,
            "neuralReadiness": build_linux_neural_readiness(),
        }

    model_path = find_local_model_file(selected)

    if not model_path:
        return {
            "ok": False,
            "error": "selected_model_file_missing",
            "selectedModel": selected,
        }

    Llama, import_error = _import_llama_cpp()

    if Llama is None:
        return {
            "ok": False,
            "error": "llama_cpp_not_installed",
            "details": import_error,
        }

    profile = get_linux_hardware_profile()
    spec = MODEL_REGISTRY[selected]

    n_gpu_layers = -1 if profile.get("cudaAvailable") else 0
    n_threads = max(2, min(16, os.cpu_count() or 2))

    raw_prompt = str(prompt or "").strip()
    system_text = (
        "Answer the user directly and follow their requested "
        "format and length."
    )
    user_text = raw_prompt

    prefix = "prompt://SYSTEM:"
    user_marker = "\n\nUSER:"
    strict_marker = "\n\n[STRICT_PLAIN_TEXT_MODE_V3]"

    if raw_prompt.startswith(prefix):
        compiled = raw_prompt[len(prefix):].strip()

        if user_marker in compiled:
            system_text, user_text = compiled.split(
                user_marker,
                1,
            )

            system_text = system_text.strip()
            user_text = user_text.strip()

            if strict_marker in user_text:
                user_text = user_text.split(
                    strict_marker,
                    1,
                )[0].strip()

    requested_max = int(
        max_tokens
        or spec.get("defaultMaxTokens", 256)
    )

    concise_request = any(
        phrase in user_text.lower()
        for phrase in (
            "one concise sentence",
            "one sentence",
            "single sentence",
        )
    )

    if concise_request:
        requested_max = min(requested_max, 64)
    else:
        requested_max = min(requested_max, 256)

    requested_max = max(16, requested_max)

    started = time.time()

    try:
        load_started = time.time()

        llm = Llama(
            model_path=str(model_path),
            n_ctx=int(spec.get("defaultCtx", 2048)),
            n_threads=n_threads,
            n_gpu_layers=n_gpu_layers,
            verbose=False,
        )

        model_load_ms = int(
            (time.time() - load_started) * 1000
        )

        generation_started = time.time()

        result = llm.create_chat_completion(
            messages=[
                {
                    "role": "system",
                    "content": system_text,
                },
                {
                    "role": "user",
                    "content": user_text,
                },
            ],
            max_tokens=requested_max,
            temperature=0.1,
            top_p=0.9,
            stop=[
                "<|im_end|>",
                "<|endoftext|>",
            ],
        )

        generation_ms = int(
            (time.time() - generation_started) * 1000
        )

        try:
            response_text = str(
                result["choices"][0]["message"]["content"]
                or ""
            ).strip()
        except Exception:
            response_text = str(result).strip()

        usage = (
            result.get("usage")
            if isinstance(result, dict)
            else {}
        ) or {}

        input_tokens = int(
            usage.get("prompt_tokens") or 0
        )

        output_tokens = int(
            usage.get("completion_tokens") or 0
        )

        tokens_per_second = None

        if output_tokens > 0 and generation_ms > 0:
            tokens_per_second = round(
                output_tokens / (generation_ms / 1000),
                3,
            )

        return {
            "ok": True,
            "response": response_text,
            "selectedModel": selected,
            "selectedCapability": spec.get("capability"),
            "requiredModel": required_model,
            "modelPath": str(model_path),
            "runtime": "llama.cpp",
            "runtimeAcceleration": (
                "cuda"
                if profile.get("cudaAvailable")
                else "cpu"
            ),
            "latencyMs": int(
                (time.time() - started) * 1000
            ),
            "modelLoadMs": model_load_ms,
            "generationMs": generation_ms,
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "tokensGenerated": output_tokens,
            "tokensPerSecond": tokens_per_second,
            "maxTokens": requested_max,
            "modelWarm": False,
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": "local_neural_inference_failed",
            "details": str(exc)[:500],
            "selectedModel": selected,
        }




# EDGE_SWARM_WINDOWS_COMPAT_MODEL_REGISTRY_V1
# Mirrors Windows node v1.5.11 canonical production packs.
DEFAULT_NEURAL_MODEL_ID = "qwen2.5:14b"

LEVEL_4_REQUIRED_MODEL_IDS = [
    "qwen2.5-coder:14b",
    "qwen2.5:14b",
]

WINDOWS_COMPAT_MODEL_REGISTRY = {
    "qwen2.5:3b": {
        "capability": "Neural-Inference-3B",
        "tier": 2,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "minRamGb": 8,
        "defaultCtx": 2048,
        "defaultMaxTokens": 256,
        "filename_patterns": ["*Qwen2.5-3B*Q4_K_M*.gguf", "*qwen2.5*3b*q4_k_m*.gguf"],
    },
    "qwen2.5:7b": {
        "capability": "Neural-Inference-7B",
        "tier": 3,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "minRamGb": 16,
        "defaultCtx": 4096,
        "defaultMaxTokens": 384,
        "filename_patterns": ["*Qwen2.5-7B*Q4_K_M*.gguf", "*qwen2.5*7b*q4_k_m*.gguf"],
    },
    "llama3.1:8b": {
        "capability": "Neural-Inference-8B",
        "tier": 3,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "minRamGb": 16,
        "defaultCtx": 4096,
        "defaultMaxTokens": 384,
        "filename_patterns": ["*Llama-3.1-8B*Q4_K_M*.gguf", "*Meta-Llama-3.1-8B*Q4_K_M*.gguf", "*llama*3.1*8b*q4_k_m*.gguf"],
    },
    "qwen2.5:14b": {
        "capability": "Neural-Inference-14B",
        "tier": 4,
        "required_for_tier": True,
        "runtime": "llama.cpp",
        "minRamGb": 32,
        "defaultCtx": 4096,
        "defaultMaxTokens": 512,
        "filename_patterns": ["*Qwen2.5-14B*Q4_K_M*.gguf", "*qwen2.5*14b*q4_k_m*.gguf"],
    },
    "qwen2.5-coder:14b": {
        "capability": "Neural-Inference-14B",
        "tier": 4,
        "required_for_tier": True,
        "runtime": "llama.cpp",
        "minRamGb": 32,
        "defaultCtx": 4096,
        "defaultMaxTokens": 1024,
        "filename_patterns": ["*Qwen2.5-Coder-14B*Q4_K_M*.gguf", "*qwen2.5-coder*14b*q4_k_m*.gguf"],
    },
    "gemma3:27b": {
        "capability": "Neural-Inference-27B",
        "tier": 5,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "minRamGb": 48,
        "defaultCtx": 4096,
        "defaultMaxTokens": 512,
        "filename_patterns": ["*gemma*3*27b*Q4_K_M*.gguf", "*gemma*27b*q4_k_m*.gguf"],
    },
    "mistral-small:24b": {
        "capability": "Neural-Inference-24B",
        "tier": 5,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "minRamGb": 48,
        "defaultCtx": 4096,
        "defaultMaxTokens": 512,
        "filename_patterns": ["*Mistral-Small-24B*Q4_K_M*.gguf", "*mistral-small*24b*q4_k_m*.gguf"],
    },
    "qwen3:30b": {
        "capability": "Neural-Inference-30B",
        "tier": 5,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "experimental": True,
        "minRamGb": 64,
        "defaultCtx": 4096,
        "defaultMaxTokens": 512,
        "filename_patterns": ["*Qwen*3*30B*Q4_K_M*.gguf", "*qwen3*30b*q4_k_m*.gguf"],
    },
}

MODEL_REGISTRY.clear()
for _model_id, _spec in WINDOWS_COMPAT_MODEL_REGISTRY.items():
    _copy = dict(_spec)
    _copy["patterns"] = list(_spec.get("filename_patterns") or [])
    MODEL_REGISTRY[_model_id] = _copy

CAPABILITY_TO_MODEL_PRIORITY.clear()
CAPABILITY_TO_MODEL_PRIORITY.update({
    "Neural-Inference-3B": ["qwen2.5:3b"],
    "Neural-Inference-7B": ["qwen2.5:7b"],
    "Neural-Inference-8B": ["llama3.1:8b"],
    "Neural-Inference-14B": ["qwen2.5-coder:14b", "qwen2.5:14b"],
    "Neural-Inference-24B": ["mistral-small:24b"],
    "Neural-Inference-27B": ["gemma3:27b"],
    "Neural-Inference-30B": ["qwen3:30b"],
    "Neural-Inference": [
        "qwen2.5-coder:14b",
        "qwen2.5:14b",
        "gemma3:27b",
        "mistral-small:24b",
        "qwen3:30b",
        "llama3.1:8b",
        "qwen2.5:7b",
        "qwen2.5:3b",
    ],
})

def normalize_selected_model(model_id):
    model_id = str(model_id or "").strip()
    if not model_id:
        return ""

    key = model_id.lower().replace("_", "-")
    aliases = {
        "qwen-coder-14b": "qwen2.5-coder:14b",
        "qwen2.5-coder-14b": "qwen2.5-coder:14b",
        "qwen25-coder-14b": "qwen2.5-coder:14b",
        "qwen-14b": "qwen2.5:14b",
        "qwen2.5-14b": "qwen2.5:14b",
        "qwen25-14b": "qwen2.5:14b",
        "qwen2.5-14b-instruct-q4-k-m": "qwen2.5:14b",
        "qwen2.5-14b-instruct-q4_k_m": "qwen2.5:14b",
        "qwen2.5-7b": "qwen2.5:7b",
        "qwen25-7b": "qwen2.5:7b",
        "qwen2.5-7b-instruct-q4-k-m": "qwen2.5:7b",
        "qwen2.5-7b-instruct-q4_k_m": "qwen2.5:7b",
        "qwen2.5-3b": "qwen2.5:3b",
        "qwen25-3b": "qwen2.5:3b",
        "llama3.1-8b": "llama3.1:8b",
        "llama-3.1-8b": "llama3.1:8b",
        "meta-llama-3.1-8b": "llama3.1:8b",
        "gemma-27b": "gemma3:27b",
        "gemma3-27b": "gemma3:27b",
        "gemma-3-27b": "gemma3:27b",
        "mistral-small-24b": "mistral-small:24b",
        "mistral-24b": "mistral-small:24b",
        "qwen3-30b": "qwen3:30b",
        "qwen3-30b-a3b": "qwen3:30b",
        "qwen3-30b-a3b-instruct-q4-k-m": "qwen3:30b",
    }
    return aliases.get(key, model_id)



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--smoke")
    parser.add_argument("--infer")
    parser.add_argument("--prompt", default="Return only the word READY.")
    parser.add_argument("--max-tokens", type=int, default=None)
    args = parser.parse_args()

    if args.smoke:
        print(json.dumps(run_smoke_test(args.smoke), indent=2))
        return 0

    if args.infer:
        print(json.dumps(run_local_linux_neural_inference(args.prompt, args.infer, args.max_tokens), indent=2))
        return 0

    readiness = build_linux_neural_readiness()

    if args.list_models:
        print("Model directory:", MODEL_DIR)
        print(json.dumps(readiness["modelStatus"], indent=2))
        return 0

    print(json.dumps(readiness, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
