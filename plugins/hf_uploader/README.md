# Hugging Face Mirror — WZML-X v3 native plugin

This branch ports the destination uploader of [WikioApps/hf](https://github.com/WikioApps/hf) to WZML-X's native `Mirror` pipeline. It runs **inside the WZML-X v3 process**. Do not run `WikioApps/hf/app.py`, start a second bot, or expose a new HTTP port.

## Changes in v3.0.1

- **Uses WZML-X download engines directly** instead of a second HTTP/Telegram downloader. The `/hfmirror` command inherits WZML-X's source handling, torrent file selection, queue and task checks.
- **Uses WZML-X archive helpers**: `-e` extract (optional password), `-z` ZIP (optional password), `-j` join split files. Also delegates the supported filename, FFmpeg, sample, screenshot, conversion and metadata stages.
- **Uploads directly to the selected HF folder**: WZML-X's local `tg/mirror/...` download path, archive name and nested extracted directories are never included in the Hugging Face remote path. Multi-file archives are flattened into this destination folder; duplicate names follow `collision` (`rename`, `skip`, `overwrite`).
- **Resilient Hub transport**: honors Hugging Face's native Xet upload backend when installed, retries transient LFS/commit failures (up to four attempts), and shows the underlying HTTP/network cause of an error. Some server/authentication/quota failures cannot be fixed by retrying.
- **Uses native `/status`**: shows WZML-X progress bar, transfer phase, speed, queue, task owner, ETA and existing automatic status edits / native cancel commands. There is **no plugin status/cancel/refresh button or separate status command**.
- **Hugging Face file explorer in Telegram**: after an upload, show folder/repo/branch and linked files 8 at a time with **Previous / Next** inline buttons, plus **Open HF Folder**. All page clicks query Hugging Face remotely. No separate TeleGraph or web page, no MongoDB task, file-list or pagination persistence.
- **Restart safe**: messages contain the actual HF repository, type, branch and directory path, so Next/Previous work after a process restart, as long as the plugin, same main Telegram bot and an HF token with access remain deployed.
- HF SDK uploads run in threads so other Telegram commands and status updates keep responding. In-flight HF HTTP commits cannot safely be terminated mid-file; native cancel stops before a subsequent file.

## Install / update

From your running WZML-X v3 bot:

1. `/plugins` → **Install** → **GitHub**.
2. Supply **`WikioApps/hf@wzmlx-plugin`**.
3. Select `hf_uploader`, approve `huggingface_hub==0.36.0` if prompted.
4. If already installed: `/plugins` → **Installed** → **hf_uploader** → **Update**. If the update is refused, uninstall and reinstall it.
5. Set a private hosting environment secret `HF_TOKEN=hf_YOUR_WRITE_TOKEN` and restart the WZML-X service when adding/changing host env values. Optional `HF_REPO_ID=USERNAME/REPO` can override the repository setting.
6. `/plugins` → **Installed** → **hf_uploader** → **Settings**: configure `repo_id`, `repo_type` (`dataset` or `model`), `branch`, **`folder`**, `collision`, optional `max_files`, `private` (only on newly created repositories). Alternatively set **`HF_FOLDER=your/target`** as a hosting environment variable, overriding the plugin's `folder` setting. An explicit `-up other/folder` on a command overrides both for that task. There is no need to set `HF_FOLDER` if you already use plugin Settings.
7. Test on a small link or Telegram file first; use the **existing** `/status` command and auto-updating status message.

No new web port, MongoDB collection, local HTTP service or additional Telegram bot token is needed. WZML-X's own plugin Settings continue to use WZML-X's existing settings storage, but this plugin never persists task states, upload manifests, file lists or pagination sessions in MongoDB. Credentials are only read from host environment variables.

## Commands and examples

```text
/hfmirror https://example.com/movie.mkv
/hfm https://example.com/file.zip
/hfmirror https://example.com/archive.zip -e
/hfmirror https://example.com/archive.zip -e ArchivePassword123
/hfmirror https://example.com/file.mkv -z
/hfmirror https://example.com/file.mkv -z SecureZipPassword123
/hfmirror https://example.com/archive.zip -e ArchivePassword123 -z NewZipPassword456
/hfmirror https://example.com/archive.zip -e -up media/archives
/hfmirror https://example.com/archive.zip -e -p media/archives
/hfmirror MAGNET_LINK -s
/hfmirror https://example.com/split-archive-part1 -j -e ArchivePassword123
```

The options are **WZML-X's options**, not a second custom parser. The `-p` alias is accepted for compatibility with the old plugin; it is transformed into WZML-X's `-up` option, where the value means an HF destination **folder**, not Google Drive. Reply with `/hfmirror -e` to extract a Telegram ZIP attachment, or `/hfmirror -z pass` to create a password-protected ZIP. WZML-X's built-in 7-Zip binary must be present for ZIP/extraction.

`-s` uses WZML-X's torrent file-selection interface where supported, not HF list selection. `-i` (multi), `-b` (bulk), `-d` (seed) and `-yt` (YouTube upload destination) are intentionally not supported by this plugin; they are refused instead of accidentally sending tasks to the ordinary mirror uploader. Other native download engines work only when enabled/configured in your WZML-X instance.

### Remote folder / pages example

If you extract `Course.zip` into 80 files and configure `folder=videos`, they are uploaded **directly** under `videos/`:

```text
username/dataset-name @ main
videos/video1.mp4
videos/video2.mp4
videos/video3.mp4
...
```

Even if the local download is at `tg/mirror/wzmlx/Task-001/Course/chapter01/video1.mp4`, the Hugging Face file becomes `videos/video1.mp4`. Folder structures **inside archives are intentionally flattened**. For duplicate filenames in different archive directories, the `collision` setting controls the outcome; `rename` gives names like `video1 (1).mp4`.

The Telegram completion message displays the **real** `videos` folder and page 1/10, with `Next ▶`. Clicking page 2 uses `HfApi.list_repo_tree` to list remote files again; after a restart, it reads the same folder path from the Telegram message. No Telegraph pages or database records.

## Important limits

- The token must have HF write permissions; organization repos require appropriate permissions. Private repo files remain private to unauthorized viewers even if links are shared.
- If you encounter `Error while uploading ... to the Hub`, the plugin now retries transient failures and logs the root cause. A 401/403, quota error, unsupported upload or persistent network block still requires fixing the HF token, permissions, quotas or network; the filename/path alone does not identify the server error.
- This plugin uses the host's disk and WZML-X's archive logic. Archives require sufficient space for downloads, extraction and possibly re-zipping. `max_files` caps the number of files prepared for one HF upload. Do not assume Hugging Face is an unlimited Google Drive/CDN replacement.
- WZML-X v3's native ZIP helper creates archives using its configured 7-Zip settings; encrypted ZIP compatibility is determined by that helper.
- A cancel during a remote HF commit cannot roll back a file already uploaded; the plugin stops additional files and cleans local data after the active SDK operation finishes.
- The WZML task status is not restart-persistent (the ordinary WZML behavior). **Completed HF file explorer messages are** restart-safe because the contents are fetched from HF each time.

## Source

- WZML-X v3: https://github.com/SilentDemonSD/WZML-X/tree/wzv3
- WZML-X plugin API: https://github.com/SilentDemonSD/WZML-X/blob/wzv3/plugins/README.md
- Original Hugging Face upload implementation: https://github.com/WikioApps/hf/blob/main/bot/helper/mirror_leech_utils/upload_utils/huggingface_upload.py
