#!/usr/bin/env python3
import os
import sys

import customtkinter as ctk

from edgeswarm_ui_dashboard import DashboardFrame
from edgeswarm_ui_login import LoginFrame


def has_display():
    return bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


class EdgeSwarmLinuxApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("EdgeSwarm Node")
        self.geometry("1120x760")
        self.minsize(980, 680)

        self.container = ctk.CTkFrame(self, fg_color="#202020")
        self.container.pack(fill="both", expand=True)

        self.show_login()

    def clear(self):
        for child in self.container.winfo_children():
            child.destroy()

    def show_login(self):
        self.clear()
        frame = LoginFrame(
            self.container,
            on_login_success=self.show_dashboard,
            on_open_dashboard=self.show_dashboard,
        )
        frame.pack(fill="both", expand=True)

    def show_dashboard(self):
        self.clear()
        frame = DashboardFrame(
            self.container,
            on_sign_out=self.show_login,
        )
        frame.pack(fill="both", expand=True)


def main():
    if not has_display():
        print("EdgeSwarm Linux UI requires a desktop display session.")
        print("No DISPLAY or WAYLAND_DISPLAY environment variable found.")
        print("")
        print("This is expected on a headless server.")
        print("The background node service can still run with systemd:")
        print("  sudo systemctl status edgeswarm-node --no-pager -l")
        print("")
        print("Launch this UI from a Linux desktop session or app menu:")
        print("  EdgeSwarm Node")
        raise SystemExit(2)

    app = EdgeSwarmLinuxApp()

    if "--self-test" in sys.argv:
        app.after(1000, app.destroy)

    app.mainloop()


if __name__ == "__main__":
    main()
