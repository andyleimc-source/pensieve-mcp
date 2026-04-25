# pensieve-mcp

**Apple Silicon installer + Claude Code MCP integration for [arkohut/pensieve](https://github.com/arkohut/pensieve).**

Turn your Mac's screen into a searchable, local-first memory — and let Claude Code CLI query it directly.

让你的 Mac 屏幕变成本地可搜的"记忆库"，Claude Code CLI 可直接调用。

---

## English

### What is this?

[Pensieve](https://github.com/arkohut/pensieve) (the `memos` CLI) is an open-source, local-first screen-memory tool: it takes screenshots every few seconds, OCRs them, embeds them, and lets you search your screen history offline.

This repo is a **thin installer kit** on top of it, for Apple Silicon Macs, that adds:

1. **A one-command installer** that pins the correct dependency versions so it actually works (upstream breaks with `transformers>=5` — see [troubleshooting](docs/troubleshooting.md)).
2. **An MCP server** so Claude Code CLI can search your screen history and answer questions like *"what did I work on this afternoon?"* without needing any external LLM API key.
3. **A launchd-based retention policy** (default: delete screenshots older than 90 days) — Pensieve has none built in.

### Why would I want this?

- "Where was that Supabase billing screen I saw last week?"
- "Summarize everything I read about X today."
- "I was in a Figma file yesterday around 3pm — which one?"

All answered locally, from your own screen history, through your existing Claude Code subscription — no DeepSeek / OpenAI / Anthropic API key needed beyond what Claude Code already uses.

### Prerequisites

- macOS (Apple Silicon strongly recommended; Intel untested)
- [Homebrew](https://brew.sh)
- [`uv`](https://docs.astral.sh/uv/) — `brew install uv`
- [Claude Code CLI](https://docs.anthropic.com/claude-code) — `claude` on your PATH

### Install

```bash
git clone https://github.com/andyleimc-source/pensieve-mcp.git
cd pensieve-mcp
./install.sh
```

The installer will:
1. Install `memos` via `uv tool install memos --with "transformers<5"`
2. Run `memos init` and `memos start`
3. Open System Settings for you to grant Screen Recording permission
4. Install a daily LaunchAgent that prunes screenshots older than `RETAIN_DAYS` (default 90)
5. Register the MCP server with Claude Code (user scope)

To change retention: `RETAIN_DAYS=30 ./install.sh`.

### Verify

```bash
claude mcp list                 # expect: pensieve ✓ Connected
memos ps                        # serve + record + watch all Running
open http://localhost:8839      # Pensieve Web UI
```

Then in a **new** Claude Code session, ask:

> "What did I work on in the last 20 minutes? Use pensieve to check."

### MCP tools exposed

The MCP server (`scripts/pensieve-mcp.py`) exposes three tools to Claude Code:

| Tool | Purpose |
|---|---|
| `search_screenshots(query, limit?, app?)` | Semantic + keyword search. Returns up to `limit` hits with id, timestamp, app/window, and a ~800-char OCR snippet. |
| `get_screenshot(entity_id)` | Full details + full OCR text for a single screenshot id. |
| `health()` | Ping the Pensieve REST API. |

### Uninstall

```bash
./uninstall.sh
```

Removes the MCP registration, LaunchAgent, and (optionally) the `memos` tool. Your `~/.memos/` data directory is left alone — delete it yourself if you want.

### Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md). The most common issue: upstream Pensieve ships without a hard pin on `transformers`, so `uv` resolves to 5.x, which breaks `/api/search`. This kit pins it for you.

### Credits

- Upstream: [arkohut/pensieve](https://github.com/arkohut/pensieve) — all the heavy lifting (capture, OCR, embedding, REST API, Web UI) is theirs.
- This repo: install wrapper + MCP server.

License: MIT.

---

## 中文

### 这是什么？

[Pensieve](https://github.com/arkohut/pensieve)（命令叫 `memos`）是一个开源的本地屏幕记忆工具：每隔几秒自动截屏 → OCR → 向量化 → 入本地库，可离线搜索"我之前在屏幕上看到过什么"。

本仓库是它的 **Apple Silicon 薄安装套件**，补齐了三件事：

1. **一键安装脚本**：固定正确依赖版本让它跑起来（上游在 `transformers>=5` 时会挂，详见 [troubleshooting](docs/troubleshooting.md)）
2. **MCP server**：让 Claude Code CLI 直接查你的屏幕历史，回答"我下午都在干嘛"这种问题。**不需要配 DeepSeek/OpenAI 的 key**，用你订阅里的 Claude 就行
3. **launchd 自动保留策略**：默认 90 天外的截图每天凌晨自动清理。Pensieve 官方没有这个功能

### 为什么要装

- "上周看到的那张 Supabase 账单截图在哪？"
- "今天关于 X 的内容我都看了啥，总结一下"
- "昨天下午三点我在 Figma 里打开的是哪个文件？"

全部**本地检索**，通过你已有的 Claude Code 订阅回答——不需要额外 API key、不需要把数据发到第三方 LLM。

### 前置条件

- macOS（强烈推荐 Apple Silicon；Intel 未测试）
- [Homebrew](https://brew.sh)
- [`uv`](https://docs.astral.sh/uv/)：`brew install uv`
- [Claude Code CLI](https://docs.anthropic.com/claude-code)：`claude` 在 PATH 里

### 安装

```bash
git clone https://github.com/andyleimc-source/pensieve-mcp.git
cd pensieve-mcp
./install.sh
```

脚本会：
1. `uv tool install memos --with "transformers<5"` 装好 Pensieve（带正确 pin）
2. `memos init` + `memos start`
3. 帮你打开"系统设置→隐私与安全性→屏幕录制"去授权
4. 装一个每天凌晨跑的 LaunchAgent，清理 `RETAIN_DAYS`（默认 90）天外的截图
5. 把 MCP server 注册到 Claude Code（user scope）

要改保留天数：`RETAIN_DAYS=30 ./install.sh`。

### 验证

```bash
claude mcp list                 # 看到 pensieve ✓ Connected
memos ps                        # serve + record + watch 都是 Running
open http://localhost:8839      # Pensieve Web UI
```

然后在**新**的 Claude Code 会话里问：

> "帮我看看最近 20 分钟我都在干嘛，用 pensieve 查一下"

### MCP 暴露的工具

| 工具 | 作用 |
|---|---|
| `search_screenshots(query, limit?, app?)` | 语义 + 关键词搜索。返回 id、时间、app/window、截断后的 OCR 片段 |
| `get_screenshot(entity_id)` | 拿某张截图的完整信息（含完整 OCR 文本） |
| `health()` | 探活 |

### 卸载

```bash
./uninstall.sh
```

移除 MCP 注册、LaunchAgent，可选卸载 `memos`。`~/.memos/` 数据目录**不会**被删——如果你真的想删，自己 `rm -rf ~/.memos`。

### 常见问题

看 [docs/troubleshooting.md](docs/troubleshooting.md)。最常见的坑：上游没 pin `transformers`，uv 装到 5.x 会把 `/api/search` 搞挂。本套件帮你 pin 好了。

### 致谢

- 上游：[arkohut/pensieve](https://github.com/arkohut/pensieve) — 核心能力（截屏、OCR、embedding、REST API、Web UI）都是它的功劳
- 本仓库：安装 wrapper + MCP server

License: MIT。
