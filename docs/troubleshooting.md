# Troubleshooting

## `/api/search` returns 500: `No module named 'transformers.onnx'`

**Cause**: upstream Pensieve doesn't pin `transformers`. `uv` resolves to `transformers>=5`, which removed the `transformers.onnx` submodule that Pensieve's embedding code still imports. Web UI keyword search may look fine (falls back to FTS), but MCP / semantic API is dead.

**Fix**:
```bash
memos stop
uv tool install memos --with "transformers<5" --force
memos start
```

`install.sh` in this repo applies the pin automatically.

## Screenshots not appearing in `~/.memos/screenshots/`

macOS Screen Recording permission is missing.

1. System Settings → Privacy & Security → Screen Recording
2. Enable your terminal app (Terminal.app / iTerm / Warp / etc.)
3. Restart the terminal; re-run `memos start`

## Web UI shows results but MCP returns nothing

Same root cause as the `transformers.onnx` bug above. Apply the pin.

## LaunchAgent doesn't fire

```bash
launchctl list | grep pensieve                # should show com.user.pensieve.prune
cat ~/.memos/prune.stderr.log                 # errors
cat ~/.memos/prune.log                        # normal output
```

If missing, rerun `./install.sh` — step 6 reinstalls the LaunchAgent.

## Port 8839 already in use

Edit `~/.memos/config.yaml`, change `server_port`, then restart `memos`. Also update `PENSIEVE_BASE_URL` env in the MCP registration:

```bash
claude mcp remove pensieve -s user
claude mcp add pensieve -s user --env PENSIEVE_BASE_URL=http://localhost:8840 -- /path/to/pensieve-mcp.py
```

## Intel Mac (x86_64)

Untested. `uv` should resolve x86 torch wheels fine, but Paddle OCR behavior on Intel hasn't been verified for this kit.

## Disk usage growing too fast

Upstream estimates ~8GB/month at 2560x1440, 10h/day. Tune:
- Capture interval: `~/.memos/config.yaml` → `record_interval`
- Dedup aggressiveness: lower `--threshold` for `memos record` (default 4)
- Retention: `RETAIN_DAYS=30 ./install.sh` to re-provision LaunchAgent at 30 days

## Archive: MCP search returns no results after archiving

You probably ran `memos scan` after archive started, which deleted the DB rows for archived files. Recovery:

1. Stop running `memos scan`
2. Re-add metadata only by re-uploading? No — once DB rows are gone, OCR/vector are gone with them. You'd have to re-OCR from the archived images, which means downloading them all back, then `memos scan`. Cheaper to live with the loss going forward.

## Archive: `coscmd upload` fails with permission errors

Re-check the CAM policy resource ARN format — must be exactly:

```
qcs::cos:<region>:uid/<APPID>:<bucket>/*
qcs::cos:<region>:uid/<APPID>:<bucket>
```

Region must match the bucket region. APPID is the trailing number in the bucket name. Both lines (with and without `/*`) are needed — `GetBucket` (list) requires the bucket-level resource.

## Archive: object is "thawing" / `403 RestoreObject`

The lifecycle rule transitions to Archive after 30 days, where retrieval requires restore (a few minutes to hours). Use `coscmd restore` or the console to start a restore, wait, then download. To avoid this, change the lifecycle rule to "low-frequency" (cheaper than Standard but instant retrieval).

## `claude mcp list` shows pensieve but `✗ Failed to connect`

```bash
~/Documents/pensieve-mcp/scripts/pensieve-mcp.py   # should not crash; Ctrl-C out
curl http://localhost:8839/api/health              # should return {"status":"ok"}
```

If the script itself errors on startup, usually means `uv` can't resolve deps — check you have a recent `uv` (`brew upgrade uv`).
