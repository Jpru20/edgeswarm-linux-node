#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "== Python compile check =="
python3 -m py_compile edgeswarm_node.py edgeswarm_linux_neural.py

echo ""
echo "== Linux neural readiness =="
python3 edgeswarm_linux_neural.py --json || python3 edgeswarm_linux_neural.py

echo ""
echo "== Runtime availability =="
python3 - <<'PY'
try:
    import llama_cpp
    print("llama_cpp: installed")
except Exception as exc:
    print("llama_cpp: not installed")
    print(str(exc)[:200])
PY

echo ""
echo "== Active process_task safety =="
python3 - <<'PY'
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
python3 - <<'PY'
import edgeswarm_node
print(edgeswarm_node.get_node_capabilities())
PY

echo ""
echo "== Neural gate simulation =="
python3 - <<'PY'
from edgeswarm_linux_neural import can_handle_linux_neural_task

for required in ["Neural-Inference", "Neural-Inference-3B", "Neural-Inference-7B", "Neural-Inference-14B"]:
    r = can_handle_linux_neural_task(required)
    print(required, "=>", r["ok"], "|", r.get("reason"), "|", r.get("selectedModel"))
PY
