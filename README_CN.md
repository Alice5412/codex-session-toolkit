# Codex 会话工具箱

English version: [README.md](./README.md)

一个面向 Windows 的 Codex 工具箱，用于按当前工作目录浏览本地 Codex Desktop / Codex CLI 对话、从任意用户消息执行分支、切换本地账户配置，并在多个账户之间传递会话。项目现在只保留本地 Web UI 作为图形前端，同时保留 CLI 用于直接操作。

## 功能

- 按工作目录浏览和筛选本地 Codex 会话
- 从任意用户消息执行 `fork + rollback`
- 切换本地 Codex 账户配置
- 在多个本地账户之间传递或复制会话
- 使用本地 Web UI 或 CLI 工具

## 要求

- Windows
- Python 3.10+
- 已安装 Codex Desktop / Codex CLI
- 本地存在可访问的 Codex 会话目录

本项目只使用 Python 标准库。

## 快速开始

第一次使用时，可以先运行：

```powershell
.\add_to_user_path.cmd
```

启动本地 Web UI：

```powershell
codex-toolkit --webui
```

或者使用专用 Web 启动器：

```powershell
codex-toolkit-web
```

如果项目目录还没有加入 `PATH`，可以先这样运行：

```powershell
.\codex-toolkit-web.cmd
```

列出可切换账户：

```powershell
codex-toolkit --list-accounts
```

切换到账户 `user1`：

```powershell
codex-toolkit --switch-account user1
```

查看当前工作目录的传递分组视图：

```powershell
codex-toolkit --list-transfer-view --accounts-root D:\path\to\accounts
```

手动把会话归属到账户：

```powershell
codex-toolkit --assign-conversations-to user1 --transfer-sources THREAD_ID_1 THREAD_ID_2
```

把会话复制到另一个账户：

```powershell
codex-toolkit --copy-conversations-to api --transfer-sources THREAD_ID_1 THREAD_ID_2
```

## 交互形态

- Web UI 是唯一的图形前端，由本地 Python 后端提供服务
- CLI 继续保留，用于交互式浏览和直接执行账户、传递操作
- Tk 桌面 GUI 已从代码库中移除

## 项目结构

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

## 模块职责

- `fork_cli.py`：主 CLI 入口，负责工作区浏览、分支、账户操作和 Web UI 启动
- `transfer_cli.py`：非交互式会话传递命令，负责查看、归属和复制会话
- `conversation_transfer.py`：传递领域逻辑，包括 provider 推断、会话分组、归属分类和复制流程
- `app_state.py`：基于本地 JSON 的工作目录状态和账号-会话映射管理
- `webui/api.py`：面向本地 HTTP 的服务层，复用现有会话、账号切换、传递和 fork 逻辑
- `webui/server.py`：本地 Web 服务器和浏览器静态资源入口
- `session_tool.py`：rollout 打包、导入导出和线程索引维护

## 说明

- 不会修改原线程
- 只会对新创建的线程执行 rollback
- fork 完成后，工具会先尝试通过 `thread/resume` 自动把新线程加载进 Codex
- 如果 Codex Desktop 正在运行，工具还会自动重启 App 来刷新线程列表
- 账户切换与 fork 共用同一个 `codex_home`，只覆盖其中的 `config.toml` 和 `auth.json`
- 目标文件在覆盖前会备份到 `account-switch-backups\...`
- 账户源目录会优先从项目内 `.\accounts` 查找；如果不存在，再兼容同级目录 `..\codex-user-change`
- 工作目录状态和会话归属映射统一保存在 `%APPDATA%\codex-any-node-fork`

## License

Licensed under the MIT License.
