#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

from edgeswarm_linux_neural import model_smoke_passed

API_BASE = os.getenv("GCP_BASE_URL", "https://api.edgeswarm.io").rstrip("/")
MODEL_DIR = Path(
    os.getenv(
        "EDGESWARM_MODEL_DIR",
        "/var/lib/edgeswarm-node/models",
    )
).expanduser()
VERIFICATION_CACHE_PATH = (
    MODEL_DIR / ".edgeswarm_model_verification_cache.json"
)


def _float_or_none(value):
    try:
        return round(float(value), 2)
    except Exception:
        return None


def get_ram_gb():
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return round((pages * page_size) / (1024 ** 3), 1)
        except Exception:
            return None


def get_disk_free_gb(path=None):
    path = path or str(MODEL_DIR)
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(path)
        return round(usage.free / (1024 ** 3), 1)
    except Exception:
        return None


def get_cpu_name():
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return platform.processor() or None


def detect_cuda_available():
    try:
        result = subprocess.run(
            ["bash", "-lc", "command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def build_recommendation_payload():
    cuda_available = detect_cuda_available()

    return {
        "nodeType": "desktop-node",
        "platform": "linux",
        "ramGb": get_ram_gb(),
        "cpuCores": os.cpu_count() or 0,
        "diskFreeGb": get_disk_free_gb(),
        "gpuVendor": "nvidia" if cuda_available else "unknown",
        "gpuName": "nvidia" if cuda_available else None,
        "gpuMemoryMb": None,
        "cudaAvailable": cuda_available,
        "metalAvailable": False,
        "architecture": platform.machine() or "x86_64",
        "chipFamily": "cuda" if cuda_available else "cpu",
        "cpuName": get_cpu_name(),
    }


def api_get(path):
    url = f"{API_BASE}{path}"
    res = requests.get(url, timeout=45)
    res.raise_for_status()
    return res.json()


def api_post(path, payload):
    url = f"{API_BASE}{path}"
    res = requests.post(url, json=payload, timeout=60)
    res.raise_for_status()
    return res.json()


def fetch_manifest():
    return api_get("/node/model-manifest")


def fetch_recommendation():
    payload = build_recommendation_payload()
    data = api_post("/node/model-recommendation", payload)
    return {"payload": payload, "recommendation": data}


def select_recommended_artifact(recommended_model):
    model = recommended_model or {}

    files = [
        dict(item)
        for item in (model.get("files") or [])
        if isinstance(item, dict)
    ]

    if not files:
        download_url = model.get("downloadUrl")
        filename = (
            model.get("filename")
            or os.path.basename(str(download_url or ""))
        )

        if download_url and filename:
            files = [{
                "modelId": model.get("id"),
                "filename": filename,
                "downloadUrl": download_url,
                "sha256": model.get("sha256"),
                "sizeGb": model.get("sizeGb"),
            }]

    if not files:
        return {
            "modelId": model.get("id"),
            "capability": model.get("capability"),
            "files": [],
        }

    preferred_ids = []

    if not model.get("pack") and model.get("id"):
        preferred_ids.append(str(model.get("id")))

    for key in (
        "primaryModelIds",
        "speedAltModelIds",
        "fallbackModelIds",
    ):
        values = model.get(key)

        if isinstance(values, list):
            preferred_ids.extend(
                str(value)
                for value in values
                if value
            )

    selected = None

    for preferred_id in preferred_ids:
        selected = next(
            (
                item
                for item in files
                if str(item.get("modelId") or "")
                == preferred_id
            ),
            None,
        )

        if selected:
            break

    if selected is None:
        selected = files[0]

    return {
        "modelId": (
            selected.get("modelId")
            or model.get("id")
        ),
        "capability": model.get("capability"),
        "files": [selected],
    }


def recommended_model_files(recommended_model):
    return select_recommended_artifact(
        recommended_model
    ).get("files", [])



def sha256_file(path):
    digest = hashlib.sha256()
    path = Path(path)

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest().lower()


def _load_verification_cache():
    try:
        data = json.loads(
            VERIFICATION_CACHE_PATH.read_text(encoding="utf-8")
        )
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_verification_cache(cache):
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        temp_path = VERIFICATION_CACHE_PATH.with_name(
            VERIFICATION_CACHE_PATH.name + ".tmp"
        )
        temp_path.write_text(
            json.dumps(cache, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temp_path, VERIFICATION_CACHE_PATH)
    except Exception:
        pass


def _forget_cached_verification(path):
    cache = _load_verification_cache()
    key = str(Path(path).resolve())

    if key in cache:
        del cache[key]
        _save_verification_cache(cache)


def _cached_verification(path, expected_sha256):
    path = Path(path)
    expected = str(expected_sha256 or "").strip().lower()

    if not expected:
        return None

    try:
        stat = path.stat()
    except Exception:
        return None

    entry = _load_verification_cache().get(
        str(path.resolve())
    )

    if not isinstance(entry, dict):
        return None

    if not (
        entry.get("expectedSha256") == expected
        and entry.get("actualSha256") == expected
        and entry.get("sizeBytes") == stat.st_size
        and entry.get("mtimeNs") == stat.st_mtime_ns
        and entry.get("device") == stat.st_dev
        and entry.get("inode") == stat.st_ino
    ):
        return None

    return {
        "ok": True,
        "path": str(path),
        "actualSha256": expected,
        "expectedSha256": expected,
        "sizeBytes": stat.st_size,
        "cached": True,
    }


def _remember_verified_file(path, expected_sha256, actual_sha256):
    path = Path(path)
    expected = str(expected_sha256 or "").strip().lower()
    actual = str(actual_sha256 or "").strip().lower()

    if not expected or expected != actual:
        return

    try:
        stat = path.stat()
    except Exception:
        return

    cache = _load_verification_cache()
    cache[str(path.resolve())] = {
        "expectedSha256": expected,
        "actualSha256": actual,
        "sizeBytes": stat.st_size,
        "mtimeNs": stat.st_mtime_ns,
        "device": stat.st_dev,
        "inode": stat.st_ino,
    }
    _save_verification_cache(cache)


def verify_file(path, expected_sha256):
    path = Path(path)
    expected = str(expected_sha256 or "").strip().lower()

    if not path.exists():
        _forget_cached_verification(path)
        return {
            "ok": False,
            "path": str(path),
            "reason": "missing",
        }

    cached = _cached_verification(path, expected)

    if cached is not None:
        return cached

    actual = sha256_file(path)
    stat = path.stat()

    if expected and actual != expected:
        _forget_cached_verification(path)
        return {
            "ok": False,
            "path": str(path),
            "reason": "sha256_mismatch",
            "expectedSha256": expected,
            "actualSha256": actual,
            "sizeBytes": stat.st_size,
        }

    _remember_verified_file(path, expected, actual)

    return {
        "ok": True,
        "path": str(path),
        "actualSha256": actual,
        "expectedSha256": expected or None,
        "sizeBytes": stat.st_size,
        "cached": False,
    }


def verify_recommended(recommendation_data=None):
    if recommendation_data is None:
        recommendation_data = fetch_recommendation()["recommendation"]
    recommendation = recommendation_data
    model = recommendation.get("recommendedModel") or {}
    selection = select_recommended_artifact(model)
    files = selection.get("files") or []

    checks = []

    for item in files:
        filename = os.path.basename(
            str(item.get("filename") or "")
        )
        checks.append(
            verify_file(
                MODEL_DIR / filename,
                item.get("sha256"),
            )
        )

    return {
        "ok": (
            bool(checks)
            and all(check.get("ok") for check in checks)
        ),
        "modelId": selection.get("modelId"),
        "capability": selection.get("capability"),
        "shouldDownload": recommendation.get(
            "shouldDownload"
        ),
        "files": checks,
        "recommendationReason": (
            recommendation.get("nodeProfile", {})
            .get("recommendationReason")
        ),
    }



def download_file(url, final_path, expected_sha256=None):
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    final_path = Path(final_path)
    temp_path = Path(str(final_path) + ".download")

    existing = verify_file(final_path, expected_sha256)
    if existing.get("ok"):
        print(f"Already verified: {final_path}")
        return existing

    max_attempts = 5
    chunk_size = 1024 * 1024
    last_error = None

    for attempt in range(1, max_attempts + 1):
        resume_from = temp_path.stat().st_size if temp_path.exists() else 0
        headers = {}

        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"

        try:
            print(f"Downloading {final_path.name} attempt {attempt}/{max_attempts} resume={resume_from} bytes")

            with requests.get(url, headers=headers, stream=True, timeout=(20, 180)) as res:
                if res.status_code == 416:
                    temp_path.unlink(missing_ok=True)
                    raise RuntimeError("server_rejected_resume_range")

                if resume_from > 0 and res.status_code == 200:
                    temp_path.unlink(missing_ok=True)
                    resume_from = 0

                if res.status_code not in (200, 206):
                    raise RuntimeError(f"http_{res.status_code}")

                content_length = int(res.headers.get("Content-Length") or 0)
                total = resume_from + content_length if content_length else None

                mode = "ab" if resume_from else "wb"
                downloaded = resume_from
                last_print = time.time()

                with temp_path.open(mode) as f:
                    for chunk in res.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue

                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        if now - last_print >= 5:
                            if total:
                                pct = round((downloaded / total) * 100, 1)
                                print(f"Progress: {pct}%")
                            else:
                                gb = round(downloaded / (1024 ** 3), 2)
                                print(f"Progress: {gb}GB")
                            last_print = now

            check = verify_file(temp_path, expected_sha256)
            if not check.get("ok"):
                temp_path.unlink(missing_ok=True)
                raise RuntimeError(json.dumps(check))

            os.replace(str(temp_path), str(final_path))
            return verify_file(final_path, expected_sha256)

        except Exception as exc:
            last_error = str(exc)
            print(f"Attempt failed: {last_error}")

            if attempt < max_attempts:
                time.sleep(min(10 * attempt, 45))

    raise RuntimeError(f"download_failed:{final_path.name}:{last_error}")


def download_recommended(recommendation_data=None):
    if recommendation_data is None:
        recommendation_data = fetch_recommendation()["recommendation"]
    recommendation = recommendation_data
    model = recommendation.get("recommendedModel") or {}
    selection = select_recommended_artifact(model)
    files = selection.get("files") or []

    if not files:
        return {
            "ok": False,
            "error": "no_downloadable_files",
            "recommendation": recommendation,
        }

    results = []

    for item in files:
        filename = os.path.basename(
            str(item.get("filename") or "")
        )
        url = item.get("downloadUrl")
        sha = item.get("sha256")

        if not filename or not url:
            raise RuntimeError(
                f"bad_file_entry:{item}"
            )

        results.append(
            download_file(
                url,
                MODEL_DIR / filename,
                sha,
            )
        )

    return {
        "ok": all(result.get("ok") for result in results),
        "modelId": selection.get("modelId"),
        "capability": selection.get("capability"),
        "files": results,
    }



def _extract_json_from_output(output):
    text = str(output or "").strip()
    idx = text.find("{")
    if idx < 0:
        raise RuntimeError(f"no_json_in_output:{text[:500]}")
    return json.loads(text[idx:])


def smoke_model(model_id):
    cmd = [sys.executable, "edgeswarm_linux_neural.py", "--smoke", model_id]
    res = subprocess.run(cmd, cwd=str(Path(__file__).resolve().parent), capture_output=True, text=True, timeout=900)

    output = (res.stdout or "") + "\n" + (res.stderr or "")

    if res.returncode != 0:
        return {
            "ok": False,
            "error": "smoke_command_failed",
            "returncode": res.returncode,
            "output": output[-2000:],
        }

    return _extract_json_from_output(output)


def smoke_recommended(recommendation_data=None):
    if recommendation_data is None:
        recommendation_data = (
            fetch_recommendation().get("recommendation")
            or {}
        )
    model = recommendation_data.get("recommendedModel") or {}
    selection = select_recommended_artifact(model)
    model_id = selection.get("modelId")

    if not model_id:
        return {
            "ok": False,
            "error": "no_recommended_model_id",
            "recommendation": recommendation_data,
        }

    result = smoke_model(model_id)

    return {
        "ok": (
            bool(result.get("ok"))
            and bool(result.get("smokePassed"))
        ),
        "modelId": model_id,
        "smoke": result,
    }



def full_setup():
    recommendation = fetch_recommendation()
    recommendation_data = recommendation.get("recommendation") or {}
    recommended_model = recommendation_data.get("recommendedModel") or {}
    selection = select_recommended_artifact(recommended_model)
    model_id = selection.get("modelId")
    should_download = bool(recommendation_data.get("shouldDownload"))

    if not should_download:
        return {
            "ok": True,
            "skipped": True,
            "reason": "backend_download_not_requested",
            "shouldDownload": False,
            "modelId": model_id,
            "recommendation": recommendation,
        }

    verify_before = verify_recommended(recommendation_data)

    if verify_before.get("ok"):
        download = {
            "ok": True,
            "skipped": True,
            "reason": "already_verified",
            "modelId": model_id,
            "files": verify_before.get("files") or [],
        }
    else:
        download = download_recommended(recommendation_data)

    verify = verify_recommended(recommendation_data)

    if not verify.get("ok"):
        smoke = {
            "ok": False,
            "skipped": True,
            "reason": "artifact_not_verified",
        }
    elif model_id and model_smoke_passed(model_id):
        smoke = {
            "ok": True,
            "skipped": True,
            "reason": "already_smoke_verified",
            "modelId": model_id,
        }
    else:
        smoke = smoke_recommended(recommendation_data)

    return {
        "ok": (
            bool(download.get("ok"))
            and bool(verify.get("ok"))
            and bool(smoke.get("ok"))
        ),
        "shouldDownload": True,
        "modelId": model_id,
        "recommendation": recommendation,
        "verifyBefore": verify_before,
        "download": download,
        "verify": verify,
        "smoke": smoke,
    }



def main():
    parser = argparse.ArgumentParser(description="EdgeSwarm Linux model provisioner")
    parser.add_argument("--manifest", action="store_true")
    parser.add_argument("--recommend", action="store_true")
    parser.add_argument("--verify-recommended", action="store_true")
    parser.add_argument("--download-recommended", action="store_true")
    parser.add_argument("--smoke-recommended", action="store_true")
    parser.add_argument("--full-setup", action="store_true")
    parser.add_argument("--smoke", metavar="MODEL_ID")
    args = parser.parse_args()

    if args.manifest:
        print(json.dumps(fetch_manifest(), indent=2))
        return 0

    if args.recommend:
        print(json.dumps(fetch_recommendation(), indent=2))
        return 0

    if args.verify_recommended:
        result = verify_recommended()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.download_recommended:
        result = download_recommended()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.smoke_recommended:
        result = smoke_recommended()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.full_setup:
        result = full_setup()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.smoke:
        result = smoke_model(args.smoke)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") and result.get("smokePassed") else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
