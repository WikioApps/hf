# Hugging Face Uploader for WZML-X v3

This plugin brings the Hugging Face upload destination from [WikioApps/hf](https://github.com/WikioApps/hf) into the **same Python process** as WZML-X (branch `wzv3`). It does not launch a second bot, MongoDB client, or web service.

## Native WZML-X status (version 1.1)

**There is no separate status or cancellation interface.** Each `/hfmirror` transfer is registered in WZML-X's existing task registry, so the normal `/status` and `/status me` commands show HF jobs alongside other tasks. Starting a transfer displays the native status message, which refreshes automatically according to the bot's `STATUS_UPDATE_INTERVAL`. The existing status page handles refresh, pagination and filtering.

Cancel through the regular WZML-X command `/cancel` (reply to the originating command) or its standard `/cancel_<GID>` variant shown on the status page. The plugin adds no custom refresh, cancel, or status buttons.

The native status page shows source download byte progress, speed, ETA and the subsequent HF upload phase. For uploads the SDK's instrumented HTTP file-read progress is used where available; hashing/commit finalization may not show network byte progress.

**Important:** Once an HF SDK upload is in-flight, cancellation is best-effort: its blocking worker is allowed to finish to avoid deleting a file still being read. A remote commit can therefore complete even after a cancellation request.

## Install in the running WZML-X bot

1. Send `/plugins` → **Install** → **GitHub**.
2. Supply `WikioApps/hf@wzmlx-plugin`.
3. Select `hf_uploader` and approve `huggingface_hub==0.36.0` if prompted.
4. Add `HF_TOKEN=hf_...` as a secret environment variable in your existing WZML-X hosting dashboard. Restart the host after changing environment variables.
5. Send `/plugins` → **Installed** → **hf_uploader** → **Settings**.
6. Set `repo_id` to `YOUR_HF_USERNAME/YOUR_REPO_NAME`; choose `repo_type` (`dataset` or `model`), optionally configure branch, default folder, new-repository privacy, filename collisions, limits and concurrency.
7. Try a small file and check the **regular** `/status` page.

For a plugin already installed from GitHub: use `/plugins` → **Installed** → **hf_uploader** → **Update** to fetch version 1.1.0. If that does not update, reinstall it from the branch above.

The HF token needs write permission to the target repository (and the appropriate organization permissions when applicable). Keep it in host secrets, not in chat messages or publicly committed configuration. `HF_REPO_ID` can optionally override the repository configured in plugin settings.

## Commands

| Command | Function |
| --- | --- |
| `/hfmirror URL` (or `/hfm URL`) | Download one direct HTTP(S) file, then upload it to your HF repo |
| `/hfmirror` (reply to Telegram file) | Download the replied media and upload it to HF |
| `/hfmirror TELEGRAM_MESSAGE_LINK` | Copy media from a channel/message your bot can access |
| `/hfmirror URL -n "My File.mp4"` | Specify the final filename |
| `/hfmirror URL -p movies/season1` | Upload under an HF subfolder |
| `/status` or `/status me` | **Existing** WZML-X status interface, now including HF tasks |
| `/cancel` or `/cancel_<GID>` | **Existing** WZML-X cancellation interface |

HF plugin commands are owner-only because the installation uses a single owner-managed token.

### Examples

```text
/hfmirror https://example.com/video.mp4
/hfmirror https://example.com/video.mp4 -n "Movie.mp4" -p movies
/hfmirror https://t.me/YourPublicChannel/100
```

Or reply to a Telegram document/video with `/hfmirror`. A `t.me/c/...` private-channel message link works only if the bot has access to that message.

## What this plugin intentionally does not replace

- It does **not yet** alter the built-in WZML-X `/mirror` pipeline or add `/mirror -hf`. That requires a core upload-backend hook.
- It does not bring the separate `hf` bot's torrent engine, archive selector, host/playlist resolver, multi-user HF token store, persistent retry history, or website dashboard.
- Downloads use WZML-X's host filesystem for temporary files, then remove them after completion/failure/cancellation. Plan disk capacity for the largest supported file plus other WZML-X tasks.

HF repository quotas, terms and content rules still apply. Hugging Face is not an unlimited generic file-hosting/CDN replacement.

WZML-X plugin specification: https://github.com/SilentDemonSD/WZML-X/blob/wzv3/plugins/README.md
