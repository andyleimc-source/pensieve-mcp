# pensieve-mcp

**Apple Silicon installer + Claude Code MCP integration for [arkohut/pensieve](https://github.com/arkohut/pensieve).**

Turn your Mac's screen into a searchable, local-first memory — and let Claude Code CLI query it directly.

让你的 Mac 屏幕变成本地可搜的"记忆库"，Claude Code CLI 可直接调用。

---

## English

### Features

- **One-command installer** — pins the right `transformers` version so `/api/search` actually works on Apple Silicon, runs `memos init/start`, walks you through Screen Recording permission, and registers the MCP with Claude Code.
- **Claude Code MCP integration** — ask *"what did I work on this afternoon?"* in Claude Code and it queries your local screen history directly. No external LLM key needed beyond Claude Code.
- **Activity summary tool** — aggregate a time range into top apps + sample windows + hit count, so Claude can answer "what did I do today?" without bloating its context with raw OCR.
- **Pause / resume capture** — tell Claude *"pause for 2 hours"* or *"pause until 9am tomorrow"*; survives sleep and reboot via launchd.
- **Power modes** — switch capture cadence between `eco` (slow, low CPU) and `performance` (fast, more detail) on the fly.
- **Daily retention policy** — launchd job prunes screenshots older than `RETAIN_DAYS` (default 90). Pensieve has none built in.
- **Optional cloud archive (Tencent COS)** — instead of deleting old screenshots, upload image bytes to your private COS bucket. OCR/embeddings stay local, so search still works; ~¥1–3/month per 100GB.
- **Optional multi-device aggregation** — set `PENSIEVE_PEERS` to your other Macs (e.g. home + work over Tailscale) and one MCP transparently searches across all of them. Results tagged with `source=<hostname>`.
- **Optional Bearer-token auth** — drop a token into `~/.config/pensieve-mcp/auth.env` to authenticate every request to local Pensieve and to peers. Required if you expose `:8839` to a LAN or Tailnet.

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

The MCP server (`scripts/pensieve-mcp.py`) exposes these tools to Claude Code:

| Tool | Purpose |
|---|---|
| `search_screenshots(query, limit?, app?)` | Semantic + keyword search. Returns up to `limit` hits with id, timestamp, app/window, and a ~800-char OCR snippet. Each hit carries `archive_status: local \| archived \| unknown`. |
| `get_screenshot(entity_id)` | Full details + full OCR text for a single screenshot id. For archived items, returns `cos_key` instead of a usable local path. |
| `download_archived(entity_id)` | Pull an archived screenshot from COS to a temp file. Call only when needed. |
| `pause_recording(duration_seconds?, resume_at?)` | Stop screen recording. Optional auto-resume after duration or at ISO timestamp. Survives sleep/reboot. |
| `resume_recording()` | Resume immediately, cancel any scheduled auto-resume. |
| `recording_status()` | Whether record is running, plus any active pause schedule. |
| `activity_summary(start?, end?, top_apps?)` | Aggregate what you worked on in a time range — top apps, sample windows, hit count — without dumping every OCR snippet into Claude's context. |
| `get_power_mode()` / `set_power_mode(mode)` / `toggle_full_power()` | Switch capture cadence between `eco` (slow, low CPU) and `performance` (fast, more detail). Useful when you're about to do something you really want recorded. |
| `health()` | Ping the Pensieve REST API; reports archive configuration too. |

### Optional: cloud archive (Tencent COS)

By default, screenshots older than 90 days are deleted locally. Enable cloud archive during `./install.sh` (step 6) to upload them to a Tencent Cloud COS bucket instead — they stay searchable through the MCP, only the image bytes move to the cloud. ~¥1–3/month per 100GB.

See [docs/cos-archive.md](docs/cos-archive.md) for the full setup (bucket, CAM sub-account, policy) and how it interacts with the MCP.

### Optional: multi-device aggregation

If you run Pensieve on multiple Macs (e.g. home + work) and connect them via [Tailscale](https://tailscale.com), one MCP can transparently search across all of them. Set `PENSIEVE_PEERS` to a comma-separated list of peer base URLs (Tailscale IPs):

```bash
export PENSIEVE_PEERS="http://100.x.y.z:8839,http://100.a.b.c:8839"
```

Search results from peers are tagged with `source=<hostname>`; local hits stay tagged `source=local`. Failures on individual peers are swallowed (you still get local results).

Notes:
- Peers must use the **same** `PENSIEVE_TOKEN` (see below) for auth to work, or all be unauthenticated.
- Peer queries skip httpx's auto-proxy detection (`trust_env=False`), since macOS system proxies (Clash/V2Ray) tend to break Tailscale CGNAT routing.
- Configure on **each** Mac symmetrically and you get bidirectional aggregation — querying either machine searches both.

### Optional: API authentication

By default, Pensieve's REST API on `:8839` is unauthenticated. That's fine for pure localhost use, but **dangerous if you expose it to a LAN or Tailnet** (which `PENSIEVE_PEERS` requires). To enable Bearer-token auth on the MCP side, drop the token into:

```bash
mkdir -p ~/.config/pensieve-mcp
echo 'PENSIEVE_TOKEN=your-long-random-token' > ~/.config/pensieve-mcp/auth.env
chmod 600 ~/.config/pensieve-mcp/auth.env
```

The MCP will send `Authorization: Bearer <token>` on every request to local Pensieve and to any peers. You're responsible for configuring the matching auth check on the Pensieve server side (e.g. via a reverse proxy or a custom middleware) — this kit only handles the **client** side.

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

### 主要特性

- **一键安装**：pin 住 `transformers` 版本（不 pin 上游会装到 5.x 把 `/api/search` 搞挂），自动跑 `memos init/start`，引导授权"屏幕录制"，注册 MCP 到 Claude Code
- **Claude Code MCP 集成**：在 Claude Code 里直接问"我下午都在干嘛"，它会查你本地的屏幕历史回答。**不需要额外 API key**，用你订阅里的 Claude 即可
- **活动汇总工具**：`activity_summary` 把时间段聚合成 top apps + 窗口样本 + 命中数，让 Claude 答"今天我都干了啥"时不会被海量 OCR 撑爆上下文
- **暂停 / 恢复截屏**：直接对 Claude 说"暂停 2 小时""暂停到明早 9 点""暂停一下"都行；通过 launchd 实现，睡眠 / 重启都能正常恢复
- **电源模式切换**：`eco`（慢、省电）和 `performance`（快、记得多）随时切，要专心做某件事且希望被完整记录就拉满
- **每日保留策略**：launchd 每天凌晨清理 `RETAIN_DAYS`（默认 90）天外的截图。Pensieve 官方没有这功能
- **可选云归档（腾讯云 COS）**：超期截图不直接删，传到你私人 COS 桶，OCR 文本和向量留在本地——**搜索照常工作**，只是图片本体上云。100GB 一年 ~¥10-30
- **可选多设备聚合**：`PENSIEVE_PEERS` 配上其他 Mac（比如家 + 公司，Tailscale 互通），一个 MCP 就能跨设备透明搜索，结果带 `source=<hostname>` 标签
- **可选 Bearer token 鉴权**：把 token 放进 `~/.config/pensieve-mcp/auth.env`，所有调本地 Pensieve 和 peer 的请求都自动带 `Authorization` 头。把 `:8839` 暴露到 LAN/Tailnet 时**必须**配

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
| `search_screenshots(query, limit?, app?)` | 语义 + 关键词搜索。返回 id、时间、app/window、截断后的 OCR 片段；每条 hit 带 `archive_status`（local/archived/unknown）|
| `get_screenshot(entity_id)` | 拿某张截图的完整信息（含完整 OCR 文本）；归档对象返回 `cos_key` 指向云端位置 |
| `download_archived(entity_id)` | 按需把归档对象从 COS 拉到本地临时文件 |
| `pause_recording(duration_seconds?, resume_at?)` | 暂停截屏。可选自动恢复（按时长或绝对时间），睡眠/重启都能正常恢复 |
| `resume_recording()` | 立刻恢复截屏，取消计划中的自动恢复 |
| `recording_status()` | 报告 record 进程是否运行 + 当前暂停状态 |
| `activity_summary(start?, end?, top_apps?)` | 聚合时间段内的活动概况——top apps、窗口样本、命中数——避免把每张截图的 OCR 全塞进 Claude 上下文 |
| `get_power_mode()` / `set_power_mode(mode)` / `toggle_full_power()` | 切换截屏节奏：`eco`（慢、省电）/ `performance`（快、记得多）。要专心做某件事且希望它被完整记录时拉满 |
| `health()` | 探活；同时报告归档功能是否启用 |

直接对 Claude 说："暂停截屏 2 小时"、"暂停到明早 9 点"、"暂停一下"（无限期）都能识别。

### 可选：云归档（腾讯云 COS）

默认行为是 90 天外的截图本地直接删。在 `./install.sh` 第 6 步可以选启用云归档——超期截图先传到你私人的 COS 桶再删本地，**搜索照常工作**（OCR 文本和向量在本地 SQLite，没动），只是图片本体上云。100GB 一年 ~¥10-30。

详见 [docs/cos-archive.md](docs/cos-archive.md)：建桶、CAM 子账号、最小权限策略、和 MCP 怎么联动。

### 可选：多设备聚合

如果你在多台 Mac 上都跑了 Pensieve（比如家里 + 公司），又用 [Tailscale](https://tailscale.com) 把它们连起来，一个 MCP 就能透明地跨设备搜。`PENSIEVE_PEERS` 设成逗号分隔的 peer URL 列表（Tailscale IP）：

```bash
export PENSIEVE_PEERS="http://100.x.y.z:8839,http://100.a.b.c:8839"
```

peer 来的结果会带 `source=<hostname>` 标签；本地命中标 `source=local`。某个 peer 抽风也不影响——本地结果照样返回。

说明：
- peer 必须**统一**用同一个 `PENSIEVE_TOKEN`（见下文），或者全部都不带 auth
- peer 查询会跳过 httpx 的自动代理探测（`trust_env=False`），因为 macOS 系统代理（Clash/V2Ray）一般不会路由 Tailscale CGNAT 段，会反 502
- 两台机器**对称**配置就能双向聚合——查任一台都搜两台

### 可选：API 鉴权

Pensieve 的 `:8839` REST API 默认不带鉴权，纯 localhost 用没问题，**但如果你把它暴露到 LAN 或 Tailnet（PENSIEVE_PEERS 要求暴露），裸奔很危险**。MCP 这边支持 Bearer token：

```bash
mkdir -p ~/.config/pensieve-mcp
echo 'PENSIEVE_TOKEN=your-long-random-token' > ~/.config/pensieve-mcp/auth.env
chmod 600 ~/.config/pensieve-mcp/auth.env
```

之后 MCP 调本地 Pensieve 和所有 peer 都会带 `Authorization: Bearer <token>`。**服务端**那一侧的鉴权校验得你自己配（反向代理或自定义中间件），本套件只管 client 端。

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
