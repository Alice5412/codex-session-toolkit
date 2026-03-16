#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

if os.name == "nt":
    from ctypes import wintypes

from account_switcher import (
    AccountSourceSummary,
    AccountSwitchError,
    describe_target_codex_home,
    detect_default_accounts_root,
    find_matching_target_account,
    list_account_sources,
    resolve_account_source,
    resolve_accounts_root,
    switch_account,
)
from app_state import load_gui_state, save_gui_state
from conversation_transfer import ConversationCopyResult
from desktop_app import restart_codex_desktop_app
from fork_cli import (
    DEFAULT_CODEX_HOME,
    CodexAppServerClient,
    ForkToolError,
    SessionSummary,
    UserTurnSummary,
    find_sessions,
    load_user_turns_for_session,
    parse_user_turns_from_rollout,
    perform_fork,
)
from gui_theme import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_SOFT,
    APP_BG,
    BORDER,
    CARD_ALT_BG,
    CARD_BG,
    INPUT_BG,
    SELECTION_BG,
    SIDEBAR_BG,
    SURFACE_BG,
    TABLE_BG,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    short_text,
    ui_font,
)
from transfer_dialog import ConversationTransferDialog

MAX_REMEMBERED_WORKDIRS = 15


def normalize_workdir(path_value: str | Path) -> str:
    return str(Path(path_value).expanduser().resolve())


def unique_existing_workdirs(values: list[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        normalized = normalize_workdir(text)
        path = Path(normalized)
        if not path.exists() or not path.is_dir():
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= MAX_REMEMBERED_WORKDIRS:
            break
    return result


def short_workspace_label(path_value: str) -> str:
    path = Path(path_value)
    name = path.name.strip()
    if name:
        return short_text(name, 28)
    return short_text(path_value, 28)


if os.name == "nt":
    WM_APP = 0x8000
    WM_COMMAND = 0x0111
    WM_CLOSE = 0x0010
    WM_DESTROY = 0x0002
    WM_LBUTTONUP = 0x0202
    WM_LBUTTONDBLCLK = 0x0203
    WM_RBUTTONUP = 0x0205
    NIF_MESSAGE = 0x0001
    NIF_ICON = 0x0002
    NIF_TIP = 0x0004
    NIM_ADD = 0x00000000
    NIM_DELETE = 0x00000002
    MF_STRING = 0x0000
    MF_SEPARATOR = 0x0800
    TPM_RIGHTBUTTON = 0x0002
    IDI_APPLICATION = 32512
    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32

    def make_int_resource(resource_id: int) -> wintypes.LPCWSTR:
        return ctypes.cast(ctypes.c_void_p(resource_id), wintypes.LPCWSTR)

    class POINT(ctypes.Structure):
        _fields_ = [
            ("x", wintypes.LONG),
            ("y", wintypes.LONG),
        ]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", POINT),
        ]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HANDLE),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uTimeoutOrVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
    user32.TrackPopupMenu.restype = wintypes.BOOL
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.DestroyMenu.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.LoadIconW.restype = wintypes.HICON
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    class WindowsTrayIcon:
        TRAY_CALLBACK_MESSAGE = WM_APP + 1
        CMD_RESTORE = 1001
        CMD_EXIT = 1002

        def __init__(self, tooltip: str, on_restore, on_exit) -> None:
            self.tooltip = tooltip[:127]
            self.on_restore = on_restore
            self.on_exit = on_exit
            self.class_name = f"CodexAnyNodeForkTray_{os.getpid()}_{id(self)}"
            self._wndproc = WNDPROC(self._window_proc)
            self._ready = threading.Event()
            self._thread: threading.Thread | None = None
            self._hwnd: wintypes.HWND | None = None
            self._icon_added = False
            self._hicon = user32.LoadIconW(None, make_int_resource(IDI_APPLICATION))

        def ensure_started(self) -> bool:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run_message_loop, daemon=True)
                self._thread.start()
            self._ready.wait(timeout=2.0)
            return self._hwnd is not None

        def show(self) -> bool:
            if not self.ensure_started() or self._hwnd is None:
                return False
            if self._icon_added:
                return True
            data = self._build_notify_icon_data()
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
                return False
            self._icon_added = True
            return True

        def hide(self) -> None:
            if not self._icon_added or self._hwnd is None:
                return
            data = self._build_notify_icon_data()
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
            self._icon_added = False

        def stop(self) -> None:
            self.hide()
            if self._hwnd is not None:
                user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            self._thread = None
            self._hwnd = None

        def _build_notify_icon_data(self) -> NOTIFYICONDATAW:
            data = NOTIFYICONDATAW()
            data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            data.hWnd = self._hwnd
            data.uID = 1
            data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            data.uCallbackMessage = self.TRAY_CALLBACK_MESSAGE
            data.hIcon = self._hicon
            data.szTip = self.tooltip
            return data

        def _run_message_loop(self) -> None:
            hinstance = kernel32.GetModuleHandleW(None)
            window_class = WNDCLASSW()
            window_class.hInstance = hinstance
            window_class.lpszClassName = self.class_name
            window_class.lpfnWndProc = self._wndproc
            user32.RegisterClassW(ctypes.byref(window_class))
            self._hwnd = user32.CreateWindowExW(
                0,
                self.class_name,
                self.class_name,
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                hinstance,
                None,
            )
            self._ready.set()

            message = MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))

            self._hwnd = None
            user32.UnregisterClassW(self.class_name, hinstance)

        def _show_context_menu(self, hwnd: wintypes.HWND) -> None:
            menu = user32.CreatePopupMenu()
            user32.AppendMenuW(menu, MF_STRING, self.CMD_RESTORE, "Open")
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, self.CMD_EXIT, "Exit")
            point = POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(hwnd)
            user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y, 0, hwnd, None)
            user32.DestroyMenu(menu)

        def _window_proc(
            self,
            hwnd: wintypes.HWND,
            message: int,
            wparam: int,
            lparam: int,
        ) -> int:
            if message == self.TRAY_CALLBACK_MESSAGE:
                if lparam in {WM_LBUTTONUP, WM_LBUTTONDBLCLK}:
                    self.on_restore()
                    return 0
                if lparam == WM_RBUTTONUP:
                    self._show_context_menu(hwnd)
                    return 0
            elif message == WM_COMMAND:
                command = wparam & 0xFFFF
                if command == self.CMD_RESTORE:
                    self.on_restore()
                    return 0
                if command == self.CMD_EXIT:
                    self.on_exit()
                    return 0
            elif message == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            elif message == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)


class ForkGuiApp:
    def __init__(
        self,
        root: tk.Tk,
        codex_home: Path,
        workdir: Path,
        accounts_root: Path | None,
        remembered_workdirs: list[str],
        minimize_to_tray_on_close: bool,
    ) -> None:
        initial_accounts_root = ""
        if accounts_root is not None:
            initial_accounts_root = str(accounts_root.expanduser().resolve())
        else:
            detected_accounts_root = detect_default_accounts_root()
            if detected_accounts_root is not None:
                initial_accounts_root = str(detected_accounts_root)

        self.root = root
        self.codex_home_var = tk.StringVar(value=str(codex_home))
        self.workdir_var = tk.StringVar(value=normalize_workdir(workdir))
        self.accounts_root_var = tk.StringVar(value=initial_accounts_root)
        self.selected_account_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Ready.")
        self.session_metric_var = tk.StringVar(value="0")
        self.turn_metric_var = tk.StringVar(value="0")
        self.workspace_metric_var = tk.StringVar(value=short_workspace_label(str(workdir)))
        self.workspace_path_var = tk.StringVar(value=str(workdir))
        self.last_refresh_var = tk.StringVar(value="Waiting for first refresh")
        self.selection_summary_var = tk.StringVar(value="No conversation selected")
        self.turn_summary_var = tk.StringVar(value="No user message selected")
        self.active_account_var = tk.StringVar(value="Active match: not checked")
        self.installed_account_var = tk.StringVar(value="Installed target: not checked")
        self.account_status_var = tk.StringVar(value="Account switcher is ready.")
        self.minimize_to_tray_var = tk.BooleanVar(value=minimize_to_tray_on_close)
        self.remembered_workdirs = unique_existing_workdirs(
            [str(workdir), *remembered_workdirs]
        )
        self.last_loaded_workdir = ""
        self.is_hidden_to_tray = False

        self.accounts: list[AccountSourceSummary] = []
        self.account_index: dict[str, AccountSourceSummary] = {}
        self.sessions: list[SessionSummary] = []
        self.session_index: dict[str, SessionSummary] = {}
        self.turns: list[UserTurnSummary] = []
        self.turn_index: dict[str, UserTurnSummary] = {}
        self.transfer_dialog: ConversationTransferDialog | None = None

        self.busy_count = 0
        self.session_request_token = 0
        self.turn_request_token = 0
        self.suppress_session_event = False

        self.refresh_button: ttk.Button
        self.fork_button: ttk.Button
        self.transfer_button: ttk.Button
        self.account_refresh_button: ttk.Button
        self.account_switch_button: ttk.Button
        self.codex_home_browse_button: ttk.Button
        self.workdir_browse_button: ttk.Button
        self.accounts_root_browse_button: ttk.Button
        self.codex_home_entry: ttk.Entry
        self.workdir_combo: ttk.Combobox
        self.accounts_root_entry: ttk.Entry
        self.account_combo: ttk.Combobox
        self.recent_workdirs_listbox: tk.Listbox
        self.sessions_tree: ttk.Treeview
        self.turns_tree: ttk.Treeview
        self.session_detail: tk.Text
        self.turn_detail: tk.Text
        self.minimize_to_tray_checkbutton: tk.Checkbutton

        self.tray_icon = (
            WindowsTrayIcon(
                tooltip="Codex Session Toolkit",
                on_restore=lambda: self.root.after(0, self.restore_from_tray),
                on_exit=lambda: self.root.after(0, self.exit_application),
            )
            if os.name == "nt"
            else None
        )

        self._configure_root()
        self._configure_styles()
        self._build_layout()
        self._rebuild_recent_workdirs_list()
        self._sync_recent_workdir_selection()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_requested)
        self.refresh_accounts()
        self.refresh_sessions()

    def _configure_root(self) -> None:
        self.root.title("Codex Session Toolkit")
        self.root.geometry("1540x940")
        self.root.minsize(1240, 760)
        self.root.configure(bg=APP_BG)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.option_add("*Font", "{Segoe UI} 10")

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        self.root.option_add("*TCombobox*Listbox.background", INPUT_BG)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT_PRIMARY)
        self.root.option_add("*TCombobox*Listbox.selectBackground", SELECTION_BG)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT_PRIMARY)

        style.configure(".", background=APP_BG, foreground=TEXT_PRIMARY)
        style.configure(
            "App.TEntry",
            fieldbackground=INPUT_BG,
            foreground=TEXT_PRIMARY,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            insertcolor=TEXT_PRIMARY,
            padding=(12, 10),
            relief="flat",
        )
        style.configure(
            "App.TCombobox",
            fieldbackground=INPUT_BG,
            foreground=TEXT_PRIMARY,
            background=INPUT_BG,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            arrowcolor=TEXT_SECONDARY,
            padding=(12, 10),
            relief="flat",
        )
        style.map(
            "App.TCombobox",
            fieldbackground=[("readonly", INPUT_BG), ("focus", INPUT_BG)],
            foreground=[("disabled", TEXT_MUTED)],
            arrowcolor=[("active", TEXT_PRIMARY), ("disabled", TEXT_MUTED)],
        )
        style.configure(
            "Primary.TButton",
            background=ACCENT,
            foreground="#ffffff",
            bordercolor=ACCENT,
            focuscolor=ACCENT,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
            padding=(18, 12),
            relief="flat",
            font=ui_font(10, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("active", ACCENT_HOVER), ("disabled", CARD_ALT_BG)],
            foreground=[("disabled", TEXT_MUTED)],
            bordercolor=[("disabled", CARD_ALT_BG)],
        )
        style.configure(
            "Secondary.TButton",
            background=CARD_ALT_BG,
            foreground=TEXT_PRIMARY,
            bordercolor=BORDER,
            focuscolor=CARD_ALT_BG,
            lightcolor=CARD_ALT_BG,
            darkcolor=CARD_ALT_BG,
            padding=(16, 12),
            relief="flat",
            font=ui_font(10, "bold"),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", ACCENT_SOFT), ("disabled", CARD_ALT_BG)],
            foreground=[("disabled", TEXT_MUTED)],
            bordercolor=[("disabled", BORDER)],
        )
        style.configure(
            "Card.Treeview",
            background=TABLE_BG,
            fieldbackground=TABLE_BG,
            foreground=TEXT_PRIMARY,
            bordercolor=CARD_BG,
            lightcolor=CARD_BG,
            darkcolor=CARD_BG,
            rowheight=34,
            relief="flat",
        )
        style.map(
            "Card.Treeview",
            background=[("selected", SELECTION_BG)],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Card.Treeview.Heading",
            background=CARD_BG,
            foreground=TEXT_SECONDARY,
            bordercolor=CARD_BG,
            lightcolor=CARD_BG,
            darkcolor=CARD_BG,
            relief="flat",
            padding=(10, 10),
            font=ui_font(10, "bold"),
        )
        style.map(
            "Card.Treeview.Heading",
            background=[("active", CARD_ALT_BG)],
            foreground=[("active", TEXT_PRIMARY)],
        )
        style.configure(
            "Vertical.TScrollbar",
            background=CARD_ALT_BG,
            bordercolor=CARD_ALT_BG,
            troughcolor=SURFACE_BG,
            arrowcolor=TEXT_SECONDARY,
        )

    def _build_layout(self) -> None:
        shell = tk.Frame(self.root, bg=APP_BG, padx=20, pady=20)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=0)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(0, weight=1)

        sidebar = tk.Frame(
            shell,
            bg=SIDEBAR_BG,
            width=290,
            highlightthickness=1,
            highlightbackground=BORDER,
            padx=18,
            pady=18,
        )
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 18))
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        sidebar.rowconfigure(2, weight=1)

        content = tk.Frame(shell, bg=APP_BG)
        content.grid(row=0, column=1, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(4, weight=1)

        self._build_sidebar(sidebar)
        self._build_content(content)
        self.root.bind("<F5>", self.on_refresh_hotkey)

    def _build_sidebar(self, parent: tk.Frame) -> None:
        brand_card = self._build_card(parent, padded=True)
        brand_card.grid(row=0, column=0, sticky="ew")

        tk.Label(
            brand_card,
            text="Codex Toolkit",
            bg=CARD_BG,
            fg=TEXT_PRIMARY,
            font=ui_font(22, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            brand_card,
            text="Modernized workspace fork console for Codex sessions.",
            bg=CARD_BG,
            fg=TEXT_SECONDARY,
            font=ui_font(10),
            wraplength=220,
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))

        status_card = self._build_card(parent, padded=True)
        status_card.grid(row=1, column=0, sticky="ew", pady=(16, 16))
        status_card.columnconfigure(0, weight=1)
        self._build_card_title(status_card, "Workspace Status").grid(row=0, column=0, sticky="w")
        tk.Label(status_card, textvariable=self.workspace_path_var, bg=CARD_BG, fg=TEXT_PRIMARY, font=ui_font(10, "bold"), wraplength=220, justify="left", anchor="w").grid(row=1, column=0, sticky="ew", pady=(10, 4))
        tk.Label(status_card, textvariable=self.last_refresh_var, bg=CARD_BG, fg=TEXT_SECONDARY, font=ui_font(10), wraplength=220, justify="left", anchor="w").grid(row=2, column=0, sticky="ew")
        tk.Label(status_card, textvariable=self.selection_summary_var, bg=CARD_BG, fg=TEXT_PRIMARY, font=ui_font(10), wraplength=220, justify="left", anchor="w").grid(row=3, column=0, sticky="ew", pady=(12, 2))
        tk.Label(status_card, textvariable=self.turn_summary_var, bg=CARD_BG, fg=TEXT_MUTED, font=ui_font(10), wraplength=220, justify="left", anchor="w").grid(row=4, column=0, sticky="ew")
        self.minimize_to_tray_checkbutton = tk.Checkbutton(
            status_card,
            text="Minimize to tray when closing",
            variable=self.minimize_to_tray_var,
            command=self.on_minimize_setting_changed,
            bg=CARD_BG,
            fg=TEXT_SECONDARY,
            activebackground=CARD_BG,
            activeforeground=TEXT_PRIMARY,
            selectcolor=INPUT_BG,
            highlightthickness=0,
            borderwidth=0,
            anchor="w",
            justify="left",
            font=ui_font(10),
        )
        self.minimize_to_tray_checkbutton.grid(row=5, column=0, sticky="w", pady=(14, 0))

        recent_card = self._build_card(parent, padded=True)
        recent_card.grid(row=2, column=0, sticky="nsew")
        recent_card.columnconfigure(0, weight=1)
        recent_card.rowconfigure(1, weight=1)
        self._build_card_title(recent_card, "Recent Workdirs").grid(row=0, column=0, sticky="w")

        list_container = tk.Frame(recent_card, bg=TABLE_BG, highlightthickness=1, highlightbackground=BORDER)
        list_container.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        list_container.columnconfigure(0, weight=1)
        list_container.rowconfigure(0, weight=1)

        self.recent_workdirs_listbox = tk.Listbox(
            list_container,
            bg=TABLE_BG,
            fg=TEXT_PRIMARY,
            selectbackground=SELECTION_BG,
            selectforeground="#ffffff",
            activestyle="none",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            exportselection=False,
            font=ui_font(10),
        )
        self.recent_workdirs_listbox.grid(row=0, column=0, sticky="nsew")
        self.recent_workdirs_listbox.bind("<<ListboxSelect>>", self.on_recent_workdir_selected)

        recent_scroll = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.recent_workdirs_listbox.yview)
        recent_scroll.grid(row=0, column=1, sticky="ns")
        self.recent_workdirs_listbox.configure(yscrollcommand=recent_scroll.set)

        footer = tk.Label(
            parent,
            textvariable=self.status_var,
            bg=SIDEBAR_BG,
            fg=TEXT_SECONDARY,
            font=ui_font(10),
            wraplength=250,
            justify="left",
            anchor="w",
        )
        footer.grid(row=3, column=0, sticky="ew", pady=(16, 0))

    def _build_content(self, parent: tk.Frame) -> None:
        header_card = self._build_card(parent, padded=True)
        header_card.grid(row=0, column=0, sticky="ew")

        tk.Label(
            header_card,
            text="Sessions And Accounts Dashboard",
            bg=CARD_BG,
            fg=TEXT_PRIMARY,
            font=ui_font(28, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header_card,
            text="Browse remembered workspaces, fork from any user turn, and switch Codex account files without leaving the same console.",
            bg=CARD_BG,
            fg=TEXT_SECONDARY,
            font=ui_font(11),
            wraplength=860,
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))

        metrics = tk.Frame(parent, bg=APP_BG)
        metrics.grid(row=1, column=0, sticky="ew", pady=(16, 16))
        metrics.columnconfigure(0, weight=1)
        metrics.columnconfigure(1, weight=1)
        metrics.columnconfigure(2, weight=1)
        self._build_metric_card(metrics, "Conversations", self.session_metric_var, 0)
        self._build_metric_card(metrics, "User Turns", self.turn_metric_var, 1)
        self._build_metric_card(metrics, "Workspace", self.workspace_metric_var, 2)

        controls_card = self._build_card(parent, padded=True)
        controls_card.grid(row=2, column=0, sticky="ew", pady=(0, 16))
        controls_card.columnconfigure(1, weight=1)
        controls_card.columnconfigure(4, weight=1)

        tk.Label(controls_card, text="Codex Home", bg=CARD_BG, fg=TEXT_SECONDARY, font=ui_font(10, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.codex_home_entry = ttk.Entry(controls_card, textvariable=self.codex_home_var, style="App.TEntry")
        self.codex_home_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        self.codex_home_browse_button = ttk.Button(controls_card, text="Browse...", style="Secondary.TButton", command=self.browse_codex_home)
        self.codex_home_browse_button.grid(row=0, column=2, sticky="w", padx=(0, 18))

        tk.Label(controls_card, text="Workdir", bg=CARD_BG, fg=TEXT_SECONDARY, font=ui_font(10, "bold")).grid(row=0, column=3, sticky="w", padx=(0, 10))
        self.workdir_combo = ttk.Combobox(
            controls_card,
            textvariable=self.workdir_var,
            values=self.remembered_workdirs,
            style="App.TCombobox",
        )
        self.workdir_combo.grid(row=0, column=4, sticky="ew", padx=(0, 10))
        self.workdir_combo.bind("<<ComboboxSelected>>", self.on_workdir_selected)
        self.workdir_combo.bind("<Return>", self.on_workdir_submitted)
        self.workdir_combo.bind("<FocusOut>", self.on_workdir_focus_out)
        self.workdir_browse_button = ttk.Button(controls_card, text="Browse...", style="Secondary.TButton", command=self.browse_workdir)
        self.workdir_browse_button.grid(row=0, column=5, sticky="w", padx=(0, 18))

        actions = tk.Frame(controls_card, bg=CARD_BG)
        actions.grid(row=0, column=6, sticky="e")
        self.refresh_button = ttk.Button(actions, text="Refresh", style="Secondary.TButton", command=self.refresh_sessions)
        self.refresh_button.grid(row=0, column=0, padx=(0, 10))
        self.fork_button = ttk.Button(actions, text="Fork Selected Turn", style="Primary.TButton", command=self.start_fork)
        self.fork_button.grid(row=0, column=1)
        self.transfer_button = ttk.Button(
            actions,
            text="Transfer Conversations",
            style="Secondary.TButton",
            command=self.open_transfer_dialog,
        )
        self.transfer_button.grid(row=0, column=2, padx=(10, 0))

        account_card = self._build_card(parent, padded=True)
        account_card.grid(row=3, column=0, sticky="ew", pady=(0, 16))
        account_card.columnconfigure(1, weight=1)
        account_card.columnconfigure(4, weight=1)
        self._build_card_title(account_card, "Account Switcher").grid(row=0, column=0, columnspan=7, sticky="w")

        tk.Label(account_card, text="Accounts Root", bg=CARD_BG, fg=TEXT_SECONDARY, font=ui_font(10, "bold")).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=(12, 0))
        self.accounts_root_entry = ttk.Entry(account_card, textvariable=self.accounts_root_var, style="App.TEntry")
        self.accounts_root_entry.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(12, 0))
        self.accounts_root_browse_button = ttk.Button(
            account_card,
            text="Browse...",
            style="Secondary.TButton",
            command=self.browse_accounts_root,
        )
        self.accounts_root_browse_button.grid(row=1, column=2, sticky="w", padx=(0, 18), pady=(12, 0))

        tk.Label(account_card, text="Account", bg=CARD_BG, fg=TEXT_SECONDARY, font=ui_font(10, "bold")).grid(row=1, column=3, sticky="w", padx=(0, 10), pady=(12, 0))
        self.account_combo = ttk.Combobox(
            account_card,
            textvariable=self.selected_account_var,
            values=[],
            style="App.TCombobox",
        )
        self.account_combo.grid(row=1, column=4, sticky="ew", padx=(0, 10), pady=(12, 0))
        self.account_combo.bind("<<ComboboxSelected>>", self.on_account_selected)
        self.account_combo.bind("<Return>", self.on_account_submitted)

        account_actions = tk.Frame(account_card, bg=CARD_BG)
        account_actions.grid(row=1, column=5, columnspan=2, sticky="e", pady=(12, 0))
        self.account_refresh_button = ttk.Button(
            account_actions,
            text="Refresh Accounts",
            style="Secondary.TButton",
            command=self.refresh_accounts,
        )
        self.account_refresh_button.grid(row=0, column=0, padx=(0, 10))
        self.account_switch_button = ttk.Button(
            account_actions,
            text="Switch Account",
            style="Primary.TButton",
            command=self.start_account_switch,
        )
        self.account_switch_button.grid(row=0, column=1)

        tk.Label(
            account_card,
            textvariable=self.active_account_var,
            bg=CARD_BG,
            fg=TEXT_PRIMARY,
            font=ui_font(10, "bold"),
            wraplength=980,
            justify="left",
            anchor="w",
        ).grid(row=2, column=0, columnspan=7, sticky="ew", pady=(14, 4))
        tk.Label(
            account_card,
            textvariable=self.installed_account_var,
            bg=CARD_BG,
            fg=TEXT_SECONDARY,
            font=ui_font(10),
            wraplength=980,
            justify="left",
            anchor="w",
        ).grid(row=3, column=0, columnspan=7, sticky="ew", pady=(0, 4))
        tk.Label(
            account_card,
            textvariable=self.account_status_var,
            bg=CARD_BG,
            fg=TEXT_MUTED,
            font=ui_font(10),
            wraplength=980,
            justify="left",
            anchor="w",
        ).grid(row=4, column=0, columnspan=7, sticky="ew")

        lists = tk.Frame(parent, bg=APP_BG)
        lists.grid(row=4, column=0, sticky="nsew")
        lists.columnconfigure(0, weight=1)
        lists.columnconfigure(1, weight=1)
        lists.rowconfigure(0, weight=1)
        lists.rowconfigure(1, weight=1)

        sessions_card = self._build_card(lists, padded=True)
        sessions_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        sessions_card.columnconfigure(0, weight=1)
        sessions_card.rowconfigure(1, weight=1)
        self._build_card_title(sessions_card, "Conversations").grid(row=0, column=0, sticky="w")
        self.sessions_tree = self._build_tree(
            sessions_card,
            row=1,
            columns=("updated", "title"),
            headings=(("updated", "Updated", 160, "w"), ("title", "Title", 460, "w")),
        )
        self.sessions_tree.bind("<<TreeviewSelect>>", self.on_session_selected)

        turns_card = self._build_card(lists, padded=True)
        turns_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0), pady=(0, 10))
        turns_card.columnconfigure(0, weight=1)
        turns_card.rowconfigure(1, weight=1)
        self._build_card_title(turns_card, "User Messages").grid(row=0, column=0, sticky="w")
        self.turns_tree = self._build_tree(
            turns_card,
            row=1,
            columns=("index", "preview"),
            headings=(("index", "Turn", 90, "center"), ("preview", "Preview", 560, "w")),
        )
        self.turns_tree.bind("<<TreeviewSelect>>", self.on_turn_selected)
        self.turns_tree.bind("<Double-1>", self.on_turn_double_click)

        session_detail_card = self._build_card(lists, padded=True)
        session_detail_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(10, 0))
        session_detail_card.columnconfigure(0, weight=1)
        session_detail_card.rowconfigure(1, weight=1)
        self._build_card_title(session_detail_card, "Conversation Detail").grid(row=0, column=0, sticky="w")
        self.session_detail = self._build_text_panel(session_detail_card, row=1)
        self.set_text(self.session_detail, "Refresh the list or select a conversation to inspect its details.")

        turn_detail_card = self._build_card(lists, padded=True)
        turn_detail_card.grid(row=1, column=1, sticky="nsew", padx=(10, 0), pady=(10, 0))
        turn_detail_card.columnconfigure(0, weight=1)
        turn_detail_card.rowconfigure(1, weight=1)
        self._build_card_title(turn_detail_card, "Message Detail").grid(row=0, column=0, sticky="w")
        self.turn_detail = self._build_text_panel(turn_detail_card, row=1)
        self.set_text(self.turn_detail, "Select a conversation to load its user messages.")

    def _build_card(self, parent: tk.Widget, *, padded: bool) -> tk.Frame:
        return tk.Frame(
            parent,
            bg=CARD_BG,
            highlightthickness=1,
            highlightbackground=BORDER,
            padx=18 if padded else 0,
            pady=18 if padded else 0,
        )

    def _build_card_title(self, parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            bg=CARD_BG,
            fg=TEXT_PRIMARY,
            font=ui_font(12, "bold"),
            anchor="w",
        )

    def _build_metric_card(self, parent: tk.Frame, title: str, value_var: tk.StringVar, column: int) -> None:
        metric = self._build_card(parent, padded=True)
        padx = (0, 8) if column == 0 else (8, 8) if column == 1 else (8, 0)
        metric.grid(row=0, column=column, sticky="ew", padx=padx)
        tk.Label(metric, text=title, bg=CARD_BG, fg=TEXT_SECONDARY, font=ui_font(10, "bold"), anchor="w").grid(row=0, column=0, sticky="w")
        tk.Label(metric, textvariable=value_var, bg=CARD_BG, fg=TEXT_PRIMARY, font=ui_font(22, "bold"), anchor="w").grid(row=1, column=0, sticky="w", pady=(10, 0))

    def _build_tree(
        self,
        parent: tk.Frame,
        *,
        row: int,
        columns: tuple[str, ...],
        headings: tuple[tuple[str, str, int, str], ...],
    ) -> ttk.Treeview:
        container = tk.Frame(parent, bg=TABLE_BG, highlightthickness=1, highlightbackground=BORDER)
        container.grid(row=row, column=0, sticky="nsew", pady=(12, 0))
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        tree = ttk.Treeview(
            container,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="Card.Treeview",
        )
        for name, label, width, anchor in headings:
            tree.heading(name, text=label)
            tree.column(name, width=width, anchor=anchor)
        tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        return tree

    def _build_text_panel(self, parent: tk.Frame, row: int) -> tk.Text:
        container = tk.Frame(parent, bg=TABLE_BG, highlightthickness=1, highlightbackground=BORDER)
        container.grid(row=row, column=0, sticky="nsew", pady=(12, 0))
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        text = tk.Text(
            container,
            wrap="word",
            height=12,
            bg=TABLE_BG,
            fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY,
            selectbackground=SELECTION_BG,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=14,
            pady=14,
            font=ui_font(10),
        )
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL, command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set, state=tk.DISABLED)
        return text

    def on_refresh_hotkey(self, _event: tk.Event[tk.Misc]) -> None:
        self.refresh_sessions()

    def _rebuild_recent_workdirs_list(self) -> None:
        self.recent_workdirs_listbox.delete(0, tk.END)
        for workdir in self.remembered_workdirs:
            self.recent_workdirs_listbox.insert(tk.END, short_text(workdir, 42))

    def _sync_recent_workdir_selection(self) -> None:
        self.recent_workdirs_listbox.selection_clear(0, tk.END)
        current_workdir = self.workdir_var.get().strip()
        if current_workdir in self.remembered_workdirs:
            index = self.remembered_workdirs.index(current_workdir)
            self.recent_workdirs_listbox.selection_set(index)
            self.recent_workdirs_listbox.see(index)

    def on_recent_workdir_selected(self, _event: tk.Event[tk.Misc]) -> None:
        if self.busy_count > 0:
            return
        selection = self.recent_workdirs_listbox.curselection()
        if not selection:
            return
        workdir = self.remembered_workdirs[selection[0]]
        self.workdir_var.set(workdir)
        self.handle_workdir_change(show_error=True)

    def on_workdir_selected(self, _event: tk.Event[tk.Misc]) -> None:
        self.handle_workdir_change(show_error=True)

    def on_workdir_submitted(self, _event: tk.Event[tk.Misc]) -> None:
        self.handle_workdir_change(show_error=True)

    def on_workdir_focus_out(self, _event: tk.Event[tk.Misc]) -> None:
        self.handle_workdir_change(show_error=False)

    def browse_codex_home(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            initialdir=self.codex_home_var.get() or str(DEFAULT_CODEX_HOME),
            mustexist=True,
            title="Select Codex home",
        )
        if selected:
            self.codex_home_var.set(selected)
            self.refresh_accounts()

    def browse_workdir(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            initialdir=self.workdir_var.get() or str(Path.cwd()),
            mustexist=True,
            title="Select working directory",
        )
        if selected:
            self.workdir_var.set(normalize_workdir(selected))
            self.handle_workdir_change(show_error=True)

    def browse_accounts_root(self) -> None:
        initialdir = self.accounts_root_var.get().strip() or str(Path(__file__).resolve().parent.parent)
        selected = filedialog.askdirectory(
            parent=self.root,
            initialdir=initialdir,
            mustexist=True,
            title="Select account source root",
        )
        if selected:
            self.accounts_root_var.set(normalize_workdir(selected))
            self.refresh_accounts(show_message_on_error=True)

    def refresh_accounts(self, show_message_on_error: bool = False) -> None:
        if self.busy_count > 0:
            return

        raw_root = self.accounts_root_var.get().strip()
        if not raw_root:
            detected = detect_default_accounts_root()
            if detected is None:
                self.accounts = []
                self.account_index = {}
                self.selected_account_var.set("")
                self.account_combo.configure(values=[])
                self.active_account_var.set("Active match: no account source folder detected")
                self.installed_account_var.set(
                    f"Installed target: {describe_target_codex_home(Path(self.codex_home_var.get()).expanduser().resolve())}"
                )
                self.account_status_var.set(
                    "Create .\\accounts\\<name>\\config.toml and auth.json, or browse to an existing source directory."
                )
                self.sync_controls()
                return
            self.accounts_root_var.set(str(detected))
            raw_root = str(detected)

        try:
            resolved_root = resolve_accounts_root(Path(raw_root))
            accounts = list_account_sources(resolved_root)
        except AccountSwitchError as exc:
            self.accounts = []
            self.account_index = {}
            self.selected_account_var.set("")
            self.account_combo.configure(values=[])
            self.active_account_var.set("Active match: unavailable")
            self.installed_account_var.set(
                f"Installed target: {describe_target_codex_home(Path(self.codex_home_var.get()).expanduser().resolve())}"
            )
            self.account_status_var.set(f"Account source error: {exc}")
            self.sync_controls()
            if show_message_on_error:
                messagebox.showerror("Account source error", str(exc), parent=self.root)
            return

        self.accounts = accounts
        self.account_index = {account.name: account for account in accounts}
        account_names = [account.name for account in accounts]
        self.account_combo.configure(values=account_names)
        self.accounts_root_var.set(str(resolved_root))

        target_codex_home = Path(self.codex_home_var.get()).expanduser().resolve()
        active_account = find_matching_target_account(accounts, target_codex_home)
        current_selection = self.selected_account_var.get().strip()

        if active_account and active_account in self.account_index:
            self.selected_account_var.set(active_account)
        elif current_selection in self.account_index:
            self.selected_account_var.set(current_selection)
        elif account_names:
            self.selected_account_var.set(account_names[0])
        else:
            self.selected_account_var.set("")

        self.active_account_var.set(
            f"Active match: {active_account if active_account else 'no exact source match'}"
        )
        self.installed_account_var.set(
            f"Installed target: {describe_target_codex_home(target_codex_home)}"
        )

        if not accounts:
            self.account_status_var.set(
                f"No switchable accounts found under {resolved_root}. "
                "Expected subdirectories containing config.toml and auth.json."
            )
        else:
            self.update_selected_account_status()

        self.sync_controls()

    def update_selected_account_status(self) -> None:
        selected_name = self.selected_account_var.get().strip()
        selected_account = self.account_index.get(selected_name)
        if selected_account is None:
            if self.accounts:
                self.account_status_var.set(
                    f"Found {len(self.accounts)} account source(s) under {self.accounts_root_var.get().strip()}."
                )
            else:
                self.account_status_var.set("No switchable accounts are available.")
            return

        self.account_status_var.set(
            f"Selected source: {selected_account.description} | {selected_account.directory}"
        )

    def on_account_selected(self, _event: tk.Event[tk.Misc]) -> None:
        self.update_selected_account_status()

    def on_account_submitted(self, _event: tk.Event[tk.Misc]) -> None:
        self.update_selected_account_status()

    def open_transfer_dialog(self) -> None:
        if self.busy_count > 0:
            return
        if not self.accounts:
            messagebox.showerror(
                "Transfer unavailable",
                "No switchable accounts are available. Refresh accounts first.",
                parent=self.root,
            )
            return
        if self.transfer_dialog is not None and self.transfer_dialog.root.winfo_exists():
            self.transfer_dialog.root.deiconify()
            self.transfer_dialog.root.lift()
            self.transfer_dialog.root.focus_force()
            return
        self.transfer_dialog = ConversationTransferDialog(self)

    def start_account_switch(self) -> None:
        if self.busy_count > 0:
            return

        account_name = self.selected_account_var.get().strip()
        if not account_name:
            messagebox.showerror("Switch failed", "Select an account first.", parent=self.root)
            return

        codex_home = Path(self.codex_home_var.get()).expanduser().resolve()
        prompt = (
            "Switch Codex account now?\n\n"
            f"Account: {account_name}\n"
            f"Target:  {codex_home}\n\n"
            "This will overwrite config.toml and auth.json in the target Codex home.\n"
            "If target files already exist, a backup folder will be created automatically."
        )
        if not messagebox.askyesno("Confirm account switch", prompt, parent=self.root):
            return

        self.begin_task(f"Switching account {account_name}...")

        def worker() -> None:
            try:
                resolved_root = resolve_accounts_root(Path(self.accounts_root_var.get()).expanduser().resolve())
                accounts = list_account_sources(resolved_root)
                selected_account = resolve_account_source(accounts, account_name)
                result = switch_account(selected_account, codex_home)
                restart_status, restart_error = restart_codex_desktop_app()
            except Exception as exc:  # noqa: BLE001
                error_message = str(exc)
                self.root.after(
                    0,
                    lambda message=error_message: self.finish_account_switch(
                        None,
                        "",
                        "",
                        message,
                    ),
                )
                return

            self.root.after(
                0,
                lambda: self.finish_account_switch(
                    result,
                    restart_status,
                    restart_error,
                    None,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def finish_account_switch(
        self,
        result,
        restart_status: str,
        restart_error: str,
        error_message: str | None,
    ) -> None:
        self.end_task()

        if error_message:
            self.status_var.set("Account switch failed.")
            messagebox.showerror("Account switch failed", error_message, parent=self.root)
            return

        if result is None:
            self.status_var.set("Account switch failed.")
            messagebox.showerror("Account switch failed", "No switch result was returned.", parent=self.root)
            return

        self.refresh_accounts()
        self.status_var.set(f"Switched account to {result.account_name}.")

        lines = [
            f"Switched account: {result.account_name}",
            f"Source folder:    {result.source_dir}",
            f"Target folder:    {result.target_dir}",
        ]
        if result.backup_dir is not None:
            lines.append(f"Backup folder:    {result.backup_dir}")
        else:
            lines.append("Backup folder:    not created (target files did not already exist)")

        if restart_status == "restarted":
            lines.append("Desktop app:      Codex App was restarted.")
        elif restart_status == "not_running":
            lines.append("Desktop app:      Codex App was not running.")
        else:
            lines.append("Desktop app:      Automatic restart failed.")
            if restart_error:
                lines.append(f"Restart warning:  {restart_error}")

        messagebox.showinfo("Account switched", "\n".join(lines), parent=self.root)
        self.status_var.set(f"Switched account to {result.account_name}. Refreshing conversations...")
        self.refresh_sessions()

    def begin_task(self, status: str) -> None:
        self.busy_count += 1
        self.status_var.set(status)
        self.sync_controls()

    def end_task(self, status: str | None = None) -> None:
        self.busy_count = max(0, self.busy_count - 1)
        if status is not None:
            self.status_var.set(status)
        self.sync_controls()

    def sync_controls(self) -> None:
        state = tk.DISABLED if self.busy_count > 0 else tk.NORMAL
        self.refresh_button.configure(state=state)
        self.fork_button.configure(
            state=tk.DISABLED
            if self.busy_count > 0 or self.current_session() is None or self.current_turn() is None
            else tk.NORMAL
        )
        self.transfer_button.configure(
            state=tk.DISABLED if self.busy_count > 0 or not self.accounts else tk.NORMAL
        )
        self.account_refresh_button.configure(state=state)
        self.account_switch_button.configure(
            state=tk.DISABLED
            if self.busy_count > 0 or not self.selected_account_var.get().strip() or not self.accounts
            else tk.NORMAL
        )
        self.codex_home_entry.configure(state=state)
        self.codex_home_browse_button.configure(state=state)
        self.workdir_combo.configure(state=state)
        self.workdir_browse_button.configure(state=state)
        self.accounts_root_entry.configure(state=state)
        self.accounts_root_browse_button.configure(state=state)
        self.account_combo.configure(state=state)
        self.minimize_to_tray_checkbutton.configure(state=state)

    def handle_workdir_change(self, *, show_error: bool) -> None:
        if self.busy_count > 0:
            return
        raw_workdir = self.workdir_var.get().strip()
        if not raw_workdir:
            return
        try:
            normalized_workdir = normalize_workdir(raw_workdir)
        except OSError as exc:
            if show_error:
                messagebox.showerror("Invalid workdir", str(exc), parent=self.root)
            self.status_var.set("Workdir change was ignored because the path is invalid.")
            return
        workdir = Path(normalized_workdir)
        if not workdir.exists() or not workdir.is_dir():
            if show_error:
                messagebox.showerror("Invalid workdir", f"Workdir does not exist: {normalized_workdir}", parent=self.root)
            self.status_var.set("Workdir change was ignored because the directory does not exist.")
            return

        self.workspace_metric_var.set(short_workspace_label(normalized_workdir))
        self.workspace_path_var.set(normalized_workdir)
        self._sync_recent_workdir_selection()
        if normalized_workdir == self.last_loaded_workdir:
            self.workdir_var.set(normalized_workdir)
            return
        self.workdir_var.set(normalized_workdir)
        self.refresh_sessions()

    def remember_workdir(self, workdir: Path) -> None:
        normalized_workdir = normalize_workdir(workdir)
        self.remembered_workdirs = unique_existing_workdirs(
            [normalized_workdir, *self.remembered_workdirs]
        )
        self.workdir_combo.configure(values=self.remembered_workdirs)
        self._rebuild_recent_workdirs_list()
        self._sync_recent_workdir_selection()
        self.persist_gui_state(last_workdir=normalized_workdir)

    def persist_gui_state(self, *, last_workdir: str | None = None) -> None:
        state_workdir = last_workdir or self.workdir_var.get().strip() or (
            self.remembered_workdirs[0] if self.remembered_workdirs else ""
        )
        try:
            save_gui_state(
                last_workdir=state_workdir,
                recent_workdirs=self.remembered_workdirs,
                minimize_to_tray_on_close=bool(self.minimize_to_tray_var.get()),
                max_remembered_workdirs=MAX_REMEMBERED_WORKDIRS,
            )
        except OSError:
            self.status_var.set("Workdir was updated, but the GUI state file could not be saved.")

    def on_minimize_setting_changed(self) -> None:
        self.persist_gui_state()

    def resolve_paths(self) -> tuple[Path, Path]:
        codex_home = Path(self.codex_home_var.get()).expanduser().resolve()
        workdir = Path(self.workdir_var.get()).expanduser().resolve()
        if not codex_home.exists():
            raise ForkToolError(f"Codex home does not exist: {codex_home}")
        if not workdir.exists():
            raise ForkToolError(f"Workdir does not exist: {workdir}")
        return codex_home, workdir

    def refresh_sessions(self, preferred_thread_id: str | None = None) -> None:
        if self.busy_count > 0:
            return
        try:
            codex_home, workdir = self.resolve_paths()
        except ForkToolError as exc:
            messagebox.showerror("Refresh failed", str(exc), parent=self.root)
            self.status_var.set("Refresh failed.")
            return

        selection = self.sessions_tree.selection()
        fallback_thread_id = preferred_thread_id or (selection[0] if selection else None)
        self.session_request_token += 1
        token = self.session_request_token
        requested_workdir = str(workdir)
        self.begin_task("Refreshing conversations...")

        def worker() -> None:
            try:
                sessions = find_sessions(codex_home, str(workdir))
            except Exception as exc:  # noqa: BLE001
                error_message = str(exc)
                self.root.after(
                    0,
                    lambda message=error_message: self.finish_refresh_sessions(
                        token,
                        [],
                        requested_workdir,
                        fallback_thread_id,
                        message,
                    ),
                )
                return
            self.root.after(
                0,
                lambda: self.finish_refresh_sessions(
                    token,
                    sessions,
                    requested_workdir,
                    fallback_thread_id,
                    None,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def finish_refresh_sessions(
        self,
        token: int,
        sessions: list[SessionSummary],
        requested_workdir: str,
        preferred_thread_id: str | None,
        error_message: str | None,
    ) -> None:
        self.end_task()
        if token != self.session_request_token:
            return

        if error_message:
            self.status_var.set("Failed to refresh conversations.")
            messagebox.showerror("Refresh failed", error_message, parent=self.root)
            return

        self.last_loaded_workdir = requested_workdir
        self.workdir_var.set(requested_workdir)
        self.workspace_metric_var.set(short_workspace_label(requested_workdir))
        self.workspace_path_var.set(requested_workdir)
        self.last_refresh_var.set(f"Refreshed {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.selection_summary_var.set("No conversation selected")
        self.turn_summary_var.set("No user message selected")
        self.remember_workdir(Path(requested_workdir))
        self.sessions = sessions
        self.session_index = {session.thread_id: session for session in sessions}
        self.session_metric_var.set(str(len(sessions)))
        self.turn_metric_var.set("0")
        self.sessions_tree.delete(*self.sessions_tree.get_children())

        for session in sessions:
            self.sessions_tree.insert(
                "",
                tk.END,
                iid=session.thread_id,
                values=(session.updated_label, short_text(session.title, 72)),
            )

        if not sessions:
            self.clear_turns()
            self.set_text(
                self.session_detail,
                f"No conversations found for workdir:\n{requested_workdir}",
            )
            self.set_text(
                self.turn_detail,
                "No user messages available because no conversation is selected.",
            )
            self.status_var.set("No conversations found for the current workspace.")
            return

        target_thread_id = preferred_thread_id if preferred_thread_id in self.session_index else sessions[0].thread_id
        self.select_session(target_thread_id)
        self.status_var.set(f"Loaded {len(sessions)} conversation(s).")

    def select_session(self, thread_id: str) -> None:
        if thread_id not in self.session_index:
            return
        self.suppress_session_event = True
        try:
            self.sessions_tree.selection_set(thread_id)
            self.sessions_tree.focus(thread_id)
            self.sessions_tree.see(thread_id)
        finally:
            self.suppress_session_event = False
        self.handle_session_selection(load_turns=True)

    def on_session_selected(self, _event: tk.Event[tk.Misc]) -> None:
        if self.suppress_session_event:
            return
        self.handle_session_selection(load_turns=True)

    def handle_session_selection(self, *, load_turns: bool) -> None:
        session = self.current_session()
        if session is None:
            self.clear_turns()
            self.selection_summary_var.set("No conversation selected")
            self.turn_summary_var.set("No user message selected")
            self.set_text(
                self.session_detail,
                "Select a conversation to inspect its details.",
            )
            self.sync_controls()
            return

        self.selection_summary_var.set(f"Conversation: {short_text(session.title, 34)}")
        details = [
            f"Thread ID: {session.thread_id}",
            f"Updated:   {session.updated_label}",
            f"Workdir:   {session.cwd}",
        ]
        if session.forked_from_id:
            details.append(f"Forked from: {session.forked_from_id}")
        if session.first_user_message:
            details.extend(["", "First user message:", session.first_user_message])
        self.set_text(self.session_detail, "\n".join(details))

        if load_turns:
            self.load_turns_for_session(session)

    def load_turns_for_session(self, session: SessionSummary) -> None:
        self.turn_request_token += 1
        token = self.turn_request_token
        self.clear_turns()
        self.turn_summary_var.set("Loading user messages...")
        self.set_text(self.turn_detail, "Loading user messages...")
        self.begin_task(f"Loading user messages for {session.title}...")

        def worker() -> None:
            app_server_error = ""
            user_turns: list[UserTurnSummary] = []
            client: CodexAppServerClient | None = None
            try:
                try:
                    client = CodexAppServerClient()
                except Exception as exc:  # noqa: BLE001
                    app_server_error = str(exc)
                    user_turns = parse_user_turns_from_rollout(session.rollout_path)
                else:
                    user_turns, app_server_error = load_user_turns_for_session(session, client)
            except Exception as exc:  # noqa: BLE001
                error_message = str(exc)
                self.root.after(
                    0,
                    lambda message=error_message: self.finish_load_turns(
                        token,
                        session.thread_id,
                        [],
                        "",
                        message,
                    ),
                )
                return
            finally:
                if client is not None:
                    client.stop()

            self.root.after(
                0,
                lambda: self.finish_load_turns(
                    token,
                    session.thread_id,
                    user_turns,
                    app_server_error,
                    None,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def finish_load_turns(
        self,
        token: int,
        thread_id: str,
        turns: list[UserTurnSummary],
        app_server_error: str,
        error_message: str | None,
    ) -> None:
        self.end_task()
        if token != self.turn_request_token:
            return

        current_session = self.current_session()
        if current_session is None or current_session.thread_id != thread_id:
            return

        if error_message:
            self.turn_summary_var.set("Failed to load user messages")
            self.status_var.set("Failed to load user messages.")
            messagebox.showerror("Load failed", error_message, parent=self.root)
            self.set_text(self.turn_detail, "Failed to load user messages.")
            return

        self.turns = turns
        self.turn_index = {turn.turn_id: turn for turn in turns}
        self.turn_metric_var.set(str(len(turns)))
        self.turns_tree.delete(*self.turns_tree.get_children())

        for turn in turns:
            self.turns_tree.insert(
                "",
                tk.END,
                iid=turn.turn_id,
                values=(turn.index, short_text(turn.preview, 82)),
            )

        if not turns:
            self.turn_summary_var.set("No user message selected")
            self.set_text(
                self.turn_detail,
                "No user messages were found in the selected conversation.",
            )
            self.sync_controls()
            self.status_var.set("Loaded 0 user messages.")
            return

        first_turn_id = turns[0].turn_id
        self.turns_tree.selection_set(first_turn_id)
        self.turns_tree.focus(first_turn_id)
        self.turns_tree.see(first_turn_id)
        self.update_turn_detail(turns[0], app_server_error)

        status = f"Loaded {len(turns)} user message turn(s)."
        if app_server_error:
            status = f"{status} Preview fallback was used."
        self.status_var.set(status)
        self.sync_controls()

    def clear_turns(self) -> None:
        self.turns = []
        self.turn_index = {}
        self.turn_metric_var.set("0")
        self.turns_tree.delete(*self.turns_tree.get_children())
        self.sync_controls()

    def on_turn_selected(self, _event: tk.Event[tk.Misc]) -> None:
        turn = self.current_turn()
        self.update_turn_detail(turn)
        self.sync_controls()

    def on_turn_double_click(self, _event: tk.Event[tk.Misc]) -> None:
        if self.current_turn() is not None and self.current_session() is not None and self.busy_count == 0:
            self.start_fork()

    def update_turn_detail(self, turn: UserTurnSummary | None, app_server_error: str = "") -> None:
        if turn is None:
            self.turn_summary_var.set("No user message selected")
            self.set_text(
                self.turn_detail,
                "Select a user message to inspect its detail.",
            )
            return

        self.turn_summary_var.set(f"Turn {turn.index}: {short_text(turn.preview, 30)}")
        lines = [
            f"Turn ID: {turn.turn_id}",
            f"Turn:    {turn.index}",
            f"Source:  {turn.source}",
        ]
        if app_server_error:
            lines.append(f"Preview fallback: {app_server_error}")
        lines.extend(["", "User message:", turn.user_text])
        self.set_text(self.turn_detail, "\n".join(lines))

    def current_session(self) -> SessionSummary | None:
        selection = self.sessions_tree.selection()
        if not selection:
            return None
        return self.session_index.get(selection[0])

    def current_turn(self) -> UserTurnSummary | None:
        selection = self.turns_tree.selection()
        if not selection:
            return None
        return self.turn_index.get(selection[0])

    def get_active_account_name(self) -> str | None:
        try:
            codex_home = Path(self.codex_home_var.get()).expanduser().resolve()
        except OSError:
            return None
        return find_matching_target_account(self.accounts, codex_home)

    def handle_transfer_complete(
        self,
        result: ConversationCopyResult,
        restart_status: str,
        restart_error: str,
    ) -> None:
        active_account = self.get_active_account_name()
        if active_account == result.target_account:
            preferred_thread_id = result.imported_thread_ids[0] if result.imported_thread_ids else None
            self.status_var.set(
                f"Copied {result.imported_count} conversation(s) to {result.target_account}. Refreshing conversations..."
            )
            self.refresh_sessions(preferred_thread_id=preferred_thread_id)
            return

        self.status_var.set(
            f"Copied {result.imported_count} conversation(s) to {result.target_account}."
        )
        if restart_status == "failed" and restart_error:
            self.status_var.set(
                f"Copied {result.imported_count} conversation(s) to {result.target_account}. Restart failed: {restart_error}"
            )

    def start_fork(self) -> None:
        if self.busy_count > 0:
            return
        session = self.current_session()
        turn = self.current_turn()
        if session is None or turn is None:
            return

        prompt = (
            "Create a fork from this user message?\n\n"
            f"Conversation: {session.title}\n"
            f"Thread ID:    {session.thread_id}\n"
            f"Target turn:  {turn.turn_id}\n"
            f"Turn index:   {turn.index}"
        )
        if not messagebox.askyesno("Confirm fork", prompt, parent=self.root):
            return

        self.begin_task("Forking the selected turn...")

        def worker() -> None:
            client: CodexAppServerClient | None = None
            try:
                client = CodexAppServerClient()
                result = perform_fork(session, turn, client)
            except Exception as exc:  # noqa: BLE001
                error_message = str(exc)
                self.root.after(0, lambda message=error_message: self.finish_fork(None, message))
                return
            finally:
                if client is not None:
                    client.stop()

            try:
                restart_status, restart_error = restart_codex_desktop_app()
            except Exception as exc:  # noqa: BLE001
                restart_status, restart_error = "failed", str(exc)
            result["desktop_app_restart_status"] = restart_status
            result["desktop_app_restart_error"] = restart_error
            self.root.after(0, lambda: self.finish_fork(result, None))

        threading.Thread(target=worker, daemon=True).start()

    def on_close_requested(self) -> None:
        if bool(self.minimize_to_tray_var.get()) and self.tray_icon is not None:
            if self.hide_to_tray():
                return
        self.exit_application()

    def hide_to_tray(self) -> bool:
        if self.tray_icon is None:
            return False
        if not self.tray_icon.show():
            messagebox.showerror("Tray unavailable", "Failed to create the system tray icon.", parent=self.root)
            return False
        self.is_hidden_to_tray = True
        self.root.withdraw()
        self.status_var.set("Application was minimized to the system tray.")
        return True

    def restore_from_tray(self) -> None:
        if self.tray_icon is not None:
            self.tray_icon.hide()
        if self.is_hidden_to_tray:
            self.root.deiconify()
            self.root.after(0, self.root.lift)
            self.root.after(0, self.root.focus_force)
        self.is_hidden_to_tray = False
        self.status_var.set("Application restored from the system tray.")

    def exit_application(self) -> None:
        if self.tray_icon is not None:
            self.tray_icon.stop()
        if self.root.winfo_exists():
            self.root.destroy()

    def finish_fork(
        self,
        result: dict[str, str | int | bool] | None,
        error_message: str | None,
    ) -> None:
        self.end_task()

        if error_message:
            self.status_var.set("Fork failed.")
            messagebox.showerror("Fork failed", error_message, parent=self.root)
            return

        if result is None:
            self.status_var.set("Fork failed.")
            messagebox.showerror("Fork failed", "No result was returned.", parent=self.root)
            return

        lines = [
            f"Original thread: {result['original_thread_id']}",
            f"Forked thread:   {result['forked_thread_id']}",
            f"Target turn:     {result['target_turn_id']}",
            f"Dropped turns:   {result['dropped_turns']}",
        ]
        forked_path = str(result.get("forked_path") or "").strip()
        if forked_path:
            lines.append(f"Fork path:       {forked_path}")

        codex_sync_status = str(result.get("codex_sync_status") or "")
        if codex_sync_status == "loaded":
            lines.append("Codex sync:      thread/resume completed and the backend reports it as loaded.")
        elif codex_sync_status == "requested":
            lines.append("Codex sync:      thread/resume was requested.")
        else:
            lines.append("Codex sync:      automatic load attempt failed.")
            codex_sync_error = str(result.get("codex_sync_error") or "").strip()
            if codex_sync_error:
                lines.append(f"Load warning:    {codex_sync_error}")

        restart_status = str(result.get("desktop_app_restart_status") or "")
        if restart_status == "restarted":
            lines.append("Desktop app:     Codex App was restarted to refresh the thread list.")
        elif restart_status == "not_running":
            lines.append("Desktop app:     Codex App was not running.")
        else:
            lines.append("Desktop app:     Automatic restart failed.")
            restart_error = str(result.get("desktop_app_restart_error") or "").strip()
            if restart_error:
                lines.append(f"Restart warning: {restart_error}")

        try:
            _, current_workdir = self.resolve_paths()
            self.remember_workdir(current_workdir)
        except ForkToolError:
            pass
        self.status_var.set(f"Forked thread {result['forked_thread_id']}. Refreshing list...")
        messagebox.showinfo("Fork complete", "\n".join(lines), parent=self.root)
        self.refresh_sessions(preferred_thread_id=str(result["forked_thread_id"]))

    @staticmethod
    def set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", text)
        widget.configure(state=tk.DISABLED)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Graphical Codex session toolkit")
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME, help="Codex home directory")
    parser.add_argument("--workdir", type=Path, help="Target working directory; defaults to the remembered GUI workdir or current directory")
    parser.add_argument("--accounts-root", type=Path, help="Directory containing one subdirectory per switchable account")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    codex_home = args.codex_home.expanduser().resolve()
    gui_state = load_gui_state(
        normalize_workdir=normalize_workdir,
        max_remembered_workdirs=MAX_REMEMBERED_WORKDIRS,
    )
    if args.workdir is not None:
        workdir = args.workdir.expanduser().resolve()
    else:
        remembered_workdir = str(gui_state.get("last_workdir") or "").strip()
        workdir = Path(remembered_workdir).expanduser().resolve() if remembered_workdir else Path.cwd().resolve()
    accounts_root = args.accounts_root.expanduser().resolve() if args.accounts_root is not None else None

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"Error: unable to start GUI: {exc}", file=sys.stderr)
        return 2

    remembered_workdirs = gui_state.get("recent_workdirs")
    ForkGuiApp(
        root,
        codex_home,
        workdir,
        accounts_root,
        remembered_workdirs if isinstance(remembered_workdirs, list) else [],
        bool(gui_state.get("minimize_to_tray_on_close")),
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
