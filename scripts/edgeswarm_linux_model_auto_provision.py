#!/usr/bin/env python3
import json
import sys
from pathlib import Path

INSTALL_ROOT = Path(__file__).resolve().parent.parent
if str(INSTALL_ROOT) not in sys.path:
    sys.path.insert(0, str(INSTALL_ROOT))

from edgeswarm_linux_neural import get_ready_model_ids
from edgeswarm_model_provisioner import full_setup

STATE_PATH = Path("/var/lib/edgeswarm-node/model_provisioner_status.json")
RESTART_MARKER = Path("/run/edgeswarm-node-model-provisioner/restart-required")


def main() -> int:
    ready_before = sorted(set(get_ready_model_ids()))
    result = full_setup()
    ready_after = sorted(set(get_ready_model_ids()))
    newly_ready = sorted(set(ready_after) - set(ready_before))

    status = {
        "ok": bool(result.get("ok")),
        "readyModelsBefore": ready_before,
        "readyModelsAfter": ready_after,
        "newlyReadyModels": newly_ready,
        "nodeRestartRequired": bool(newly_ready),
        "result": result,
    }

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(status, indent=2) + "\n")

    if newly_ready:
        RESTART_MARKER.parent.mkdir(parents=True, exist_ok=True)
        RESTART_MARKER.write_text(json.dumps({"newlyReadyModels": newly_ready}) + "\n")

    print(json.dumps(status, indent=2))
    return 0 if status["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
