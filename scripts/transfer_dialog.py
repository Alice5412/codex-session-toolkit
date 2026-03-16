#!/usr/bin/env python3
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from conversation_transfer import (
    AccountProfile,
    ConversationCopyResult,
    ConversationTransferError,
    TransferConversation,
    UNASSIGNED_ACCOUNT,
    assign_threads_to_account,
    build_account_counts,
    copy_conversations_to_account,
    load_transfer_view,
)
from desktop_app import restart_codex_desktop_app
from gui_theme import (
    APP_BG,
    BORDER,
    CARD_BG,
    SELECTION_BG,
    TABLE_BG,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    short_text,
    ui_font,
)


class ConversationTransferDialog:
    def __init__(self, app: "ForkGuiApp") -> None:
        self.app = app
        self.root = tk.Toplevel(app.root)
        self.root.title("Conversation Transfer")
        self.root.geometry("1320x820")
        self.root.minsize(1080, 680)
        self.root.configure(bg=APP_BG)
        self.root.transient(app.root)
        self.root.grab_set()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.codex_home = Path(app.codex_home_var.get()).expanduser().resolve()
        self.workdir = Path(app.workdir_var.get()).expanduser().resolve()
        raw_accounts_root = app.accounts_root_var.get().strip()
        self.accounts_root = Path(raw_accounts_root).expanduser().resolve() if raw_accounts_root else None

        self.status_var = tk.StringVar(value="Loading conversations...")
        self.target_account_var = tk.StringVar(value="")
        self.busy = False
        self.source_keys: list[str] = []
        self.account_profiles: list[AccountProfile] = []
        self.account_index: dict[str, AccountProfile] = {}
        self.conversations: list[TransferConversation] = []
        self.filtered_conversations: list[TransferConversation] = []

        self.refresh_button: ttk.Button
        self.assign_button: ttk.Button
        self.copy_button: ttk.Button
        self.close_button: ttk.Button
        self.target_combo: ttk.Combobox
        self.source_listbox: tk.Listbox
        self.conversations_tree: ttk.Treeview
        self.detail_text: tk.Text

        self._build_layout()
        self.reload_data()

    def _build_layout(self) -> None:
        shell = tk.Frame(self.root, bg=APP_BG, padx=18, pady=18)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=0)
        shell.columnconfigure(1, weight=1)
        shell.columnconfigure(2, weight=1)
        shell.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        header = tk.Frame(shell, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER, padx=18, pady=18)
        header.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 16))
        header.columnconfigure(0, weight=1)

        tk.Label(
            header,
            text="Conversation Transfer",
            bg=CARD_BG,
            fg=TEXT_PRIMARY,
            font=ui_font(22, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text=f"Workdir: {self.workdir}",
            bg=CARD_BG,
            fg=TEXT_SECONDARY,
            font=ui_font(10),
            anchor="w",
            justify="left",
            wraplength=1160,
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        sources_card = tk.Frame(shell, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER, padx=18, pady=18)
        sources_card.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        sources_card.columnconfigure(0, weight=1)
        sources_card.rowconfigure(1, weight=1)

        tk.Label(
            sources_card,
            text="Source Accounts",
            bg=CARD_BG,
            fg=TEXT_PRIMARY,
            font=ui_font(12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        source_list_container = tk.Frame(sources_card, bg=TABLE_BG, highlightthickness=1, highlightbackground=BORDER)
        source_list_container.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        source_list_container.columnconfigure(0, weight=1)
        source_list_container.rowconfigure(0, weight=1)

        self.source_listbox = tk.Listbox(
            source_list_container,
            bg=TABLE_BG,
            fg=TEXT_PRIMARY,
            selectbackground=SELECTION_BG,
            selectforeground="#ffffff",
            activestyle="none",
            exportselection=False,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            width=28,
            font=ui_font(10),
        )
        self.source_listbox.grid(row=0, column=0, sticky="nsew")
        self.source_listbox.bind("<<ListboxSelect>>", self.on_source_selected)
        source_scroll = ttk.Scrollbar(source_list_container, orient=tk.VERTICAL, command=self.source_listbox.yview)
        source_scroll.grid(row=0, column=1, sticky="ns")
        self.source_listbox.configure(yscrollcommand=source_scroll.set)

        conversations_card = tk.Frame(shell, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER, padx=18, pady=18)
        conversations_card.grid(row=1, column=1, sticky="nsew", padx=(0, 12))
        conversations_card.columnconfigure(0, weight=1)
        conversations_card.rowconfigure(1, weight=1)

        tk.Label(
            conversations_card,
            text="Conversations",
            bg=CARD_BG,
            fg=TEXT_PRIMARY,
            font=ui_font(12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        conversation_container = tk.Frame(conversations_card, bg=TABLE_BG, highlightthickness=1, highlightbackground=BORDER)
        conversation_container.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        conversation_container.columnconfigure(0, weight=1)
        conversation_container.rowconfigure(0, weight=1)

        self.conversations_tree = ttk.Treeview(
            conversation_container,
            columns=("updated", "provider", "title"),
            show="headings",
            selectmode="extended",
            style="Card.Treeview",
        )
        self.conversations_tree.heading("updated", text="Updated")
        self.conversations_tree.heading("provider", text="Provider")
        self.conversations_tree.heading("title", text="Title")
        self.conversations_tree.column("updated", width=160, anchor="w")
        self.conversations_tree.column("provider", width=110, anchor="center")
        self.conversations_tree.column("title", width=500, anchor="w")
        self.conversations_tree.grid(row=0, column=0, sticky="nsew")
        self.conversations_tree.bind("<<TreeviewSelect>>", self.on_conversation_selected)
        conversation_scroll = ttk.Scrollbar(conversation_container, orient=tk.VERTICAL, command=self.conversations_tree.yview)
        conversation_scroll.grid(row=0, column=1, sticky="ns")
        self.conversations_tree.configure(yscrollcommand=conversation_scroll.set)

        action_card = tk.Frame(shell, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER, padx=18, pady=18)
        action_card.grid(row=1, column=2, sticky="nsew")
        action_card.columnconfigure(0, weight=1)
        action_card.rowconfigure(3, weight=1)

        tk.Label(
            action_card,
            text="Transfer Actions",
            bg=CARD_BG,
            fg=TEXT_PRIMARY,
            font=ui_font(12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            action_card,
            text="Target Account",
            bg=CARD_BG,
            fg=TEXT_SECONDARY,
            font=ui_font(10, "bold"),
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=(14, 6))
        self.target_combo = ttk.Combobox(
            action_card,
            textvariable=self.target_account_var,
            values=[],
            style="App.TCombobox",
            state="readonly",
        )
        self.target_combo.grid(row=2, column=0, sticky="ew")

        detail_container = tk.Frame(action_card, bg=TABLE_BG, highlightthickness=1, highlightbackground=BORDER)
        detail_container.grid(row=3, column=0, sticky="nsew", pady=(14, 14))
        detail_container.columnconfigure(0, weight=1)
        detail_container.rowconfigure(0, weight=1)

        self.detail_text = tk.Text(
            detail_container,
            wrap="word",
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
        self.detail_text.grid(row=0, column=0, sticky="nsew")
        detail_scroll = ttk.Scrollbar(detail_container, orient=tk.VERTICAL, command=self.detail_text.yview)
        detail_scroll.grid(row=0, column=1, sticky="ns")
        self.detail_text.configure(yscrollcommand=detail_scroll.set, state=tk.DISABLED)

        action_buttons = tk.Frame(action_card, bg=CARD_BG)
        action_buttons.grid(row=4, column=0, sticky="ew")
        action_buttons.columnconfigure(0, weight=1)
        action_buttons.columnconfigure(1, weight=1)

        self.refresh_button = ttk.Button(
            action_buttons,
            text="Refresh View",
            style="Secondary.TButton",
            command=self.reload_data,
        )
        self.refresh_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.assign_button = ttk.Button(
            action_buttons,
            text="Assign Selected To Target",
            style="Secondary.TButton",
            command=self.assign_selected_to_target,
        )
        self.assign_button.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        bottom_buttons = tk.Frame(action_card, bg=CARD_BG)
        bottom_buttons.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        bottom_buttons.columnconfigure(0, weight=1)
        bottom_buttons.columnconfigure(1, weight=1)

        self.copy_button = ttk.Button(
            bottom_buttons,
            text="Copy Selected Conversations",
            style="Primary.TButton",
            command=self.copy_selected_conversations,
        )
        self.copy_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.close_button = ttk.Button(
            bottom_buttons,
            text="Close",
            style="Secondary.TButton",
            command=self.close,
        )
        self.close_button.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        footer = tk.Label(
            shell,
            textvariable=self.status_var,
            bg=APP_BG,
            fg=TEXT_SECONDARY,
            font=ui_font(10),
            anchor="w",
            justify="left",
        )
        footer.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(16, 0))

        self.set_detail_text("Select a source account and one or more conversations to inspect and copy.")

    def set_busy(self, busy: bool, status: str) -> None:
        self.busy = busy
        self.status_var.set(status)
        widget_state = tk.DISABLED if busy else tk.NORMAL
        button_state = tk.DISABLED if busy else tk.NORMAL
        self.source_listbox.configure(state=widget_state)
        self.conversations_tree.configure(selectmode="none" if busy else "extended")
        self.target_combo.configure(state="disabled" if busy else "readonly")
        self.refresh_button.configure(state=button_state)
        self.assign_button.configure(state=button_state)
        self.copy_button.configure(state=button_state)
        self.close_button.configure(state=button_state)
        self.update_action_buttons()

    def reload_data(
        self,
        preferred_source: str | None = None,
        preferred_thread_ids: list[str] | None = None,
    ) -> None:
        try:
            profiles, conversations = load_transfer_view(
                self.codex_home,
                self.workdir,
                self.accounts_root,
            )
        except ConversationTransferError as exc:
            messagebox.showerror("Transfer unavailable", str(exc), parent=self.root)
            self.close()
            return

        self.account_profiles = profiles
        self.account_index = {profile.name: profile for profile in profiles}
        self.conversations = conversations

        target_names = [profile.name for profile in profiles]
        self.target_combo.configure(values=target_names)
        if target_names:
            if self.target_account_var.get().strip() not in self.account_index:
                self.target_account_var.set(target_names[0])
        else:
            self.target_account_var.set("")

        counts = build_account_counts(profiles, conversations)
        self.source_keys = [profile.name for profile in profiles] + [UNASSIGNED_ACCOUNT]
        self.source_listbox.delete(0, tk.END)
        for key in self.source_keys:
            self.source_listbox.insert(tk.END, f"{key} ({counts.get(key, 0)})")

        if not self.source_keys:
            self.filtered_conversations = []
            self.conversations_tree.delete(*self.conversations_tree.get_children())
            self.set_detail_text("No accounts are available for transfer.")
            self.status_var.set("No switchable accounts are available.")
            self.update_action_buttons()
            return

        selected_key = preferred_source if preferred_source in self.source_keys else self.current_source_key()
        if selected_key not in self.source_keys:
            selected_key = self.source_keys[0]
        index = self.source_keys.index(selected_key)
        self.source_listbox.selection_clear(0, tk.END)
        self.source_listbox.selection_set(index)
        self.source_listbox.see(index)
        self.refresh_filtered_conversations(preferred_thread_ids=preferred_thread_ids)
        self.status_var.set(
            f"Loaded {len(conversations)} local conversation(s) across {len(profiles)} account(s)."
        )

    def current_source_key(self) -> str:
        selection = self.source_listbox.curselection()
        if not selection:
            return UNASSIGNED_ACCOUNT
        return self.source_keys[selection[0]]

    def refresh_filtered_conversations(self, preferred_thread_ids: list[str] | None = None) -> None:
        source_key = self.current_source_key()
        self.filtered_conversations = [
            conversation
            for conversation in self.conversations
            if conversation.assigned_account == source_key
        ]
        self.conversations_tree.delete(*self.conversations_tree.get_children())
        for conversation in self.filtered_conversations:
            self.conversations_tree.insert(
                "",
                tk.END,
                iid=conversation.thread_id,
                values=(
                    conversation.updated_label,
                    conversation.model_provider,
                    short_text(conversation.title, 78),
                ),
            )

        if preferred_thread_ids:
            selected = [thread_id for thread_id in preferred_thread_ids if self.conversations_tree.exists(thread_id)]
            if selected:
                self.conversations_tree.selection_set(selected)
                self.conversations_tree.focus(selected[0])
                self.conversations_tree.see(selected[0])
        if not self.conversations_tree.selection():
            rows = self.conversations_tree.get_children()
            if rows:
                self.conversations_tree.selection_set(rows[0])
                self.conversations_tree.focus(rows[0])
                self.conversations_tree.see(rows[0])

        self.update_detail_from_selection()
        self.update_action_buttons()

    def get_selected_conversations(self) -> list[TransferConversation]:
        selected_ids = set(self.conversations_tree.selection())
        return [conversation for conversation in self.filtered_conversations if conversation.thread_id in selected_ids]

    def get_target_profile(self) -> AccountProfile | None:
        target_name = self.target_account_var.get().strip()
        return self.account_index.get(target_name)

    def update_action_buttons(self) -> None:
        if self.busy:
            self.assign_button.configure(state=tk.DISABLED)
            self.copy_button.configure(state=tk.DISABLED)
            return
        has_selection = bool(self.get_selected_conversations())
        has_target = self.get_target_profile() is not None
        self.assign_button.configure(state=tk.NORMAL if has_selection and has_target else tk.DISABLED)
        self.copy_button.configure(state=tk.NORMAL if has_selection and has_target else tk.DISABLED)

    def on_source_selected(self, _event: tk.Event[tk.Misc]) -> None:
        if self.busy:
            return
        self.refresh_filtered_conversations()

    def on_conversation_selected(self, _event: tk.Event[tk.Misc]) -> None:
        self.update_detail_from_selection()
        self.update_action_buttons()

    def update_detail_from_selection(self) -> None:
        selected = self.get_selected_conversations()
        if not selected:
            self.set_detail_text("Select one or more conversations to inspect their details.")
            return

        if len(selected) > 1:
            lines = [
                f"Selected conversations: {len(selected)}",
                f"Source group:           {self.current_source_key()}",
                "",
                "Titles:",
            ]
            lines.extend(f"- {conversation.title}" for conversation in selected[:12])
            if len(selected) > 12:
                lines.append("...")
            self.set_detail_text("\n".join(lines))
            return

        conversation = selected[0]
        lines = [
            f"Thread ID:         {conversation.thread_id}",
            f"Assigned account:  {conversation.assigned_account}",
            f"Assignment source: {conversation.assignment_source}",
            f"Provider:          {conversation.model_provider}",
            f"Updated:           {conversation.updated_label}",
            f"Rollout path:      {conversation.rollout_path}",
            "",
            "Preview:",
            conversation.preview,
        ]
        self.set_detail_text("\n".join(lines))

    def set_detail_text(self, text: str) -> None:
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete("1.0", tk.END)
        self.detail_text.insert("1.0", text)
        self.detail_text.configure(state=tk.DISABLED)

    def assign_selected_to_target(self) -> None:
        selected = self.get_selected_conversations()
        target_profile = self.get_target_profile()
        if not selected or target_profile is None:
            return

        assign_threads_to_account(
            [conversation.thread_id for conversation in selected],
            target_profile.name,
        )
        self.reload_data(
            preferred_source=target_profile.name,
            preferred_thread_ids=[conversation.thread_id for conversation in selected],
        )
        self.status_var.set(
            f"Assigned {len(selected)} conversation(s) to {target_profile.name}."
        )

    def copy_selected_conversations(self) -> None:
        selected = self.get_selected_conversations()
        target_profile = self.get_target_profile()
        source_key = self.current_source_key()

        if not selected:
            messagebox.showerror("Copy failed", "Select at least one conversation first.", parent=self.root)
            return
        if target_profile is None:
            messagebox.showerror("Copy failed", "Select a target account first.", parent=self.root)
            return
        if source_key == target_profile.name:
            messagebox.showerror("Copy failed", "Source and target account cannot be the same.", parent=self.root)
            return
        if not target_profile.provider:
            messagebox.showerror(
                "Copy failed",
                f"Cannot determine the target provider for account '{target_profile.name}'.",
                parent=self.root,
            )
            return

        prompt = (
            f"Copy {len(selected)} conversation(s) from '{source_key}' to '{target_profile.name}'?\n\n"
            "The source conversations will be kept unchanged. The target account will receive new copies."
        )
        if not messagebox.askyesno("Confirm copy", prompt, parent=self.root):
            return

        self.set_busy(True, f"Copying {len(selected)} conversation(s) to {target_profile.name}...")

        def worker() -> None:
            restart_status = "skipped"
            restart_error = ""
            try:
                result = copy_conversations_to_account(
                    self.codex_home,
                    selected,
                    target_profile,
                )
                if self.app.get_active_account_name() == target_profile.name:
                    restart_status, restart_error = restart_codex_desktop_app()
            except Exception as exc:  # noqa: BLE001
                error_message = str(exc)
                self.root.after(
                    0,
                    lambda message=error_message: self.finish_copy(
                        None,
                        message,
                        "",
                        "",
                    ),
                )
                return

            self.root.after(
                0,
                lambda: self.finish_copy(
                    result,
                    None,
                    restart_status,
                    restart_error,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def finish_copy(
        self,
        result: ConversationCopyResult | None,
        error_message: str | None,
        restart_status: str,
        restart_error: str,
    ) -> None:
        self.set_busy(False, "Ready.")
        if error_message:
            messagebox.showerror("Copy failed", error_message, parent=self.root)
            self.status_var.set("Conversation copy failed.")
            return
        if result is None:
            messagebox.showerror("Copy failed", "No copy result was returned.", parent=self.root)
            self.status_var.set("Conversation copy failed.")
            return

        self.reload_data(
            preferred_source=result.target_account,
            preferred_thread_ids=result.imported_thread_ids,
        )
        self.app.handle_transfer_complete(result, restart_status, restart_error)

        lines = [
            f"Target account:   {result.target_account}",
            f"Copied sessions:  {result.imported_count}",
        ]
        if restart_status == "restarted":
            lines.append("Desktop app:      Codex App was restarted.")
        elif restart_status == "not_running":
            lines.append("Desktop app:      Codex App was not running.")
        elif restart_status == "failed":
            lines.append("Desktop app:      Automatic restart failed.")
            if restart_error:
                lines.append(f"Restart warning:  {restart_error}")
        else:
            lines.append("Desktop app:      restart not required for the current account.")

        messagebox.showinfo("Copy complete", "\n".join(lines), parent=self.root)
        self.status_var.set(
            f"Copied {result.imported_count} conversation(s) to {result.target_account}."
        )

    def close(self) -> None:
        self.app.transfer_dialog = None
        if self.root.winfo_exists():
            self.root.destroy()

