# Hugging Face Mirror for WZML-X v3

Native v3-styled Hugging Face mirror plugin. Runs inside the original WZML-X process; the separate `WikioApps/hf` Telegram bot, web server and MongoDB task manager do **not** start.

## Install or upgrade

1. On WZML-X branch `wzv3`, send `/plugins` → **Install → GitHub**.
2. Enter `WikioApps/hf@wzmlx-plugin` and select **hf_uploader**. If already installed, use **Installed → hf_uploader → Update** to fetch v1.1.0; **Reload** alone does not download new code.
3. Approve the `huggingface_hub==0.36.0` dependency if prompted.
4. Configure `HF_TOKEN=hf_...` in the WZML-X **hosting environment**, not Telegram chat; restart the container so the environment change applies.
5. In `/plugins` → **Installed → hf_uploader → Settings**, enter `repo_id` (`username/repo`), `repo_type`, `branch`, `folder`, `private`, and filename collision behavior. Alternatively define `HF_REPO_ID` as a hosting secret.
6. Test by replying to a small Telegram document with `/hfmirror` or sending `/hfmirror https://example.com/file.zip`.

The HF token must have write permission on the target repo. A private repo requires permission for anyone opening its file links.

## Same WZML-X v3 task UI

- **Automatic native status:** running `/hfmirror` immediately registers a WZML-X task and sends the existing **v3 /status panel** without requiring another command.
- **Auto refresh:** WZML-X's standard `STATUS_UPDATE_INTERVAL` controls updates, with the native **Refresh**, **TStats**, pagination and status-filter buttons. Configure `STATUS_UPDATE_INTERVAL` under `/bsettings` if you want more frequent refreshes.
- **Task line:** file name, percentage, processed/total, speed, ETA, engine, input/output modes and native `/cancel_<GID>` shortcut appear through the standard task renderer.
- **Buttons:** an additional, compact WZML-styled start message has **Refresh Status** and **Cancel Transfer** buttons. The native task panel remains owned by WZML-X rather than reimplemented in this plugin.
- **Unified cancel:** the existing built-in `/cancel` works by **replying to the original /hfmirror command** or by `/cancel_<GID>` from the status panel. The button and legacy `/hfcancel TASK_ID` work too.
- **Results:** on completion the bot sends a WZML-styled summary with **Open HF File** and **Repository** buttons. Errors and cancellations use native-style notifications.
- **Progress:** HTTP and Telegram downloads report real downloaded bytes. Hugging Face upload attempts byte-level LFS tracking using the v0.36.0 internal streaming helper. Small Git-backed commits or alternate Hub paths may show **Upload** with indeterminate/zero progress until the commit completes. The integration does not claim that hashing bytes are uploaded bytes.

## Commands

```text
/hfmirror https://example.com/video.mp4
/hfmirror https://example.com/video.mp4 -n "movie.mp4" -p "movies/2026"
/hfmirror             # reply to a Telegram video/document
/hfmirror https://t.me/publicchannel/123
/status               # optional: native WZML-X status, automatically shown
/hfstatus             # optional shortcut to that same native status
/hfcancel TASK_ID      # legacy fallback, native /cancel is preferred
```

Commands are **owner-only** by default because the plugin reads one host-level `HF_TOKEN`.

## Existing boundaries

Supports direct public HTTP(S) files, Telegram file replies, permitted Telegram message links, model/dataset repos, branch creation, HF subfolders and collision policies, basic size/disk safeguards, task cancellation, and local cleanup.

Not yet included: `/mirror -hf` (would require WZML-X core upload router modifications), torrent/magnet downloads, general filehost or yt-dlp extraction, archive selection/extraction, per-user tokens, persistent retry/history and the original separate HF web dashboard.

Cancelling an already-started synchronous HF commit is **best-effort**: local files are retained until the worker stops, and the remote commit may still finish.

Hub accounts and repositories remain subject to [Hugging Face terms and storage policies](https://huggingface.co/docs/hub/storage-limits). HF is not an unlimited replacement for generic file storage.

## Technical details

- Tasks register in WZML-X's `task_dict` and implement the exact `MirrorStatus`/status-widget protocol; removal refreshes/cleans the stock status panel.
- Native inline buttons are declared as owner-only callbacks in `wzml_plugin.yml`.
- Downloaded files are staged under WZML-X's `DOWNLOAD_DIR`, and their temporary folder is removed on success/failure.
- The plugin does not create a second HTTP port, app process, Telegram client or MongoDB collection.
- WZML-X native plugin format: https://github.com/SilentDemonSD/WZML-X/blob/wzv3/plugins/README.md
