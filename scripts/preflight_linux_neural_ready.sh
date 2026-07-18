#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${EDGESWARM_PYTHON:-$ROOT/.venv/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
  echo "EdgeSwarm Python runtime missing: $PYTHON" >&2
  exit 1
fi

echo "== Python compile check =="
"$PYTHON" -m py_compile edgeswarm_node.py edgeswarm_linux_neural.py

echo ""
echo "== Linux neural readiness =="
"$PYTHON" edgeswarm_linux_neural.py --json || "$PYTHON" edgeswarm_linux_neural.py

echo ""
echo "== Runtime availability =="
"$PYTHON" - <<'PY'
try:
    import llama_cpp
    print("llama_cpp: installed")
except Exception as exc:
    print("llama_cpp: not installed")
    print(str(exc)[:200])
PY

echo ""
echo "== Active process_task safety =="
"$PYTHON" - <<'PY'
import inspect
import edgeswarm_node

source, start = inspect.getsourcelines(edgeswarm_node.process_task)
joined = "".join(source)

print("active process_task line:", start)
print("linux neural gate:", "EDGE_SWARM_LINUX_NEURAL_PROCESS_TASK_GATE_V1" in joined)
print("neural helper present:", hasattr(edgeswarm_node, "process_linux_neural_task_v1"))
PY

echo ""
echo "== Capabilities with neural disabled =="
"$PYTHON" - <<'PY'
import edgeswarm_node
print(edgeswarm_node.get_node_capabilities())
PY

echo ""
echo "== Neural gate simulation =="
"$PYTHON" - <<'PY'
from edgeswarm_linux_neural import can_handle_linux_neural_task

for required in ["Neural-Inference", "Neural-Inference-3B", "Neural-Inference-7B", "Neural-Inference-14B"]:
    r = can_handle_linux_neural_task(required)
    print(required, "=>", r["ok"], "|", r.get("reason"), "|", r.get("selectedModel"))
PY
