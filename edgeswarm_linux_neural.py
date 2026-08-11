#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
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


QWEN_GENERATIVE_MAX_TOKENS = 384
QWEN_CODE_MAX_TOKENS = 1024
QWEN_JSON_MAX_TOKENS = 640
QWEN_EXACT_MAX_TOKENS = 120


def is_code_generation_prompt_v2(prompt):
    text = str(prompt or "").lower()
    wants_code_only = any(token in text for token in [
        "return code only",
        "code only",
        "no markdown fences",
        "no language label",
    ])
    looks_like_code_task = any(token in text for token in [
        "react",
        "component",
        "javascript",
        "typescript",
        "jsx",
        "tsx",
        "code",
        "function",
        "fetch",
        "api",
        "export default",
    ])
    return wants_code_only and looks_like_code_task


def is_json_response_prompt_v2(prompt):
    text = str(prompt or "").lower()
    return any(token in text for token in [
        "return valid json only",
        "return json only",
        "use keys:",
        "\"summary\"",
        "\"risks\"",
        "\"recommended_fixes\"",
        "\"next_actions\"",
    ])


def linux_generation_settings_v1(prompt, task_mode=None, max_tokens=None):
    mode = str(task_mode or "").strip().lower()
    text = str(prompt or "")

    if mode == "exact_extraction":
        generation_mode = "exact_extraction"
        budget = QWEN_EXACT_MAX_TOKENS
        temperature = 0.0
        top_p = 0.1
    elif is_code_generation_prompt_v2(text):
        generation_mode = "code"
        budget = QWEN_CODE_MAX_TOKENS
        temperature = 0.10
        top_p = 0.85
    elif is_json_response_prompt_v2(text):
        generation_mode = "json"
        budget = QWEN_JSON_MAX_TOKENS
        temperature = 0.05
        top_p = 0.75
    else:
        generation_mode = "general"
        budget = QWEN_GENERATIVE_MAX_TOKENS
        temperature = 0.15
        top_p = 0.8

    if max_tokens is not None:
        try:
            budget = min(budget, max(1, int(max_tokens)))
        except (TypeError, ValueError):
            pass

    if generation_mode == "general" and any(
        phrase in text.lower()
        for phrase in ("one concise sentence", "one sentence", "single sentence")
    ):
        budget = min(budget, 64)

    return {
        "mode": generation_mode,
        "maxTokens": max(1, int(budget)),
        "temperature": temperature,
        "topP": top_p,
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
        stat = model_path.stat()
        recorded_path = data.get("modelPath")
        recorded_hash = str(data.get("modelSha256") or "").strip().lower()
        return bool(
            data.get("smokePassed") is True
            and data.get("smokeVersion") == "linux_model_smoke_v2"
            and recorded_path
            and Path(recorded_path).resolve() == model_path.resolve()
            and recorded_hash
            and int(data.get("modelSizeBytes") or -1) == stat.st_size
            and int(data.get("modelMtimeNs") or -1) == stat.st_mtime_ns
            and int(data.get("modelDevice") or -1) == stat.st_dev
            and int(data.get("modelInode") or -1) == stat.st_ino
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

    required_tier_models = [
        model_id
        for model_id, spec in MODEL_REGISTRY.items()
        if spec.get("required_for_tier") is True
    ]
    missing_required_models = [
        model_id
        for model_id in required_tier_models
        if model_id not in ready
    ]
    primary_model_id = ready[0] if ready else None
    fallback_models = ready[1:] if len(ready) > 1 else []
    level4_ready = bool(required_tier_models) and not missing_required_models

    neural_eligible = bool(caps)
    cuda = bool(profile.get("cudaAvailable"))

    return {
        "neuralReadinessVersion": EDGE_SWARM_LINUX_NEURAL_READINESS_VERSION,
        "hardwareProfile": profile,
        "installedModels": installed,
        "readyModels": ready,
        "primaryModelId": primary_model_id,
        "fallbackModels": fallback_models,
        "requiredTierModels": required_tier_models,
        "missingRequiredModels": missing_required_models,
        "level4Ready": level4_ready,
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


def select_model_for_task_v1(required_model: str, selected_model: Optional[str] = None):
    requested = normalize_selected_model(selected_model)

    if not requested or requested == "tier:auto":
        return select_model_for_required_model(required_model), None

    if requested not in MODEL_REGISTRY:
        return None, "unsupported_selected_model"

    ready = set(get_ready_model_ids())
    if requested not in ready:
        return None, "selected_model_not_ready"

    required = str(required_model or "Neural-Inference").strip()
    capability = str(MODEL_REGISTRY.get(requested, {}).get("capability") or "")

    if required not in ("", "Neural-Inference") and capability != required:
        return None, "selected_model_capability_mismatch"

    return requested, None

def can_handle_linux_neural_task(required_model: str, selected_model: Optional[str] = None) -> Dict[str, Any]:
    selected, selection_error = select_model_for_task_v1(required_model, selected_model)
    readiness = build_linux_neural_readiness()

    if not selected:
        return {
            "ok": False,
            "reason": selection_error or "no_ready_local_model_for_required_model",
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
    n_threads = max(2, min(16, os.cpu_count() or 2))
    n_gpu_layers = -1 if profile.get("cudaAvailable") else 0

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
        model_stat = model_path.stat()
        model_sha256 = _sha256_file(model_path)
        ok = bool(text) and bool(model_sha256)

        marker = {
            "smokePassed": ok,
            "modelId": model_id,
            "capability": spec.get("capability"),
            "modelPath": str(model_path),
            "modelSha256": model_sha256,
            "modelSizeBytes": model_stat.st_size,
            "modelMtimeNs": model_stat.st_mtime_ns,
            "modelDevice": model_stat.st_dev,
            "modelInode": model_stat.st_ino,
            "responsePreview": text[:200],
            "elapsedMs": elapsed_ms,
            "hardwareProfile": profile,
            "timestamp": int(time.time()),
            "smokeVersion": "linux_model_smoke_v2",
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


_LINUX_LLM_CACHE_V1 = {}


def _linux_llm_generate_cached_v1(Llama, selected, model_path, spec, n_threads, cuda_available, messages, max_tokens, temperature, top_p, stop):
    n_ctx = int(spec.get("defaultCtx", 2048))
    accelerations = ["cuda", "cpu"] if cuda_available else ["cpu"]
    last_error = None

    for acceleration in accelerations:
        key = (selected, str(model_path), acceleration, n_ctx, n_threads)
        llm = _LINUX_LLM_CACHE_V1.get(key)
        model_warm = llm is not None
        model_load_ms = 0

        try:
            if llm is None:
                load_started = time.time()
                llm = Llama(
                    model_path=str(model_path),
                    n_ctx=n_ctx,
                    n_threads=n_threads,
                    n_gpu_layers=-1 if acceleration == "cuda" else 0,
                    verbose=False,
                )
                model_load_ms = int((time.time() - load_started) * 1000)
                _LINUX_LLM_CACHE_V1.clear()
                _LINUX_LLM_CACHE_V1[key] = llm

            generation_started = time.time()
            result = llm.create_chat_completion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                stop=stop,
            )
            generation_ms = int((time.time() - generation_started) * 1000)

            return {
                "result": result,
                "runtimeAcceleration": acceleration,
                "modelWarm": model_warm,
                "modelLoadMs": model_load_ms,
                "generationMs": generation_ms,
                "cudaFallbackToCpu": bool(cuda_available and acceleration == "cpu"),
            }
        except Exception as exc:
            last_error = exc
            _LINUX_LLM_CACHE_V1.pop(key, None)
            if acceleration == "cuda":
                continue
            raise

    raise last_error or RuntimeError("linux_neural_generation_failed")

def run_local_linux_neural_inference(
    prompt: str,
    required_model: str = "Neural-Inference",
    max_tokens: Optional[int] = None,
    selected_model: Optional[str] = None,
) -> Dict[str, Any]:
    selected, selection_error = select_model_for_task_v1(required_model, selected_model)

    if not selected:
        return {
            "ok": False,
            "error": selection_error or "no_ready_local_model_for_required_model",
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

    generation_settings = linux_generation_settings_v1(user_text, max_tokens=max_tokens)
    generation_mode = generation_settings["mode"]
    requested_max = generation_settings["maxTokens"]
    temperature = generation_settings["temperature"]
    top_p = generation_settings["topP"]

    started = time.time()

    try:
        generation = _linux_llm_generate_cached_v1(
            Llama,
            selected,
            model_path,
            spec,
            n_threads,
            profile.get("cudaAvailable") is True,
            [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
            requested_max,
            temperature,
            top_p,
            ["<|im_end|>", "<|endoftext|>"],
        )
        result = generation["result"]
        model_load_ms = generation["modelLoadMs"]
        generation_ms = generation["generationMs"]
        model_warm = generation["modelWarm"]
        runtime_acceleration = generation["runtimeAcceleration"]
        cuda_fallback_to_cpu = generation["cudaFallbackToCpu"]

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
            "runtimeAcceleration": runtime_acceleration,
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
            "generationMode": generation_mode,
            "modelWarm": model_warm,
            "cudaFallbackToCpu": cuda_fallback_to_cpu,
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": "local_neural_inference_failed",
            "details": str(exc)[:500],
            "selectedModel": selected,
        }




# EDGE_SWARM_CANONICAL_MODEL_REGISTRY_V1
# Mirrors Windows node v1.5.11 canonical production packs.
MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    "qwen2.5:3b": {
        "capability": "Neural-Inference-3B",
        "tier": 2,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "minRamGb": 8,
        "defaultCtx": 2048,
        "defaultMaxTokens": 256,
        "patterns": ["*Qwen2.5-3B*Q4_K_M*.gguf", "*qwen2.5*3b*q4_k_m*.gguf"],
    },
    "qwen2.5:7b": {
        "capability": "Neural-Inference-7B",
        "tier": 3,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "minRamGb": 16,
        "defaultCtx": 4096,
        "defaultMaxTokens": 384,
        "patterns": ["*Qwen2.5-7B*Q4_K_M*.gguf", "*qwen2.5*7b*q4_k_m*.gguf"],
    },
    "llama3.1:8b": {
        "capability": "Neural-Inference-8B",
        "tier": 3,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "minRamGb": 16,
        "defaultCtx": 4096,
        "defaultMaxTokens": 384,
        "patterns": ["*Llama-3.1-8B*Q4_K_M*.gguf", "*Meta-Llama-3.1-8B*Q4_K_M*.gguf", "*llama*3.1*8b*q4_k_m*.gguf"],
    },
    "qwen2.5:14b": {
        "capability": "Neural-Inference-14B",
        "tier": 4,
        "required_for_tier": True,
        "runtime": "llama.cpp",
        "minRamGb": 32,
        "defaultCtx": 4096,
        "defaultMaxTokens": 512,
        "patterns": ["*Qwen2.5-14B*Q4_K_M*.gguf", "*qwen2.5*14b*q4_k_m*.gguf"],
    },
    "qwen2.5-coder:14b": {
        "capability": "Neural-Inference-14B",
        "tier": 4,
        "required_for_tier": True,
        "runtime": "llama.cpp",
        "minRamGb": 32,
        "defaultCtx": 4096,
        "defaultMaxTokens": 1024,
        "patterns": ["*Qwen2.5-Coder-14B*Q4_K_M*.gguf", "*qwen2.5-coder*14b*q4_k_m*.gguf"],
    },
    "gemma3:27b": {
        "capability": "Neural-Inference-27B",
        "tier": 5,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "minRamGb": 48,
        "defaultCtx": 4096,
        "defaultMaxTokens": 512,
        "patterns": ["*gemma*3*27b*Q4_K_M*.gguf", "*gemma*27b*q4_k_m*.gguf"],
    },
    "mistral-small:24b": {
        "capability": "Neural-Inference-24B",
        "tier": 5,
        "required_for_tier": False,
        "runtime": "llama.cpp",
        "minRamGb": 48,
        "defaultCtx": 4096,
        "defaultMaxTokens": 512,
        "patterns": ["*Mistral-Small-24B*Q4_K_M*.gguf", "*mistral-small*24b*q4_k_m*.gguf"],
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
        "patterns": ["*Qwen*3*30B*Q4_K_M*.gguf", "*qwen3*30b*q4_k_m*.gguf"],
    },
}

CAPABILITY_TO_MODEL_PRIORITY = {
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
}

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



def run_linux_neural_worker_loop_v1() -> int:
    for line in sys.stdin:
        line = str(line or "").strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            result = run_local_linux_neural_inference(
                str(request.get("prompt") or ""),
                str(request.get("requiredModel") or "Neural-Inference"),
                request.get("maxOutputTokens"),
                request.get("selectedModel"),
            )
        except Exception as exc:
            result = {"ok": False, "error": "neural_worker_crash", "details": str(exc)[:500]}
        sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    return 0

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--smoke")
    parser.add_argument("--infer")
    parser.add_argument("--prompt", default="Return only the word READY.")
    parser.add_argument("--prompt-stdin", action="store_true")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--selected-model", default=None)
    parser.add_argument("--worker-loop", action="store_true")
    args = parser.parse_args()

    if args.worker_loop:
        return run_linux_neural_worker_loop_v1()

    if args.smoke:
        print(json.dumps(run_smoke_test(args.smoke), indent=2))
        return 0

    if args.infer:
        inference_prompt = sys.stdin.read() if args.prompt_stdin else args.prompt
        print(json.dumps(run_local_linux_neural_inference(inference_prompt, args.infer, args.max_tokens, args.selected_model), indent=2))
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
