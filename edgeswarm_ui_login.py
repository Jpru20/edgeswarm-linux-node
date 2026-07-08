#!/usr/bin/env python3
import threading

import customtkinter as ctk

from edgeswarm_ui_auth import EdgeSwarmAuthError, login_install_and_restart
from edgeswarm_ui_common import get_provider_email


class LoginFrame(ctk.CTkFrame):
    def __init__(self, master, on_login_success, on_open_dashboard):
        super().__init__(master, fg_color="#202020")

        self.on_login_success = on_login_success
        self.on_open_dashboard = on_open_dashboard

        self.email_entry = None
        self.password_entry = None
        self.totp_entry = None
        self.status_label = None
        self.login_button = None

        self.build()

    def build(self):
        outer = ctk.CTkFrame(self, fg_color="#202020")
        outer.pack(fill="both", expand=True, padx=42, pady=38)

        ctk.CTkLabel(
            outer,
            text="EdgeSwarm Node",
            font=ctk.CTkFont(size=38, weight="bold"),
            text_color="#EAEFF7",
        ).pack(anchor="w", pady=(8, 6))

        ctk.CTkLabel(
            outer,
            text="Sign in to activate your Linux node",
            font=ctk.CTkFont(size=17),
            text_color="#A7A7A7",
        ).pack(anchor="w", pady=(0, 30))

        card = ctk.CTkFrame(outer, fg_color="#2A2A2A", corner_radius=12)
        card.pack(anchor="center", pady=30, ipadx=32, ipady=26)

        ctk.CTkLabel(
            card,
            text="Provider Email",
            text_color="#BDBDBD",
            font=ctk.CTkFont(size=15),
        ).pack(anchor="w", padx=32, pady=(26, 6))

        self.email_entry = ctk.CTkEntry(
            card,
            width=430,
            height=42,
            placeholder_text="email@example.com",
        )
        self.email_entry.pack(padx=32)

        existing_provider = get_provider_email()
        if existing_provider and existing_provider != "Not signed in":
            self.email_entry.insert(0, existing_provider)

        ctk.CTkLabel(
            card,
            text="Password",
            text_color="#BDBDBD",
            font=ctk.CTkFont(size=15),
        ).pack(anchor="w", padx=32, pady=(18, 6))

        self.password_entry = ctk.CTkEntry(
            card,
            width=430,
            height=42,
            show="*",
            placeholder_text="Password",
        )
        self.password_entry.pack(padx=32)

        ctk.CTkLabel(
            card,
            text="2FA Code",
            text_color="#BDBDBD",
            font=ctk.CTkFont(size=15),
        ).pack(anchor="w", padx=32, pady=(18, 6))

        self.totp_entry = ctk.CTkEntry(
            card,
            width=430,
            height=42,
            placeholder_text="6-digit authenticator code",
        )
        self.totp_entry.pack(padx=32)

        self.status_label = ctk.CTkLabel(
            card,
            text="",
            text_color="#F5C542",
            font=ctk.CTkFont(size=14),
            wraplength=430,
            justify="left",
        )
        self.status_label.pack(anchor="w", padx=32, pady=(16, 0))

        self.login_button = ctk.CTkButton(
            card,
            text="Sign In and Start Node",
            width=430,
            height=46,
            fg_color="#16C083",
            hover_color="#13A973",
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self.submit,
        )
        self.login_button.pack(padx=32, pady=(18, 12))

        ctk.CTkButton(
            card,
            text="Open Dashboard",
            width=430,
            height=40,
            fg_color="#0A1933",
            hover_color="#10264B",
            font=ctk.CTkFont(size=15, weight="bold"),
            command=self.on_open_dashboard,
        ).pack(padx=32, pady=(0, 26))

        ctk.CTkLabel(
            outer,
            text="Password is never stored. Only the authenticated session token is saved for the node service.",
            text_color="#7C7C7C",
            font=ctk.CTkFont(size=13),
        ).pack(anchor="center", pady=(10, 0))

    def set_status(self, text, color="#F5C542"):
        self.status_label.configure(text=text, text_color=color)

    def set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.login_button.configure(state=state)

    def submit(self):
        email = self.email_entry.get().strip().lower()
        password = self.password_entry.get().strip()
        code = self.totp_entry.get().strip().replace(" ", "")

        if not email:
            self.set_status("Enter your provider email.", "#FF6B6B")
            return

        if not password:
            self.set_status("Enter your password.", "#FF6B6B")
            return

        if not code:
            self.set_status("Enter your 2FA code.", "#FF6B6B")
            return

        self.set_busy(True)
        self.set_status("Signing in and verifying 2FA...", "#F5C542")

        threading.Thread(
            target=self._login_worker,
            args=(email, password, code),
            daemon=True,
        ).start()

    def _login_worker(self, email, password, code):
        try:
            result = login_install_and_restart(email, password, code)

            provider = result.get("providerEmail") or email

            self.after(
                0,
                lambda: self.set_status(
                    f"Login successful: {provider}. Starting node service...",
                    "#46D369",
                ),
            )

            self.after(900, self.on_login_success)

        except EdgeSwarmAuthError as e:
            self.after(0, lambda: self.set_status(f"Login failed: {e}", "#FF6B6B"))
            self.after(0, lambda: self.set_busy(False))

        except Exception as e:
            self.after(0, lambda: self.set_status(f"Unexpected login error: {e}", "#FF6B6B"))
            self.after(0, lambda: self.set_busy(False))
