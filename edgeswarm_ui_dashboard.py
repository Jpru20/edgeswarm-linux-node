#!/usr/bin/env python3
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import requests

from edgeswarm_ui_common import (
    API_BASE,
    APP_VERSION,
    AUTH_PATH,
    CORE_VERSION,
    RELEASE_METADATA_PATH,
    SERVICE_NAME,
    STATUS_PATH,
    detect_model_status,
    get_hardware_id,
    get_latest_logs,
    get_ledger_defaults,
    get_provider_email,
    pkexec,
    read_json,
    run_cmd,
    service_active,
    short_middle,
)

USER_CACHE_PATH = Path.home() / ".config" / "edgeswarm" / "ui_cache.json"


def read_user_cache():
    try:
        if USER_CACHE_PATH.exists():
            return json.loads(USER_CACHE_PATH.read_text())
    except Exception:
        pass
    return {}


def write_user_cache(patch: dict):
    try:
        USER_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        current = read_user_cache()
        current.update(patch)
        USER_CACHE_PATH.write_text(json.dumps(current, indent=2))
        return current
    except Exception:
        return patch


class DashboardFrame(ctk.CTkFrame):
    def __init__(self, master, on_sign_out):
        super().__init__(master, fg_color="#202020")

        self.on_sign_out = on_sign_out

        self.left_panel = None
        self.right_panel = None
        self.ledger_values = None
        self.log_box = None
        self.online_label = None
        self.level_badge = None

        self.build()
        self.refresh_dashboard()
        self.after(10000, self.auto_refresh)

    def build(self):
        root = ctk.CTkFrame(self, fg_color="#202020")
        root.pack(fill="both", expand=True, padx=24, pady=24)

        top = ctk.CTkFrame(root, fg_color="transparent")
        top.pack(fill="x")

        ctk.CTkLabel(
            top,
            text="EdgeSwarm Node",
            font=ctk.CTkFont(size=34, weight="bold"),
            text_color="#EAEFF7",
        ).pack(side="left")

        self.level_badge = ctk.CTkLabel(
            top,
            text="Level 3 Node",
            fg_color="#071A3A",
            corner_radius=14,
            width=148,
            height=34,
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self.level_badge.pack(side="right", padx=(12, 0))

        self.online_label = ctk.CTkLabel(
            top,
            text="Checking",
            text_color="#F5C542",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        self.online_label.pack(side="right", padx=10)

        panels = ctk.CTkFrame(root, fg_color="transparent")
        panels.pack(fill="x", pady=(22, 16))

        self.left_panel = ctk.CTkFrame(panels, fg_color="#2A2A2A", corner_radius=8)
        self.left_panel.pack(side="left", fill="both", expand=True, padx=(0, 10), ipady=10)

        self.right_panel = ctk.CTkFrame(panels, fg_color="#2A2A2A", corner_radius=8)
        self.right_panel.pack(side="left", fill="both", expand=True, padx=(10, 0), ipady=10)

        buttons = ctk.CTkFrame(root, fg_color="transparent")
        buttons.pack(fill="x", pady=(0, 16))

        ctk.CTkButton(
            buttons,
            text="Start Node",
            width=150,
            height=42,
            fg_color="#16C083",
            hover_color="#13A973",
            command=lambda: self.service_action("start"),
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            buttons,
            text="Stop Node",
            width=150,
            height=42,
            fg_color="#E51E25",
            hover_color="#BE171D",
            command=lambda: self.service_action("stop"),
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            buttons,
            text="Check Updates",
            width=165,
            height=42,
            fg_color="#0A1933",
            hover_color="#10264B",
            command=self.check_updates,
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            buttons,
            text="Sync Ledger Data",
            width=185,
            height=42,
            fg_color="#0A1933",
            hover_color="#10264B",
            command=self.sync_ledger,
        ).pack(side="left", padx=(0, 12))

        ctk.CTkButton(
            buttons,
            text="Sign Out",
            width=145,
            height=42,
            fg_color="#363636",
            hover_color="#444444",
            command=self.sign_out,
        ).pack(side="right")

        self.ledger_panel = ctk.CTkFrame(root, fg_color="#2A2A2A", corner_radius=8)
        self.ledger_panel.pack(fill="x", pady=(0, 18), ipady=8)

        ctk.CTkLabel(
            self.ledger_panel,
            text="Token Ledger",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#EAEFF7",
        ).pack(anchor="w", padx=18, pady=(12, 8))

        self.ledger_values = ctk.CTkFrame(self.ledger_panel, fg_color="transparent")
        self.ledger_values.pack(fill="x", padx=18, pady=(0, 14))

        ctk.CTkLabel(
            root,
            text="Activity Log",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#EAEFF7",
        ).pack(anchor="w", padx=4, pady=(0, 6))

        self.log_box = ctk.CTkTextbox(
            root,
            fg_color="#151515",
            text_color="#EFEFEF",
            font=("Menlo", 13),
            corner_radius=8,
        )
        self.log_box.pack(fill="both", expand=True)

    def row(self, parent, label, value):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=18, pady=10)

        ctk.CTkLabel(
            frame,
            text=label,
            text_color="#A9A9A9",
            width=155,
            anchor="w",
            font=ctk.CTkFont(size=15),
        ).pack(side="left")

        ctk.CTkLabel(
            frame,
            text=str(value or "—"),
            text_color="#EAEFF7",
            anchor="w",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(side="left")

    def ledger_col(self, parent, label, value):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(side="left", fill="x", expand=True)

        ctk.CTkLabel(
            frame,
            text=label,
            text_color="#A9A9A9",
            font=ctk.CTkFont(size=14),
        ).pack(anchor="w")

        ctk.CTkLabel(
            frame,
            text=str(value or "—"),
            text_color="#EAEFF7",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", pady=(6, 0))

    def refresh_dashboard(self):
        for panel in [self.left_panel, self.right_panel, self.ledger_values]:
            for child in panel.winfo_children():
                child.destroy()

        status = read_json(STATUS_PATH)
        cache = read_user_cache()
        release = read_json(RELEASE_METADATA_PATH)
        model = detect_model_status()
        active = service_active()

        provider = get_provider_email()
        hardware = status.get("hardwareId") or get_hardware_id()
        wallet = (
            status.get("wallet")
            or status.get("walletAddress")
            or cache.get("wallet")
            or "Not linked"
        )

        self.online_label.configure(
            text="Online" if active else "Offline",
            text_color="#46D369" if active else "#FF6B6B",
        )
        self.level_badge.configure(text=model.get("level") or "Node")

        self.row(self.left_panel, "Provider:", provider)
        self.row(self.left_panel, "Wallet:", short_middle(wallet, 12, 6) if wallet != "Not linked" else wallet)
        self.row(self.left_panel, "Hardware ID:", short_middle(hardware, 14, 6))
        self.row(self.left_panel, "Platform:", "linux x64")

        release_channel = str(
            release.get("releaseChannel") or "unknown"
        ).strip()

        release_label = release_channel.replace("_", " ").title()

        mode = (
            f"{release_label} | "
            f"{model['level'].replace(' Node', '')} rewards enabled | "
            f"neural={'true' if model.get('neural') else 'false'}"
        )

        self.row(self.right_panel, "Desktop Version:", APP_VERSION)
        self.row(self.right_panel, "Core Version:", CORE_VERSION)
        self.row(self.right_panel, "Release:", release_channel)
        self.row(self.right_panel, "Mode:", mode)

        ledger = get_ledger_defaults(model)
        ledger.update({k: v for k, v in cache.items() if k in ("balance", "usd", "rewards", "lastSync")})

        self.ledger_col(self.ledger_values, "Balance", ledger.get("balance"))
        self.ledger_col(self.ledger_values, "USD Value", ledger.get("usd"))
        self.ledger_col(self.ledger_values, "Rewards", ledger.get("rewards"))
        self.ledger_col(self.ledger_values, "Last Sync", ledger.get("lastSync"))

        self.log_box.delete("1.0", "end")
        self.log_box.insert("1.0", get_latest_logs())

    def auto_refresh(self):
        try:
            self.refresh_dashboard()
        finally:
            self.after(10000, self.auto_refresh)

    def service_action(self, action):
        def worker():
            pkexec(["systemctl", action, SERVICE_NAME], timeout=60)
            self.after(0, self.refresh_dashboard)

        threading.Thread(target=worker, daemon=True).start()

    def check_updates(self):
        def worker():
            pkexec(["systemctl", "start", "edgeswarm-node-updater.service"], timeout=120)
            self.after(0, self.refresh_dashboard)

        threading.Thread(target=worker, daemon=True).start()

    def sync_ledger(self):
        def worker():
            provider = get_provider_email()

            balance_text = "— SWARM"
            usd_text = "—"

            if provider and provider != "Not signed in":
                for path in ["/v1/provider/ledger", "/v1/provider/ledge"]:
                    try:
                        r = requests.get(
                            f"{API_BASE}{path}",
                            params={
                                "providerEmail": provider,
                                "limit": 20,
                                "t": int(time.time() * 1000),
                            },
                            timeout=15,
                        )

                        if r.status_code >= 300:
                            continue

                        data = r.json()

                        balance = (
                            data.get("balance")
                            or data.get("verified_balance")
                            or data.get("verifiedBalance")
                            or data.get("total")
                            or data.get("amount")
                        )

                        if balance is not None:
                            value = float(balance)
                            balance_text = f"{value:.2f} SWARM"
                            usd_text = f"${value * 0.10:.2f}"
                            break

                    except Exception:
                        pass

            write_user_cache({
                "balance": balance_text,
                "usd": usd_text,
                "lastSync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

            self.after(0, self.refresh_dashboard)

        threading.Thread(target=worker, daemon=True).start()

    def sign_out(self):
        if not messagebox.askyesno("Sign Out", "Sign out and stop the EdgeSwarm node?"):
            return

        def worker():
            pkexec([
                "bash",
                "-lc",
                f"rm -f {AUTH_PATH} {STATUS_PATH}; systemctl disable --now {SERVICE_NAME}",
            ], timeout=60)
            try:
                if USER_CACHE_PATH.exists():
                    USER_CACHE_PATH.unlink()
            except Exception:
                pass
            self.after(0, self.on_sign_out)

        threading.Thread(target=worker, daemon=True).start()
