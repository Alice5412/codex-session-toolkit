# Codex Session Toolkit

中文说明: [README_CN.md](./README_CN.md)

A Windows toolkit for browsing local Codex Desktop / Codex CLI conversations by working directory, forking from any user turn, switching local account profiles, and transferring conversations between accounts. It includes both an interactive CLI and a graphical GUI.

## Features

- Browse and filter local Codex conversations by workspace
- Fork from any user turn with `fork + rollback`
- Switch local Codex account profiles
- Transfer or copy conversations between local accounts
- Use either the interactive CLI or the graphical GUI

## Requirements

- Windows
- Python 3.10+
- Codex Desktop or Codex CLI installed
- Access to a local Codex sessions directory

This project uses only the Python standard library.

## Quick Start

For first-time setup, beginners can run:

```powershell
.\add_to_user_path.cmd
```

Run:

```powershell
codex-toolkit -ls
```

Launch the GUI:

```powershell
codex-toolkit --gui
```

On Windows this path prefers launching the GUI as a detached no-console process.

Or use the dedicated launcher:

```powershell
codex-toolkit-gui
```

The launcher prefers `pythonw` so the GUI does not keep an extra `cmd` window open.

The legacy launchers `fork.cmd` and `fork-gui.cmd` are kept as compatibility shims.

If the project directory is not in `PATH`, use:

```powershell
.\codex-toolkit.cmd -ls
```

Or run the script directly:

```powershell
python .\scripts\fork_cli.py -ls
```

Or:

```powershell
python .\scripts\fork_gui.py
```

List switchable accounts:

```powershell
codex-toolkit --list-accounts
```

Switch to a specific account:

```powershell
codex-toolkit --switch-account user1
```

If your account source folders live outside the default location, pass:

```powershell
codex-toolkit --list-accounts --accounts-root D:\path\to\accounts
```

Inspect transfer groups for the current workdir:

```powershell
codex-toolkit --list-transfer-view --accounts-root D:\path\to\accounts
```

Assign conversations to an account in the transfer mapping:

```powershell
codex-toolkit --assign-conversations-to user1 --transfer-sources THREAD_ID_1 THREAD_ID_2
```

Copy conversations to another account:

```powershell
codex-toolkit --copy-conversations-to api --transfer-sources THREAD_ID_1 THREAD_ID_2
```

## Controls

- `↑ / ↓`: move selection
- `Enter`: confirm
- `Backspace`: go back
- `q`: quit

## GUI

- The GUI uses a modern dark card-based layout with a workspace rail and split content panels
- `Workdir` uses an editable dropdown that remembers recently selected or used work directories
- A recent-workdirs list is shown on the left side for one-click switching
- The GUI prefers the last remembered work directory on the next launch
- You can enable “minimize to tray when closing” so the close button hides the window instead of exiting
- `Refresh` button: reload the conversation list for the selected `codex_home` and `workdir`
- Changing `Workdir` triggers an automatic refresh
- The `Account Switcher` panel can browse an accounts root, detect the currently installed profile, and switch with one click
- `F5`: trigger a manual refresh
- Double-click a user message or use `Fork Selected Turn` to create a fork
- After a successful fork, the GUI refreshes the conversation list automatically

After entering a conversation, the tool shows only user messages.
Once a target message is selected, it creates a new thread and rolls that new thread back to the matching turn.

## Project Structure

```text
.
├─ accounts
│  ├─ .gitkeep
│  └─ README.md
├─ add_to_user_path.cmd
├─ codex-toolkit.cmd
├─ codex-toolkit-gui.cmd
├─ fork.cmd
├─ fork-gui.cmd
├─ LICENSE
├─ README.md
├─ README_CN.md
├─ tests
│  └─ test_conversation_transfer.py
└─ scripts
   ├─ account_switcher.py
   ├─ app_state.py
   ├─ conversation_transfer.py
   ├─ desktop_app.py
   ├─ fork_cli.py
   ├─ fork_gui.py
   ├─ gui_theme.py
   ├─ session_tool.py
   ├─ transfer_cli.py
   └─ transfer_dialog.py
```

## Module Roles

- `fork_gui.py`: main dashboard window, workspace browser, account switcher, and fork flow orchestration
- `transfer_dialog.py`: dedicated conversation transfer window and its GUI-only interaction logic
- `fork_cli.py`: primary CLI entrypoint for workspace browsing, forking, and account operations
- `transfer_cli.py`: non-interactive transfer commands for listing, assigning, and copying conversations
- `conversation_transfer.py`: transfer domain logic including provider inference, conversation grouping, ownership classification, and copy workflow
- `app_state.py`: local JSON-backed state management for remembered GUI workdirs and account-session mappings
- `desktop_app.py`: shared Codex Desktop restart helpers
- `gui_theme.py`: shared GUI color and typography constants
- `session_tool.py`: rollout packaging, import/export, and thread index maintenance helpers

## Notes

- The original thread is never modified
- Rollback is applied only to the new thread
- After a fork, the tool attempts to load the new thread into Codex automatically via `thread/resume`
- If Codex Desktop is running, the tool also restarts the app to refresh the thread list
- Account switching uses the same `codex_home` directory and overwrites only `config.toml` and `auth.json`
- Existing target account files are backed up into `account-switch-backups\...` before overwrite
- Account sources are discovered from `.\\accounts` first, then from the sibling `..\\codex-user-change` folder if present
- If the new thread still does not appear, reopen Codex manually
- Local GUI state and transfer mapping are stored under `%APPDATA%\codex-any-node-fork` via a shared state layer
- GUI theme constants, desktop restart logic, transfer CLI commands, and the transfer dialog now live in separate modules to reduce maintenance overhead

## License

Licensed under the MIT License.
