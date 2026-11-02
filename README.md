# Hugging Face Mirror for Telegram

A Python Telegram bot that downloads Telegram media or supported web links and uploads them **only to Hugging Face**. Built for the InstaPods Python preset, without Docker, root access, aria2, FFmpeg, rclone, or a local Telegram Bot API server.

Live status messages follow the WZML-X/mirror-leech presentation: filename, task owner, progress bar, processed/total bytes, speed, ETA/estimated total/elapsed time, phase, engine, input/output modes, cancel command, CPU, RAM, free disk and uptime. There are paginated views, user filtering, phase filtering and an aggregate task overview.

This is an independent implementation of that workflow, not a fork containing all of those bots' engines. In particular, “every mirror-leech source” and “no external binaries” are not equivalent requirements. The supported-source table below defines what this repository actually implements.

## Implemented features

- Telegram documents, videos, audio, photos and other downloadable media, through Telethon/MTProto; reply with `/mirror` or send media directly.
- Public/private Telegram message links that the bot can access. An optional existing user session can retrieve links accessible to that account, **for owner requests only**.
- Streaming HTTP/HTTPS with redirects, Content-Disposition filenames, length checking, timeouts and disk limits.
- Link adapters for public Google Drive single files, Dropbox, Pixeldrain and MediaFire; yt-dlp resolves other supported pages to a single HTTP media file.
- Rename and destination-folder options; model or dataset repositories, a configurable branch, and automatic repository/branch creation.
- Independent download and upload queues; serialized extraction; bounded accepted task count.
- ZIP, TAR, TAR.GZ/BZ2/XZ and 7Z extraction, optional paginated member selection, then extract → upload → delete one member at a time. Original archive remains until every selected member is committed.
- Real transport byte counts for streamed Hugging Face LFS uploads. Hashing/preparation and commit finalization are separate stages. Small regular Git files or deduplicated files may go straight from preparation to commit; no simulated upload percentage is shown.
- Cancellation, failed-file retention, restart detection, explicit retry, history and cleanup. Successful archive members are skipped on retry.
- Single-instance file lock, child-process cleanup, graceful SIGTERM shutdown, JSON application logs and a health endpoint tied to the real bot supervisor.
- Owner-only access by default; additional explicitly allowed Telegram user IDs.

## Supported sources and limits

| Source or feature | Behavior |
| --- | --- |
| Telegram media | Downloads with the bot's MTProto permissions, subject to Telegram's account/file/API limits; this does not use the cloud Bot API `getFile` download endpoint. |
| `https://t.me/channel/123` or `https://t.me/c/.../123` | Single accessible message with media. Bot access is required unless the owner configures their own user session. Restricted/protected content is subject to Telegram enforcement. |
| Direct HTTP/HTTPS files | Public HTTP downloads and redirects. HTML landing pages are resolved or rejected, rather than uploaded accidentally. |
| Google Drive | Public single files and gdown-supported Google document exports, subject to Google's quota and sharing settings. No folders or login cookies. |
| Dropbox / Pixeldrain / MediaFire | Public file links; remote-service changes can require adapter updates. |
| Other yt-dlp sites | Only a directly downloadable single HTTP/HTTPS format. No playlists, separate video/audio merging, HLS/DASH processing or FFmpeg. Availability depends on the site and pinned extractor release. |
| Archives | ZIP, TAR variants, 7Z. Links and unsafe member paths are excluded/rejected. Duplicate member paths, oversized archives and oversized files are rejected. |
| RAR | Upload intact; extraction requires a decoder outside the pure-Python deployment scope. |
| Torrents/magnets, NZB, JDownloader, rclone/MEGA SDK integrations | Not implemented. They are not silently treated as working inputs. |
| CAPTCHA, paid/login-only links, DRM, expired signed links | Not bypassed. Supply a currently accessible direct link. |

Python is the application and transfer runtime. Some Python dependencies include normal platform wheels (for example compression and system metrics). No separately installed OS executables are required by this application's supported paths. Python 3.11–3.13 is accepted; **Python 3.12 is recommended and tested**.

## Repository structure

```text
hf/
├── app.py                # ASGI service, startup checks, instance lock, supervision
├── bot.py                # Telegram commands, selection UI and live statuses
├── core.py               # Task state, queues, manifests, retries, cleanup
├── transfers.py          # Downloads, link adapters, worker supervision
├── worker.py             # Isolated HF upload, extraction and resolver workers
├── config.py             # Environment validation and safe paths
├── requirements.txt      # Pinned Python dependencies
├── build.sh              # Virtualenv install, syntax checks and tests
├── start.sh              # Exec the actual app using .venv
├── .env.example          # Placeholders only
├── .gitignore
├── README.md
└── tests/
    └── test_app.py        # Behavioral and local integration tests
```

At runtime, `DATA_DIR` contains the Telegram session, `instance.lock`, and `tasks/<task-id>/manifest.json` plus temporary or retained files. It must be writable and private. The program does not expose these files over HTTP.

## Deploy on InstaPods from a phone

1. Open [InstaPods](https://instapods.com/deploy/github/) and sign in. Create a pod with **From GitHub**, or open an existing Python pod's **Git** tab.
2. Enter **`https://github.com/WikioApps/hf`** and select **`main`**. Use the repository root (no subdirectory). Select the **Python 3.12** preset. No managed database or release command is needed.
3. Review the detected commands. Replace them with the exact commands below. Do not accept a guessed Flask/Gunicorn startup: this app runs one Uvicorn process and its Telegram bot together.
4. Enter the six required variables in the wizard or the **Env** tab. For existing pods, Env accepts one `KEY=VALUE` per line. Set `DATA_DIR=/home/instapod/hf-data` to keep sessions and retained files outside the application checkout. Keep port 8000 unless you also configure the pod's app/proxy port to match.
5. Save and deploy. Open **Git → deployment/build logs** to check installation and tests. Then use **Logs**, filtered to the `app` service, to check startup.
6. Visit the pod's URL followed by `/healthz`. HTTP 200 with `status: ok` means the Telegram client and status supervisor are alive. HTTP 503 means they are not ready/healthy. `/` performs the same check for platforms probing the root path.
7. Open your bot in Telegram as `OWNER_ID`, send `/start`, then test a small file with `/mirror`. Check `/status` and the final Hugging Face folder link.
8. Only after that test should you increase file-size limits or concurrency. The repository being published is not proof of an actual deployment or upload.

### Exact commands

**Install** (when the panel provides its own install step):

```sh
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
```

**Build** (independently usable; it also performs installation):

```sh
sh build.sh
```

**Start**:

```sh
sh start.sh
```

**Release**: leave empty.

The duplicated install when both Install and Build are configured is intentional and idempotent. `start.sh` sets `.venv/bin` on `PATH`, then uses `exec .venv/bin/python app.py`; Python workers use `sys.executable`, so they always use that same interpreter. Nothing backgrounds the bot or installs system packages. File execute permissions are unnecessary because the dashboard invokes `sh` explicitly.

Do not run multiple Uvicorn workers or replicas using the same bot token. The local lock protects the same `DATA_DIR`, not separate pods. Never start a second manual bot while `app.service` is already running.

### Getting credentials

- `BOT_TOKEN`: create a bot with Telegram's **@BotFather**.
- `API_ID` and `API_HASH`: create a Telegram API application at [my.telegram.org](https://my.telegram.org). MTProto needs these even for a bot.
- `OWNER_ID`: your numeric Telegram user ID, not your username or chat ID.
- `HF_TOKEN`: a Hugging Face token with write access to the target repository; scope it as narrowly as possible. A token without creation rights requires an existing accessible repository.
- `REPO_ID`: `namespace/repository` (for example `yourname/uploads`). Use a namespace you can write to.

`USER_SESSION_STRING` is optional. Use only a Telethon StringSession for an account you control. The bot never asks for a phone number, login code or interactive sign-in on the server. Treat a user session as a secret with account-level access. Normal bot downloads do not need one.

### Environment variables

The application explicitly loads `.env` beside `app.py` with python-dotenv, with exported environment variables taking precedence. InstaPods' Env UI writes/injects the variables; this explicit loader also covers manual file edits and older environment wiring. `.env.example` is not loaded.

| Variable | Required | Default / purpose |
| --- | --- | --- |
| `API_ID` | Yes | Positive Telegram application ID. |
| `API_HASH` | Yes | 32-character hexadecimal application hash. |
| `BOT_TOKEN` | Yes | BotFather token. |
| `OWNER_ID` | Yes | Positive numeric Telegram user ID. |
| `HF_TOKEN` | Yes | Hugging Face write token. |
| `REPO_ID` | Yes | `namespace/repository`. |
| `REPO_TYPE` | No | `model`; alternatively `dataset`. |
| `BRANCH` | No | `main`; created if needed. No fallback to another branch on failure. |
| `PATH_IN_REPO` | No | Empty; optional relative destination prefix. |
| `HF_PRIVATE` | No | `true`; visibility when creating a new HF repository. Does not change an existing repository's visibility. |
| `ALLOWED_USERS` | No | Empty; comma/space-separated numeric user IDs in addition to the owner. |
| `PORT` | No | `8000`; listens on `0.0.0.0`. Must match the hosting proxy's app port. |
| `DATA_DIR` | No | `data` beside the source; recommended on InstaPods: `/home/instapod/hf-data`. |
| `DOWNLOAD_WORKERS` | No | `2`, range 1–8. |
| `UPLOAD_WORKERS` | No | `1`, range 1–4. Start with one to limit RAM and concurrent commits. |
| `MAX_TASKS` | No | `20`, range 1–100; accepted active and queued tasks. |
| `STATUS_UPDATE_INTERVAL` | No | `5` seconds, range 3–60. Telegram flood waits are respected. |
| `MAX_FILE_GB` | No | `20` GiB, positive integer; applies to each download/extracted member. |
| `MIN_FREE_MB` | No | `512` MiB, positive integer disk reserve. |
| `MAX_EXTRACT_GB` | No | `50` GiB, positive integer total declared expanded archive size. |
| `MAX_ARCHIVE_MEMBERS` | No | `10000` positive integer. |
| `USER_SESSION_STRING` | No | Empty; optional owner-only Telethon account session for message links. |

## Telegram usage

```text
/mirror https://example.org/file.bin
/mirror https://example.org/archive.zip -unzip
/mirror https://example.org/archive.zip -e -s
/mirror https://example.org/download -n "model.zip" -p models -e
```

Reply to a Telegram file with `/mirror`, optionally with the same flags. `/m`, `/upload` and `/u` are aliases. Put multiple URL tasks on separate lines after `/mirror`; each line has its own flags.

`/status` opens your live view in the current chat; the owner can see all authorized users' tasks in that chat. `/status me` restricts it to the requester. Buttons paginate, refresh, filter phases, or show task counts and total speeds. `/s` is an alias. Status messages contain **phase-specific** speed/progress: an upload starts its own counters when download/extraction has finished. Queue, selection, preparation and commit phases do not invent a rate or ETA.

`/select ID` opens archive member checkboxes when `-e -s` was used. Choose **Upload selected** to proceed. Selection expires after an hour and retains the input for retry.

`/cancel ID` cancels only that task and reaps its worker. `/retry ID` retries retained input and skips already recorded committed archive members. `/history` shows the most recent ten tasks and result folders. `/clean ID` removes a terminal task's local files and manifest, never its HF files. `/stats` reports system resource readings. Commands are restricted to the owner/allowed users; only owners can manage another user's task.

Files are stored at:

```text
PATH_IN_REPO / optional-command-folder / task-id / original-filename-or-archive-path
```

Task IDs prevent accidental overwriting across simultaneous jobs. Archive directory structure is preserved; temporary extraction names never become repository filenames. Retry uses the same remote path, so an interrupted commit can be safely retried without creating another task folder. If cancelled during final commit, a file may already have reached HF; cancellation cannot reverse a completed remote commit.

## Storage, retries and resources

The original download occupies disk throughout extraction and upload. Each archive member is extracted to one additional temporary file, uploaded and removed. Plan disk for the archive **plus its largest expanded member**, concurrent downloads, the reserve, and retained failures. The archive remains until all selected uploads succeed. Completed tasks retain only small JSON history manifests.

No automatic task replay occurs after a restart. Interrupted tasks appear in `/history`; use `/retry ID`. Completed downloads and confirmed archive commits are reused. A partial ordinary HTTP/Telegram download restarts from the beginning. gdown can resume its own partial files. A partially uploaded HF member is retried through the HF client; this project does not guarantee byte-level multipart upload resume across process restarts.

InstaPods documents persistent storage across restarts. Do not assume deleting/recreating a pod or rolling back source code will restore your runtime data. Keep `DATA_DIR` outside the checkout, use the platform's backups where appropriate, and protect backups containing sessions/manifests. Manifests can contain original signed source URLs. `/clean` is deliberate; failed files are not silently removed to make room.

Start on **Build (2 GB RAM)** or a larger plan for archives. The smallest 10 GB disk plan cannot accommodate a default 20 GiB maximum download. Lower `MAX_FILE_GB` there. Compression dictionaries, archive metadata, multiple transfer clients and Python dependencies consume memory beyond streaming buffers. Solid 7Z and compressed TAR files may require repeated decompression for individually selected members; this trades CPU/time for lower temporary disk use. This is not a guaranteed 80% RAM limiter.

Use status CPU/RAM metrics as host/container readings from psutil; they may differ from plan/cgroup limits shown by the hosting dashboard. For very large repositories, follow Hugging Face's storage and file-count recommendations. Each file is its own commit; thousands of tiny members can be slow and may encounter service rate limits. Reduce concurrency and retry after remote throttling.

## Health, process management and logs

The HTTP server is Uvicorn in the same process/event loop as Telethon. `/` and `/healthz` return only service health, no task names, tokens or files. There is no dashboard or extra login system. Startup checks HF authentication and repository/branch setup, then starts Telegram. Health requires a connected Telegram client, a live supervisor and a recent heartbeat. A supervisor failure shuts down the server; prolonged Telegram disconnects trigger this after 60 seconds. Failed individual transfers remain visible/retryable and do not by themselves mean the bot process has crashed.

Keep the default web pod if you want public health checks. Official docs also describe **Settings → Pod type → Convert to worker**, which removes public ingress and URL health checks. The bot can run with the same command there, but you cannot visit its public health URL. This project does not invent a platform-specific health-check configuration file; use `/healthz` if your hosting controls accept a custom probe, otherwise `/` works.

In the dashboard: use **Git** for build/deployment logs and **Logs → app** for runtime logs. With the InstaPods CLI:

```sh
instapods git status YOUR_POD
instapods logs YOUR_POD -s app
instapods git deploy YOUR_POD -w
```

Copy the actual SSH command from the pod's **SSH** tab (host and SSH port are assigned by InstaPods). Once connected, useful commands are:

```sh
cd /home/instapod/app
.venv/bin/python --version
.venv/bin/python -m pip check
.venv/bin/python app.py --check-config
systemctl status app --no-pager
journalctl -u app -n 100 --no-pager
curl -i http://127.0.0.1:8000/healthz
df -h
```

Journal visibility depends on your pod's permissions; use dashboard logs if denied. Do not use `sudo`, kill unrelated processes, or launch a second instance to “fix” the service. Use dashboard redeploy/reload controls. `.venv` is deliberately selected by our commands: the generic Python-preset documentation also describes an automatically created `venv` directory, while the supplied observed Git-deploy instance used `.venv`.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| PEP 668 / externally-managed-environment | Use the exact install/build commands; never system-wide pip. |
| `app.py` missing / wrong start filename | Repository root must be selected. Start with `sh start.sh`. |
| Missing environment variables | Fill all six required variables in Env; save. Check `--check-config`. The program also explicitly reads `.env`. |
| API hash/token validation error | Correct the named variable; placeholders in `.env.example` are intentionally not usable secrets. |
| Startup authentication/repository error | Verify Telegram credentials and HF write permission/namespace, branch permissions, and outbound connectivity. No branch fallback occurs. |
| Package executable not found | Workers use the current venv interpreter. No aria2/unrar/FFmpeg command is required. A source requiring one is unsupported. |
| HTTP 502 or 503 | Inspect `app` logs, credentials and health. Check port alignment and `0.0.0.0`; don't switch to a guessed WSGI command. |
| “Another bot instance” / session database locked | Stop the duplicate deployment and use one process/one pod per bot token. Don't delete a session in use. |
| Telegram message inaccessible | Ensure the bot is in the source chat and has access. An owner's optional authorized user session may access their own chats. |
| Bot ignores me | `OWNER_ID` must be your user ID; add other users to `ALLOWED_USERS`. Anonymous admin/channel identities are not allowed. |
| Google Drive quota / HTML / resolver failure | Check sharing and quotas; supply a fresh direct link. The bot doesn't bypass remote restrictions. |
| Stuck in Prepare | Large HF files are hashed first; there is no fake upload counter. Check CPU/disk activity and allow time. |
| Commit failure / concurrent update | Data remains locally; reduce `UPLOAD_WORKERS` and `/retry ID`. |
| Disk full / MAX_FILE_GB / archive limit | `/clean` old terminal tasks, increase storage, reduce concurrency, or adjust documented limits appropriately. |
| RAR extraction fails | Upload the RAR intact or provide ZIP/TAR/7Z. |
| Updates restart a transfer | Interrupted tasks are retained and listed in history; use `/retry ID`. |

## Updates and verification

Pushes to the tracked `main` branch auto-deploy when enabled. Avoid updating during important transfers; restart recovery is explicit. Use the Git tab to inspect the new deployment and rollback source code if needed. A rollback doesn't undo HF commits or restore deleted local data.

For local validation, run `sh build.sh`. It creates/uses `.venv`, installs pinned dependencies, compiles application files and runs unit/local integration tests. Tests use temporary directories and local fixture servers; they do not contact Telegram or upload to Hugging Face. Real network-service credentials are never required for the build.

**19 automated tests passed.** Validation performed during preparation: Python 3.12 clean virtualenv install, dependency consistency, syntax/import checks, shell syntax, missing/invalid environment checks, real local HTTP transfer/redirect tests, archive extraction, traversal rejection, state/retention/retry behavior, worker cancellation, status rendering, transport-progress hook behavior and health-state responses. The HF transport tests substitute the remote API while exercising the real pinned file-stream hook.

**Not verified here:** a live Telegram login/download, an authenticated HF commit, individual third-party host availability, or a deployment on your InstaPods account. Complete the small-file smoke test described above with your credentials. No “100% deployment success” claim is made without that evidence.

## Official platform references

Reviewed on 2026-11-02:

- https://instapods.com/
- https://instapods.com/deploy/github/
- https://instapods.com/about/
- https://docs.instapods.com/presets/python
- https://docs.instapods.com/guides/git-deployment
- https://docs.instapods.com/guides/environment-variables
- https://docs.instapods.com/guides/worker-pods
- https://docs.instapods.com/guides/operations
- https://docs.instapods.com/dashboard/pod-detail

These document Python 3.11/3.12, app root, port 8000, systemd `app`, editable Git deployment commands, environment delivery, persistence, logs and optional worker pods. Your observed kernel 6.8 and Python patch version 3.12.3 are instance observations, not application requirements. No undocumented InstaPods YAML, privilege, binary-install or storage configuration is assumed.

Workflow inspiration: [WZML-X](https://github.com/SilentDemonSD/WZML-X) and [mirror-leech-telegram-bot](https://github.com/anasty17/mirror-leech-telegram-bot). Their source was studied for task phases and status presentation; their download/upload engine stacks are not bundled here.
