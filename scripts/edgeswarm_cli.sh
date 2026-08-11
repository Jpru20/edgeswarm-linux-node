#!/usr/bin/env bash
set -u

INSTALL_DIR="/opt/edgeswarm-node"
SERVICE="edgeswarm-node.service"
PYTHON="$INSTALL_DIR/runtime/bin/edgeswarm-python"
LOGIN_SCRIPT="$INSTALL_DIR/scripts/edgeswarm_linux_login.py"
ENV_FILE="/etc/edgeswarm-node.env"
AUTH_FILE="/etc/edgeswarm-node-auth.json"
WALLET_KEY_FILE="/etc/edgeswarm-node-wallet.key"
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

show_auth_status() {
  local provider auth_version service_state
  provider="$(sed -n 's/.*"providerEmail":[[:space:]]*"\([^"]*\)".*/\1/p' "$AUTH_FILE" 2>/dev/null | head -1)"
  auth_version="$(sed -n 's/.*"authFileVersion":[[:space:]]*"\([^"]*\)".*/\1/p' "$AUTH_FILE" 2>/dev/null | head -1)"
  service_state="$(systemctl is-active "$SERVICE" 2>/dev/null || true)"

  [ -n "$provider" ] || provider="Not signed in"
  [ -n "$auth_version" ] || auth_version="none"
  [ -n "$service_state" ] || service_state="unknown"

  echo "Provider: $provider"
  echo "Auth version: $auth_version"
  if grep -q '"accessToken"[[:space:]]*:[[:space:]]*"[^"]' "$AUTH_FILE" 2>/dev/null; then echo "Access token: present"; else echo "Access token: missing"; fi
  if grep -q '"refreshToken"[[:space:]]*:[[:space:]]*"[^"]' "$AUTH_FILE" 2>/dev/null; then echo "Refresh token: present"; else echo "Refresh token: missing"; fi
  if grep -q '"mfaVerified"[[:space:]]*:[[:space:]]*true' "$AUTH_FILE" 2>/dev/null; then echo "MFA verified: yes"; else echo "MFA verified: no"; fi
  if [ -s "$WALLET_KEY_FILE" ]; then echo "Wallet credential: present"; else echo "Wallet credential: missing"; fi
  echo "Service: $service_state"
}

show_diagnose() {
  local failures auth_mode wallet_mode
  failures=0

  echo "EdgeSwarm Linux diagnostics"
  echo "--------------------------"
  show_auth_status
  echo ""

  if [ -f "$VERSION_FILE" ]; then echo "Version file: ok"; else echo "Version file: missing"; failures=$((failures+1)); fi
  if [ -f "$RELEASE_FILE" ]; then echo "Release metadata: ok"; else echo "Release metadata: missing"; failures=$((failures+1)); fi
  if systemctl cat "$SERVICE" >/dev/null 2>&1; then echo "Systemd unit: installed"; else echo "Systemd unit: missing"; failures=$((failures+1)); fi

  if systemctl cat "$SERVICE" 2>/dev/null | grep -q "LoadCredential=wallet-private-key:"; then
    echo "Wallet credential wiring: ok"
  else
    echo "Wallet credential wiring: missing"
    failures=$((failures+1))
  fi

  auth_mode="$(stat -c "%a" "$AUTH_FILE" 2>/dev/null || true)"
  wallet_mode="$(stat -c "%a" "$WALLET_KEY_FILE" 2>/dev/null || true)"
  [ -n "$auth_mode" ] && echo "Auth file mode: $auth_mode"
  [ -n "$wallet_mode" ] && echo "Wallet credential mode: $wallet_mode"

  if [ -s "$WALLET_KEY_FILE" ] && [ "$wallet_mode" != "600" ]; then
    echo "Wallet credential permissions: WARNING"
    failures=$((failures+1))
  elif [ -s "$WALLET_KEY_FILE" ]; then
    echo "Wallet credential permissions: ok"
  fi

  if [ "$failures" -eq 0 ]; then
    echo "Diagnostic result: PASS"
    return 0
  fi

  echo "Diagnostic result: FAIL ($failures issue(s))"
  return 1
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

  logout)
    run_as_root logout "$@"
    systemctl disable --now "$SERVICE" >/dev/null 2>&1 || true
    rm -f "$AUTH_FILE" "$STATUS_FILE"
    echo "Signed out. Node service stopped and disabled."
    if [ -f "$WALLET_KEY_FILE" ]; then
      echo "Device wallet credential preserved for secure recovery."
    fi
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

  reset-auth)
    run_as_root reset-auth "$@"
    systemctl disable --now "$SERVICE" >/dev/null 2>&1 || true
    rm -f "$AUTH_FILE" "$STATUS_FILE"
    echo "Authentication state reset."
    echo "Node service stopped and disabled."
    if [ -f "$WALLET_KEY_FILE" ]; then
      echo "Device wallet credential preserved."
    else
      echo "Device wallet credential not present."
    fi
    echo "Run edgeswarm login to authenticate again."
    ;;

  auth-status)
    show_auth_status
    ;;

  diagnose)
    show_diagnose
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
  edgeswarm logout
  edgeswarm start
  edgeswarm stop
  edgeswarm restart
  edgeswarm status
  edgeswarm auth-status
  edgeswarm reset-auth
  edgeswarm diagnose
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
