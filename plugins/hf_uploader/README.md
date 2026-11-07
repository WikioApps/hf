# HF Uploader for WZML-X

A native WZML-X plugin port of the repository uploader from [WikioApps/hf](https://github.com/WikioApps/hf). It runs in the **same** WZML-X Python process. **Do not run the original `app.py` or `start.sh` in WZML-X.**

## Install from Telegram

1. In the running WZML-X bot, open `/plugins` → **Install** → **GitHub**.
2. Enter `WikioApps/hf@wzmlx-plugin` (or `https://github.com/WikioApps/hf/tree/wzmlx-plugin` if supported).
3. Select **hf_uploader** and approve the `huggingface_hub==0.36.0` dependency if prompted.
4. Add `HF_TOKEN=hf_...` as a **private environment variable** in the WZML-X hosting dashboard, then restart the WZML-X container (a host restart is necessary for a newly added environment secret).
5. Open `/plugins` → **Installed** → **hf_uploader** → **Settings**, set `repo_id` to `YOUR_HF_USERNAME/YOUR_REPO_NAME`, choose `repo_type` (`dataset` or `model`), and optionally branch, folder, privacy and collision policy. You can alternatively set `HF_REPO_ID` in your host environment.
6. Test by replying to a Telegram document or video with `/hfmirror`; or send `/hfmirror https://example.com/file.zip`.

The Hugging Face access token should have write permissions on the target repo. For organizations, it must have the appropriate organization permissions as well. Do not paste it into public Telegram chats or the plugin Settings field; `HF_TOKEN` is intentionally read only from the host's environment.

## Commands

- `/hfmirror URL` or `/hfm URL`: download one HTTP(S) file and upload it to the configured Hugging Face repo.
- `/hfmirror` as a **reply to a file**: download that Telegram file and upload to HF.
- `/hfmirror https://t.me/publicchannel/123` (or an accessible private channel message link): upload the linked Telegram file.
- `/hfmirror URL -n "custom name.mp4" -p "movies/season1"`: rename or set a per-task HF subfolder.
- `/hfstatus`: see active tasks (Telegram/HTTP download progress and HF upload phase).
- `/hfcancel TASK_ID`: best-effort cancellation. The Hugging Face SDK may finish an already-started remote upload.

All commands are **owner-only**, because the uploader uses a single owner's HF token.

## What it does / does not do

**Included:** native WZML-X plugin loading, direct HTTP(S) downloads, Telegram media replies and supported message links, per-file size / free-space checks, Hugging Face model & dataset repos, branch creation, private new repos, subfolders, rename/overwrite/skip collision handling, task status, basic cancellation, and local temporary-file cleanup.

**Not included yet:** generic `/mirror -hf` (that requires a WZML-X core upload-destination hook), torrent/magnet downloading, automatic file-host/playlist resolution, archive extraction or selection, per-user token management, integration with WZML-X's built-in `/status`, persistent retry/history, or the separate `hf` repository web dashboard. These features cannot be obtained by directly importing the old Telethon bot, and should be ported individually if needed.

HF file storage is subject to Hub quotas, content policies and repository versioning; this is not an unlimited generic CDN or Google Drive replacement. Private HF links only work for authorized viewers.

## Runtime

No extra container or port is needed. The plugin uses the WZML-X `aiohttp` and Pyrogram installation and adds `huggingface_hub==0.36.0`. Temporary files live under WZML-X's `DOWNLOAD_DIR` and are removed after completion/failure. Ensure the hosting service has enough free temporary disk for each downloaded file, plus headroom.

WZML-X native plugin instructions: https://github.com/SilentDemonSD/WZML-X/blob/wzv3/plugins/README.md
