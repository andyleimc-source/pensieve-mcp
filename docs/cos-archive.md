# Optional: Cloud Archive to Tencent COS

When enabled, screenshots older than `RETAIN_DAYS` (default 90) are uploaded to a Tencent Cloud COS bucket instead of being deleted. Search and OCR keep working through the MCP because the SQLite metadata is preserved; only the image bytes move to the cloud.

Cost (rough, for personal use): ~¥1-3/month per 100GB at Archive storage class.

---

## What gets created on the cloud side

You create these once in the Tencent Cloud console:

1. **A private COS bucket** (e.g. `pensieve-archive-1234567890`)
   - Region: any; pick the one closest to you (`ap-shanghai` works well in mainland China)
   - Access: **Private R/W** — never public
   - Storage class: **Standard**
   - Lifecycle rule: **transition to Archive after 30 days** (saves ~67% vs Standard; min retention 90 days, retrieval needs a "thaw" of a few minutes)
2. **A custom CAM policy** scoped only to that bucket:
   ```json
   {
     "version": "2.0",
     "statement": [{
       "effect": "allow",
       "action": [
         "cos:PutObject", "cos:PostObject", "cos:GetObject",
         "cos:HeadObject", "cos:DeleteObject",
         "cos:GetBucket", "cos:HeadBucket",
         "cos:InitiateMultipartUpload", "cos:UploadPart",
         "cos:CompleteMultipartUpload", "cos:AbortMultipartUpload",
         "cos:ListMultipartUploads", "cos:ListParts"
       ],
       "resource": [
         "qcs::cos:<REGION>:uid/<APPID>:<BUCKET>/*",
         "qcs::cos:<REGION>:uid/<APPID>:<BUCKET>"
       ]
     }]
   }
   ```
3. **A CAM sub-account** with **programmatic access only** (no console login), attach the policy above. Save the `SecretId` / `SecretKey` it gives you — they're shown only once.

---

## How it works

```
~/.memos/screenshots/<date>/<file>.webp     (local, 0–RETAIN_DAYS days)
                ↓ daily 03:30, find -mtime +RETAIN_DAYS
                ↓ coscmd upload then HEAD-verify
                ↓ rm local file
cos://<bucket>/<DEVICE>/<date>/<file>.webp  (cloud, archived; 30d → Archive class)
```

`<DEVICE>` is the prefix you set in `cos.env`. Use a unique value per machine (e.g., `personal-mac` / `work-mac`) so multiple devices can share one bucket without colliding.

The SQLite database (`~/.memos/database.db`) is **not touched**. DB rows for archived files stay intact, which is why MCP search still finds them. The local file just no longer exists.

⚠️ **Critical**: with archive enabled, **never run `memos scan`**. Scan reconciles DB rows against on-disk files, and would delete the rows for archived items, killing search.

## MCP behavior with archived items

`search_screenshots` keeps returning archived items as normal hits, with an extra `archive_status: "archived"` field.

`get_screenshot(id)` returns `archive_status` and `cos_key` (no automatic download).

`download_archived(id)` fetches the image to a temp file and returns the path — call this only when you actually want to view the archived image.

## Bring-up

The installer (`install.sh` step 6) prompts you for COS credentials and writes `~/.config/pensieve-mcp/cos.env` (chmod 600). You can also create that file manually using `config/cos.env.example` as a template, then re-run `./install.sh`.

## Restoring an archived screenshot manually

```bash
source ~/.config/pensieve-mcp/cos.env
coscmd download "/${PENSIEVE_DEVICE}/<date>/<filename>" /tmp/restored.webp
open /tmp/restored.webp
```

If the object has already transitioned to Archive class, you'll need to "restore" it first via the COS console or `coscmd restore`. Standard tier (first 30 days) is instant.

## Multi-device sharing one bucket

Two Macs can share the same bucket; just give them different `PENSIEVE_DEVICE` values:

```
cos://pensieve-archive-1234567890/
    ├── personal-mac/20260115/...
    └── work-mac/20260115/...
```

Each device has its own SQLite locally, so search results from "this Mac" are still local-only — see the upcoming multi-device aggregator (M5 in the project plan) for cross-device search.
