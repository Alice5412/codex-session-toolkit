# Codex Session Toolkit

中文说明: [README_CN.md](./README_CN.md)

A Windows toolkit for browsing local Codex Desktop / Codex CLI conversations by working directory, forking from any user turn, switching local account profiles, and transferring conversations between accounts. The project now keeps a local Web UI as its only graphical frontend, with CLI utilities retained for direct operations.

## Features

- Browse and filter local Codex conversations by workspace
- Fork from any user turn with `fork + rollback`
- Switch local Codex account profiles
- Transfer or copy conversations between local accounts
- Use the local Web UI or CLI utilities

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

Launch the local Web UI:

```powershell
codex-toolkit --webui
```

Or use the dedicated Web launcher:

```powershell
codex-toolkit-web
```

If the project directory is not in `PATH`, use:

```powershell
.\codex-toolkit-web.cmd
```

List switchable accounts:

```powershell
codex-toolkit --list-accounts
```

Switch to a specific account:

```powershell
codex-toolkit --switch-account user1
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

## Interfaces

- The Web UI is the only graphical frontend and is served locally from the bundled Python backend
- The CLI remains available for interactive browsing and direct account or transfer operations
- The Tk desktop GUI has been removed from the codebase

## Project Structure

```text
.
├─ accounts
│  ├─ .gitkeep
│  └─ README.md
├─ add_to_user_path.cmd
├─ codex-toolkit.cmd
├─ codex-toolkit-web.cmd
├─ fork.cmd
├─ LICENSE
├─ README.md
├─ README_CN.md
├─ tests
│  ├─ test_conversation_transfer.py
│  └─ test_webui_api.py
└─ scripts
   ├─ account_switcher.py
   ├─ app_state.py
   ├─ conversation_transfer.py
   ├─ desktop_app.py
   ├─ fork_cli.py
   ├─ session_tool.py
   ├─ transfer_cli.py
   └─ webui
      ├─ __init__.py
      ├─ api.py
      ├─ server.py
      └─ assets
```

## Module Roles

- `fork_cli.py`: primary CLI entrypoint for workspace browsing, forking, account operations, and Web UI launch
- `transfer_cli.py`: non-interactive transfer commands for listing, assigning, and copying conversations
- `conversation_transfer.py`: transfer domain logic including provider inference, conversation grouping, ownership classification, and copy workflow
- `app_state.py`: local JSON-backed state management for remembered workdirs and account-session mappings
- `webui/api.py`: local HTTP-facing service layer for sessions, account switching, transfer, and fork actions
- `webui/server.py`: local Web server and static asset delivery for the browser UI
- `session_tool.py`: rollout packaging, import/export, and thread index maintenance helpers

## Notes

- The original thread is never modified
- Rollback is applied only to the new thread
- After a fork, the tool attempts to load the new thread into Codex automatically via `thread/resume`
- If Codex Desktop is running, the tool also restarts the app to refresh the thread list
- Account switching uses the same `codex_home` directory and overwrites only `config.toml` and `auth.json`
- Existing target account files are backed up into `account-switch-backups\...` before overwrite
- Account sources are discovered from `.\accounts` first, then from the sibling `..\codex-user-change` folder if present
- Workspace state and transfer mapping are stored under `%APPDATA%\codex-any-node-fork`

## License

Licensed under the MIT License.
