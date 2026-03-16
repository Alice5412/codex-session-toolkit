# Account Sources

Put one subdirectory per switchable Codex account in this folder.

Each account directory must contain exactly these two files:

- `config.toml`
- `auth.json`

Example:

```text
accounts
├─ user1
│  ├─ config.toml
│  └─ auth.json
└─ api
   ├─ config.toml
   └─ auth.json
```

The GUI and CLI will copy those two files into the target Codex home, which defaults to `C:\Users\Administrator\.codex` on this machine.

This folder is ignored by git except for this README and `.gitkeep`, so local credential files are not committed by default.
