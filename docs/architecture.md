# Source guide

The root `app.py` uses Python’s standard library to find `.venv`, `venv`, or the active virtual environment. Candidate interpreters must have a supported Python version, the exact dependencies from `requirements.txt`, and working application imports. An incomplete environment is skipped; a valid host-managed `venv` works without `.venv`. The launcher then runs `bot/core/application.py`, using `exec` when switching interpreters to preserve the service process ID. It never installs packages at startup.

The application has one service process. Uvicorn starts the HTTP listener while the application lifespan launches bot initialization in a background task. Initialization waits for a MongoDB instance lease before connecting Telegram or recovering transfers. A separate heartbeat renews the lease throughout startup and operation using MongoDB’s clock. A supervisor persists active phases and refreshes Telegram status messages. Failure of that supervisor makes health checks fail and stops the service.

## Responsibilities

- `bot/core`: validated bootstrap configuration, shared settings and process lifecycle.
- `bot/modules`: Telegram commands, user and owner menus, file confirmation, archive selection and status messages.
- `bot/helper/ext_utils`: MongoDB storage, transfer coordination, worker subprocess protocol, path and network checks.
- `bot/helper/mirror_leech_utils`: download engines, archive operations, Hugging Face commits and task progress.
- `web/backend`: HTTP routes, one-time Telegram sign-in, cookie sessions and authorization.
- `web/frontend`: responsive HTML, CSS and plain JavaScript. No frontend build system is needed.
- `bot/helper/mirror_leech_utils/download_utils/providers`: one module per named host; `resolver.py` provides bounded public HTTP metadata requests, and `direct_link_generator.py` dispatches URLs. `media_download.py` hosts yt-dlp/gallery-dl integrations; `mega_crypto.py` verifies streamed MEGA files.
- `scripts`: verified user-space tool installation and the legacy InstaPods service detector.

## Transfer lifecycle

A command or confirmed file creates a task and a destination snapshot. Download slots control input concurrency; upload slots independently control Hugging Face transfers. Different users and multiple tasks from one user use the same global pools. An archive uses one extraction worker at a time, while a bounded pool uploads completed members concurrently. Collections use bounded per-task pipelines and global download/upload slots. Every in-flight file has an independent progress object; the parent aggregates phases for Telegram and the website.

The HF token is loaded from that user's current settings when an upload starts. The destination snapshot keeps an existing task pointed at its original repository even if the user changes settings while it runs. A retry continues with that destination and the current token.

Progress is taken from aria2 RPC, Telegram's download callback, archive bytes written, or HF LFS transport reads. Hashing and commit finalization use separate phases without invented byte progress. Small files committed directly by the HF API may show Prepare/Commit without a separate upload bar.

The resource manager reserves unwritten disk bytes, checks container-aware available memory and limits worker RSS. File cleanup follows HF confirmation and MongoDB bookkeeping. Local upload receipts permit recovery if bookkeeping fails after a commit.

Subprocesses run in their own process groups and are terminated on cancellation or shutdown. Native archive children remain in their worker's group. Downloaded and extracted files are isolated by task locally; those internal task IDs never become remote folder names.

## MongoDB collections

| Collection | Purpose |
| --- | --- |
| `settings` | Bot-wide access, limits and website settings. |
| `users` | User identity, access state and destination settings; encrypted HF tokens. |
| `tasks` | Task identity, phase, selected members, encrypted source and options. |
| `manifests` | Archive metadata in bounded chunks. |
| `uploads` | Confirmed per-file HF results, separately stored for large archives. |
| `telegram` | Encrypted portable Telegram session. |
| `sessions` | Hashed one-time links and portal sessions, with expiration indexes. |
| `leases` | Single active bot instance and lease expiration. |

Collections are scoped by bot ID. Metadata and filenames are visible to database administrators; tokens, source URLs, passwords and Telegram sessions are encrypted. Database backups are still necessary. The service does not delete task history automatically.

## Logging and settings messages

Only the parent service writes the rotating application log. Workers write redacted diagnostics to stderr; the parent drains that pipe while reading progress JSON from stdout. `/logs` sends a redacted snapshot to the owner in a private chat. Upload stages and confirmed commits are logged without token or source-URL values.

Settings-menu message IDs are saved in each MongoDB user record. A text prompt edits its existing menu, and a successful input is deleted before that menu is restored. Back/Cancel invalidate the pending edit. Standard ZIP operations live in `zip_utils.py`; 7-Zip remains available for RAR, 7Z and ZIP methods unsupported by Python.

## Making changes

Add commands in `bot/helper/telegram_helper/bot_commands.py` and implement them in `bot/modules`. Keep preset settings, labels and validation together in `bot/core/settings.py`. Transfer engines should emit real progress through the worker protocol and return confirmed results only after completion. Keep secrets out of logs, command arguments and web responses.

Run `sh build.sh --build` to check dependency installation, imports, syntax and helper executables. Start with `sh start.sh`. No test files are shipped or executed by deployment.

## Complete repository tree

```text
hf/
├── bot/
│   ├── core/
│   │   ├── __init__.py
│   │   ├── application.py
│   │   ├── config_manager.py
│   │   ├── runtime.py
│   │   └── settings.py
│   ├── helper/
│   │   ├── ext_utils/
│   │   │   ├── __init__.py
│   │   │   ├── db_handler.py
│   │   │   ├── files_utils.py
│   │   │   ├── format_utils.py
│   │   │   ├── log_utils.py
│   │   │   ├── network_utils.py
│   │   │   ├── process_utils.py
│   │   │   ├── resource_manager.py
│   │   │   ├── task_manager.py
│   │   │   └── worker.py
│   │   ├── mirror_leech_utils/
│   │   │   ├── archive_utils/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── sevenz.py
│   │   │   │   └── zip_utils.py
│   │   │   ├── download_utils/
│   │   │   │   ├── providers/
│   │   │   │   │   ├── __init__.py
│   │   │   │   │   ├── bandcamp.py
│   │   │   │   │   ├── bilibili.py
│   │   │   │   │   ├── bitbucket.py
│   │   │   │   │   ├── bluesky.py
│   │   │   │   │   ├── bunkr.py
│   │   │   │   │   ├── buzzheavier.py
│   │   │   │   │   ├── catbox.py
│   │   │   │   │   ├── cyberdrop.py
│   │   │   │   │   ├── dailymotion.py
│   │   │   │   │   ├── deviantart.py
│   │   │   │   │   ├── direct.py
│   │   │   │   │   ├── douyin.py
│   │   │   │   │   ├── dropbox.py
│   │   │   │   │   ├── facebook.py
│   │   │   │   │   ├── flickr.py
│   │   │   │   │   ├── giphy.py
│   │   │   │   │   ├── github.py
│   │   │   │   │   ├── gitlab.py
│   │   │   │   │   ├── gofile.py
│   │   │   │   │   ├── google_drive.py
│   │   │   │   │   ├── huggingface.py
│   │   │   │   │   ├── imagebam.py
│   │   │   │   │   ├── imgbb.py
│   │   │   │   │   ├── imgbox.py
│   │   │   │   │   ├── imgur.py
│   │   │   │   │   ├── instagram.py
│   │   │   │   │   ├── internet_archive.py
│   │   │   │   │   ├── krakenfiles.py
│   │   │   │   │   ├── lensdump.py
│   │   │   │   │   ├── mediafire.py
│   │   │   │   │   ├── mega.py
│   │   │   │   │   ├── mixdrop.py
│   │   │   │   │   ├── pinterest.py
│   │   │   │   │   ├── pixeldrain.py
│   │   │   │   │   ├── pixhost.py
│   │   │   │   │   ├── reddit.py
│   │   │   │   │   ├── redgifs.py
│   │   │   │   │   ├── rutube.py
│   │   │   │   │   ├── soundcloud.py
│   │   │   │   │   ├── streamable.py
│   │   │   │   │   ├── streamtape.py
│   │   │   │   │   ├── tiktok.py
│   │   │   │   │   ├── tumblr.py
│   │   │   │   │   ├── twitch.py
│   │   │   │   │   ├── twitter.py
│   │   │   │   │   ├── vimeo.py
│   │   │   │   │   ├── vk.py
│   │   │   │   │   ├── xiaohongshu.py
│   │   │   │   │   ├── yandex_disk.py
│   │   │   │   │   └── youtube.py
│   │   │   │   ├── __init__.py
│   │   │   │   ├── aria2_download.py
│   │   │   │   ├── direct_link_generator.py
│   │   │   │   ├── media_download.py
│   │   │   │   ├── mega_crypto.py
│   │   │   │   ├── mega_download.py
│   │   │   │   ├── resolver.py
│   │   │   │   └── telegram_download.py
│   │   │   ├── status_utils/
│   │   │   │   ├── __init__.py
│   │   │   │   └── task_status.py
│   │   │   ├── upload_utils/
│   │   │   │   ├── __init__.py
│   │   │   │   └── huggingface_upload.py
│   │   │   └── __init__.py
│   │   ├── telegram_helper/
│   │   │   ├── __init__.py
│   │   │   └── bot_commands.py
│   │   └── __init__.py
│   ├── modules/
│   │   ├── __init__.py
│   │   ├── logs.py
│   │   ├── mirror.py
│   │   ├── router.py
│   │   ├── selection.py
│   │   ├── speedtest.py
│   │   ├── status.py
│   │   ├── users.py
│   │   └── users_settings.py
│   └── __init__.py
├── docs/
│   ├── licenses/
│   │   └── siphon.txt
│   ├── architecture.md
│   ├── sources.md
│   └── supported-sites.md
├── scripts/
│   ├── install_media_tools.py
│   ├── install_tools.py
│   └── service_detection.py
├── web/
│   ├── backend/
│   │   ├── __init__.py
│   │   └── application.py
│   ├── frontend/
│   │   ├── app.js
│   │   ├── index.html
│   │   └── style.css
│   └── __init__.py
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile
├── README.md
├── THIRD_PARTY_NOTICES.md
├── app.py
├── build.sh
├── requirements.txt
└── start.sh
```
