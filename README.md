# EdgeSwarm Linux Node

Headless Linux node for EdgeSwarm deterministic and local neural execution.

Capabilities:
- Exact-Extraction
- Data-Scraper
- Distributed-Compute
- Neural-Inference after local model download, SHA verification, and smoke test

Model setup:
python3 edgeswarm_model_provisioner.py --recommend
python3 edgeswarm_model_provisioner.py --download-recommended
python3 edgeswarm_model_provisioner.py --smoke-recommended

Only enable neural after smoke passes.

Release channel and public-release safety are determined from packaged release metadata and backend manifest verification.
