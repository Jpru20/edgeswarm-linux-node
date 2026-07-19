#!/usr/bin/env bash
set -u

INSTALL_DIR="/opt/edgeswarm-node"
SERVICE="edgeswarm-node.service"
PYTHON="$INSTALL_DIR/runtime/bin/edgeswarm-python"
LOGIN_SCRIPT="$INSTALL_DIR/scripts/edgeswarm_linux_login.py"
ENV_FILE="/etc/edgeswarm-node.env"
STATUS_FILE="/var/lib/edgeswarm-node/ui_status.json"
RELEASE_FILE="$INSTALL_DIR/RELEASE_METADATA.json"
VERSION_FILE="$INSTALL_DIR/VERSION"
MODEL_FILE="/var/lib/edgeswarm-node/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf"

run_as_root() {
  if [ "$EUID" -ne 0 ]; then
    exec sudo "$0" "$@"
  fi
}

show_status() {
  local version active enabled main_pid mask release provider model

  version="$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null || true)"
  active="$(systemctl is-active "$SERVICE" 2>/dev/null || true)"
  enabled="$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"
  main_pid="$(systemctl show "$SERVICE" --property=MainPID --value 2>/dev/null || true)"
  mask="$(readlink "/run/systemd/system/$SERVICE" 2>/dev/null || true)"

  release="$(
    sed -n 's/.*"releaseChannel":[[:space:]]*"\([^"]*\)".*/\1/p' \
      "$RELEASE_FILE" 2>/dev/null | head -1
  )"

  provider="$(
    sed -n 's/.*"providerEmail":[[:space:]]*"\([^"]*\)".*/\1/p' \
      "$STATUS_FILE" 2>/dev/null | head -1
  )"

  if [ -f "$MODEL_FILE" ]; then
    model="qwen2.5:7b ready"
  else
    model="deterministic only"
  fi

  [ -n "$version" ] || version="unknown"
  [ -n "$release" ] || release="unknown"
  [ -n "$provider" ] || provider="Not signed in"
  [ -n "$active" ] || active="unknown"
  [ -n "$enabled" ] || enabled="unknown"
  [ -n "$main_pid" ] || main_pid="0"

  echo "EdgeSwarm Linux Node"
  echo "Version: v${version#v}"
  echo "Release: $release"
  echo "Provider: $provider"
  echo "Service: $active"
  echo "Enabled at boot: $enabled"
  echo "Main PID: $main_pid"
  echo "Model: $model"

  if [ "$mask" = "/dev/null" ]; then
    echo "Runtime mask: active"
  else
    echo "Runtime mask: none"
  fi
}

command="${1:-help}"
shift || true

case "$command" in
  login)
    run_as_root login "$@"

    set -a
    [ -f "$ENV_FILE" ] && source "$ENV_FILE"
    set +a

    systemctl unmask --runtime "$SERVICE" >/dev/null 2>&1 || true
    exec "$PYTHON" "$LOGIN_SCRIPT"
    ;;

  start)
    run_as_root start "$@"
    systemctl unmask --runtime "$SERVICE" >/dev/null 2>&1 || true
    systemctl daemon-reload
    systemctl start "$SERVICE"
    echo "Service: $(systemctl is-active "$SERVICE" 2>/dev/null || true)"
    ;;

  stop)
    run_as_root stop "$@"
    systemctl stop "$SERVICE"
    echo "Service: $(systemctl is-active "$SERVICE" 2>/dev/null || true)"
    ;;

  restart)
    run_as_root restart "$@"
    systemctl unmask --runtime "$SERVICE" >/dev/null 2>&1 || true
    systemctl restart "$SERVICE"
    echo "Service: $(systemctl is-active "$SERVICE" 2>/dev/null || true)"
    ;;

  status)
    show_status
    ;;

  logs)
    run_as_root logs "$@"

    if [ "${1:-}" = "--follow" ] || [ "${1:-}" = "-f" ]; then
      exec journalctl -u "$SERVICE" --follow --no-pager -o cat
    fi

    count="${1:-80}"
    exec journalctl -u "$SERVICE" -n "$count" --no-pager -l -o cat
    ;;

  help|-h|--help)
    cat <<'HELP'
Usage:
  edgeswarm login
  edgeswarm start
  edgeswarm stop
  edgeswarm restart
  edgeswarm status
  edgeswarm logs [line-count]
  edgeswarm logs --follow
HELP
    ;;

  *)
    echo "Unknown command: $command" >&2
    echo "Run: edgeswarm help" >&2
    exit 2
    ;;
esac
