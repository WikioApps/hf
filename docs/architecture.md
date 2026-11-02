# Source guide

The root `app.py` uses only the Python standard library to select the project’s `.venv`, then loads `bot/core/application.py`. This also handles hosts that activate a different `venv` before running `python app.py`. Re-execution uses `exec` to preserve the service process ID.

The application has one service process. Uvicorn starts the Telegram client and MongoDB connection through the application lifespan. A supervisor renews the single-instance lease, persists active phases and refreshes Telegram status messages. Failure of that supervisor makes health checks fail and stops the service.

## Responsibilities

- `bot/core`: validated bootstrap configuration, shared settings and process lifecycle.
- `bot/modules`: Telegram commands, user and owner menus, file confirmation, archive selection and status messages.
- `bot/helper/ext_utils`: MongoDB storage, transfer coordination, worker subprocess protocol, path and network checks.
- `bot/helper/mirror_leech_utils`: download engines, archive operations, Hugging Face commits and task progress.
- `web/backend`: HTTP routes, one-time Telegram sign-in, cookie sessions and authorization.
- `web/frontend`: responsive HTML, CSS and plain JavaScript. No frontend build system is needed.
- `scripts`: verified user-space tool installation and the legacy InstaPods service detector.

## Transfer lifecycle

A command or confirmed file creates a task and a destination snapshot. Download slots control input concurrency; upload slots independently control Hugging Face transfers. Different users and multiple tasks from one user use the same global pools. Extracted members are processed sequentially to limit disk use.

The HF token is loaded from that user's current settings when an upload starts. The destination snapshot keeps an existing task pointed at its original repository even if the user changes settings while it runs. A retry continues with that destination and the current token.

Progress is taken from aria2 RPC, Telegram's download callback, archive bytes written, or HF LFS transport reads. Hashing and commit finalization use separate phases without invented byte progress. Small files committed directly by the HF API may show Prepare/Commit without a separate upload bar.

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
│   │   │   ├── network_utils.py
│   │   │   ├── process_utils.py
│   │   │   ├── task_manager.py
│   │   │   └── worker.py
│   │   ├── mirror_leech_utils/
│   │   │   ├── archive_utils/
│   │   │   │   ├── __init__.py
│   │   │   │   └── sevenz.py
│   │   │   ├── download_utils/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── aria2_download.py
│   │   │   │   ├── direct_link_generator.py
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
│   │   ├── mirror.py
│   │   ├── router.py
│   │   ├── selection.py
│   │   ├── status.py
│   │   ├── users.py
│   │   └── users_settings.py
│   └── __init__.py
├── docs/
│   └── architecture.md
├── scripts/
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
├── .env.example
├── .gitignore
├── README.md
├── app.py
├── build.sh
├── requirements.txt
└── start.sh
```
