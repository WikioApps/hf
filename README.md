# HF Uploader

A Telegram bot that downloads files and sends them to your Hugging Face account. It runs on InstaPods without Docker or root access. Each user chooses their own token, repository and branch in Telegram; MongoDB keeps those settings when you move hosts.

## What it does

- Downloads Telegram documents, videos and other file attachments after you press **Upload**. **Cancel upload** and **For other purpose** leave the original message untouched.
- Downloads direct HTTP files with aria2 and resolves public file hosts, folders, albums and playlists. Separate adapters cover MediaFire, Gofile, Pixeldrain, MEGA, Drive, Dropbox, Internet Archive and many video/image sites. See [supported links](docs/supported-sites.md).
- Uploads to model or dataset repositories, including branches and nested folders. A destination of `movies/cdn` produces `movies/cdn/filename.ext`. Task IDs are never added to repository paths.
- Runs multiple downloads and uploads for the same user. The owner chooses separate concurrency limits; the default is four of each.
- Extracts members one at a time while completed members upload concurrently. A bounded pipeline limits prepared files, reserves disk space and checks available memory. Confirmed uploads are saved before their local file is deleted. Optional archive selection, ZIP creation and 7Z creation are supported.
- Shows task type, concurrent transfer phases, recent byte speed, ETA and cancellation commands. HF uses instrumented HTTP uploads so native Xet cannot bypass the progress counter. Status views contain refresh and pagination controls and disappear when their visible tasks finish.
- Stores user access, destinations, task records, confirmed uploads, archive manifests and Telegram sessions in MongoDB. Failed transfers retain local files for retry.
- Provides a responsive website with private transfer history, live activity and owner access controls. Public statistics are optional and contain counts only.
- Registers Telegram commands automatically. Includes owner-only Speedtest.net results with the provider’s share image and detailed server statistics, plus `/logs` for the current application log.

The optional Dockerfile packages the same service for container hosting such as Orkestr.

This is a Hugging Face uploader. It does not upload files back to Telegram, download torrents, bypass paid access, or promise support for every file-hosting website.

## Deploy with Docker on Orkestr

Use this repository as the build context, branch **main**, with `Dockerfile` at the repository root. The image uses Python 3.12, installs the download/archive helpers during the build, and runs the bot and website as an unprivileged user. It does not require systemd inside the container.

- Set the five required environment variables listed below: `API_ID`, `API_HASH`, `BOT_TOKEN`, `OWNER_ID`, and `DATABASE_URL`.
- Route HTTP traffic to container port **8000**, or set `PORT` to match the port configured in the dashboard.
- Keep the image's default start command. If an override is required, use `sh start.sh`.
- Use `/healthz` for readiness, allowing at least 120 seconds for startup and lease handover. A 503 means the bot is not ready; opening the website alone does not establish readiness.
- Run **one replica** for each bot token. Stop the old deployment before moving hosts.
- If persistent volumes are available, mount one at `/opt/hf/hf-data`, writable by UID/GID **10001**. MongoDB retains settings and sessions, but download files and local logs need this volume to survive container replacement.
- Set the new HTTPS origin in Telegram **/botsettings → Website URL**, then use `/web` to sign in.

The Orkestr console is reachable, but its authenticated deployment controls have not been verified here. Use these values in the corresponding container, environment, port and storage settings available in your account. No platform-specific configuration file is required by this repository.

To build the same image locally:

```sh
docker build -t hf-uploader .
docker run --rm --env-file .env -p 8000:8000 -v hf-data:/opt/hf/hf-data hf-uploader
```

The `.env` file in that local command must contain your credentials and remain untracked. On a hosted deployment, enter credentials in the dashboard instead. The Dockerfile copies only application sources; `.dockerignore` also excludes local credentials and generated data.

## Other Python hosts

Use Python 3.12 on a long-running Linux x86_64 host with subprocesses, writable storage and outbound access to Telegram, MongoDB, Hugging Face and your source hosts. Docker is not required. The same installation/build/start commands below apply. ARM64 helper assets are included, but the complete runtime has been tested on x86_64.

This service is not suitable for request-only serverless functions, browser Python, hosts that prohibit subprocesses, or free tiers that suspend background workers. No repository can remove those platform restrictions. Keep one active instance per bot token and use persistent storage for downloads that must survive redeployments.

## Deploy on InstaPods

Use a Linux x86_64 pod with **Python 3.12**. Python 3.11–3.13 is supported. Start with 2 GB RAM, two CPU cores and enough disk for your largest downloads. Four simultaneous large transfers can need substantially more storage.

1. Open InstaPods and create a Python application with GitHub deployment. Keep HTTP/public networking enabled if you want the website.
2. Select `https://github.com/WikioApps/hf.git`, branch **main**.
3. Add the five environment variables below in the hosting dashboard. Never put real values in GitHub.
4. Open the repository's deployment settings and set the commands explicitly. On an existing pod, these controls are under the Git settings. Save all three commands before deploying.
5. Deploy. Check the installation/build output, then the application logs. A running service logs `Bot connected; commands registered; MongoDB ready` and Uvicorn listens on the configured port.
6. Open your bot in Telegram, press Start, and use `/usettings`. Set your HF token, choose your account or organization, then enter a repository name. Choose the repository type and branch using buttons.
7. In `/botsettings`, set **Website URL** to the HTTPS origin InstaPods assigned, such as `https://your-pod.example.com`. Use `/web` for a private sign-in link.
8. Leave **Allow everyone** off for an owner-only bot. Use `/users` to authorize individual users, or enable it deliberately. New users configure their own `/usettings`.

### Installation

If the dashboard offers a separate installation step:

```sh
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
```

### Build

```sh
sh build.sh --build
```

This command is independently usable: it creates the virtual environment, installs dependencies and user-space archive tools, then checks imports and syntax. It does not run a test suite.

### Start

```sh
sh start.sh
```

Start runs the actual bot and Uvicorn in the foreground. It does not install packages or run tests. The launcher checks `.venv`, `venv`, and the active virtual environment, then uses one with a supported Python version, the pinned dependencies and working application imports. It exports that environment’s `bin` directory and `tools` in `PATH` and uses `exec` so the service manager can stop the application cleanly.

Both observed InstaPods layouts are supported: an explicit build installing into `.venv`, and an auto-detected install using `venv` without a build stage. If InstaPods starts `source ~/app/venv/bin/activate && python app.py`, the launcher uses that environment when it contains the required packages. It can switch to `.venv` when that is the complete environment, or skip an incomplete `.venv` and use `venv`. No packages are installed during startup. Keep the explicit build command: it installs 7-Zip, FFmpeg and FFprobe. Ordinary ZIP/TAR processing uses Python; video merging and richer archive formats need their helpers.

**Do not save `sh build.sh --build` as the Start command.** Earlier deployments selected `sh build.sh` and exited after building. A compatibility check now redirects bare `sh build.sh` to the launcher only when it is actually running inside `app.service`; explicit settings above remain the recommended configuration.

## Environment variables

| Variable | Required | Value |
| --- | --- | --- |
| `OWNER_ID` | Yes | Your numeric Telegram user ID. The owner always has access. |
| `API_ID` | Yes | Telegram application ID from [my.telegram.org](https://my.telegram.org). |
| `API_HASH` | Yes | The corresponding 32-character Telegram API hash. |
| `BOT_TOKEN` | Yes | Bot token from BotFather. |
| `DATABASE_URL` | Yes | MongoDB URI, for example `mongodb+srv://USER:PASSWORD@HOST/hf_mirror`. |
| `PORT` | Platform optional | HTTP port supplied by the host; defaults to `8000`. |

The application loads the repository's `.env` explicitly without replacing environment variables already exported by the host. `.env.example` contains placeholders only. Hugging Face settings are saved through `/usettings`, including for the owner. Old `HF_TOKEN`, `REPO_ID` and branch environment variables are no longer used.

For MongoDB Atlas, create a database user with read/write access to this application's database. Configure network access for your pod's outbound address. Percent-encode special characters in URI passwords. The database is selected from the URI, with `hf_mirror` as the default.

## Telegram commands

| Command | Purpose |
| --- | --- |
| `/start` | Short welcome and setup links. |
| `/help` or `/help mirror` | All commands or detailed help for one command. |
| `/mirror URL` | Download and upload a link. Reply to a file with `/mirror` to use that file. |
| `/usettings` | Your HF token, account, repository, branch, folder and file behavior. Private chat only. |
| `/status`, `/status me` | Active transfers in this chat; the owner can see all users in the chat. |
| `/cancel ID` | Stop a transfer and retain its files. Without an ID, close a settings prompt. |
| `/retry ID` | Retry an interrupted, failed or cancelled task. |
| `/select ID` | Reopen a pending archive selection menu. |
| `/history` | Recent transfers and completion links. The owner sees all users. |
| `/clean ID` | Remove retained local files for a finished task. Remote uploads remain. |
| `/sites` | Public hosts, video sites and galleries, with a link to support details. |
| `/stats` | CPU, memory, storage and current transfer counts. |
| `/web` | Private, single-use website sign-in link, valid for five minutes. |
| `/botsettings` | Owner: access, concurrency, limits and website URL. |
| `/users` | Owner: authorize, block or reset user access. |
| `/speedtest` | Owner: Speedtest.net measurements, result photo when available, and detailed server statistics. Five-minute cooldown after a successful test. |
| `/logs` or `/log` | Owner, private chat: download the current application log as `logs.txt`. |

Owner controls work only in a private chat. The bot registers the ordinary command menu for everyone and an expanded menu for the owner after their first `/start`. Task IDs are used for cancellation and recovery, not remote folder names.

### Upload examples

```text
/mirror https://example.com/video.mp4
/mirror https://example.com/file -n "My video.mp4" -p "movies/cdn"
/mirror https://example.com/archive.zip -e
/mirror https://example.com/archive.7z -e -s
/mirror https://example.com/archive.rar -e -pw "archive password"
/mirror https://example.com/document.pdf -z
/mirror https://example.com/document.pdf -7z
```

Use one URL per line for a batch, each with its own options. Reply to a Telegram file with `/mirror -e -s` to select archive contents. Telegram message links work only when the bot can access the source chat and message. A bot cannot read arbitrary private chats or hidden channel content.

### User settings

Fixed choices use buttons: **model/dataset**, private/public for newly created repositories, **rename/overwrite/skip** for collisions, and **none/extract/zip/7z** processing. The token is verified before saving; its account and organizations become namespace choices. Existing branches are listed, with a separate option to create a branch.

Settings keep a single menu message. Successful text input is deleted and that menu is updated in place. Back and `/cancel` close the pending edit. Reopening the menu removes its previous tracked message; a one-time cleanup also removes recognizable older settings menus/prompts among the preceding 100 message IDs in that private chat. Unrelated messages remain. Deletion depends on Telegram allowing the operation.

Tokens are never shown in settings. Token messages are deleted where Telegram allows it. Send tokens only in the private settings prompt. Private mode affects newly created repositories; it does not change an existing repository's visibility.

Extraction preserves member subfolders. Files with the same name can be renamed, overwritten or skipped according to your settings. Renaming adds ` (1)`, ` (2)` and so on. Simultaneous writes to the same target filename are serialized to prevent local collisions.

## Archives and download engines

The app is written in Python. The normal Python build installs aria2, 7-Zip, FFmpeg and FFprobe into the virtual environment or `tools/`, without apt, sudo, Node.js or system-wide pip. Docker is an optional packaging method; both deployment modes run the same application and transfer code.

- aria2 1.37 is supplied by the pinned Python wheel. Downloads use multiple connections when the source supports range requests.
- The installer downloads official 7-Zip 26.05, verifies its SHA-256 digest, and installs `tools/7zz`. The build fails if download, checksum or execution fails.
- Standard ZIP extraction/creation and TAR-family extraction use Python directly, so ordinary ZIP/TAR operations work even if the native helper is missing. AES ZIP and unsupported ZIP compression methods fall back to 7-Zip.
- ZIP, 7Z, RAR, TAR, TAR.GZ, TAR.BZ2 and TAR.XZ are supported. Other formats readable by the installed 7-Zip may work. Encrypted formats require a supported password scheme.
- Split archives that need several separately uploaded volumes are not assembled automatically. Unsupported, corrupt or incomplete archives fail with a useful error.
- Unsafe paths, archive links, duplicate member names, excessive expanded sizes and excessive file counts are rejected.
- Solid RAR/7Z archives and compressed TARs may need repeated decompression for member-by-member processing. This saves disk space but can be slow.
- yt-dlp handles public videos, audio, bounded playlists and supported HLS/DASH streams. gallery-dl handles supported public galleries and albums. FFmpeg comes from the pinned imageio-ffmpeg wheel; FFprobe comes from a pinned, SHA-256-verified FFmpeg-Builds archive. Neither requires a browser-cookie upload.
- Public Google Drive files use gdown. Private files, quota blocks, CAPTCHA and paid accounts require access this bot does not supply.

Download and upload counts are separate. Queueing begins only when a configured slot or the global task limit is reached. Archive extraction is limited to one process to control resource use; other downloads and uploads can continue.

## Concurrency, storage and memory

The default is four download slots, four upload slots and three files in each preparation pipeline. Multiple tasks from the same user can run together. Extraction itself is limited to one worker globally; extracted members can upload concurrently, and the next member can extract while uploads continue. Collection downloads overlap uploads within the same limits. A collection containing archives also shares a per-task extracted-member limit.

Disk reservations account for expected bytes that have not been written yet, so simultaneous downloads cannot all claim the same free space. The default disk floor is 1 GiB. Unknown-length downloads receive a fixed budget based on remaining capacity and the file-size limit; exceeding it stops that task with an error. Video downloads reserve space for merging. Failed and cancelled files continue to occupy real disk until retried or removed with `/clean`.

Admission checks use available RAM, including Linux container memory limits. Child-worker memory is monitored; an over-budget worker is terminated within its task. New work can show **Wait** while existing uploads free space. A resource wait expires after 15 minutes with instructions to clean files or lower concurrency. These limits reduce exhaustion risk; they cannot guarantee survival if the host kills the whole service or external processes consume its resources.

An uploaded file is deleted only after HF confirms it and the result is saved in MongoDB. A local upload receipt covers the gap between commit and bookkeeping. Archive sources remain until their selected members finish; retry can skip confirmed members. A failure inside one collection stops that collection's remaining children, not unrelated tasks.

For a small host, use two downloads, two uploads and two prepared files in `/botsettings`. Start with 2 GiB RAM and enough disk for input archives, their largest concurrent members and the disk floor. The image build also needs space for dependencies and helper binaries.

## Website and health checks

Uvicorn listens on `0.0.0.0:$PORT`, default `8000`. Configure an HTTP health check at **`/healthz`** if your panel supports it. If it offers a startup grace period, allow at least 120 seconds for lease expiry and Telegram connection; readiness must remain 503 until initialization finishes. It returns 200 only while Telegram is connected, MongoDB was recently checked and the supervisor is alive. It returns 503 when the application is not ready, including `waiting_for_previous_instance` during a lease handover. The HTTP server and public page start immediately while bot initialization runs in the background. Protected APIs remain unavailable until the bot is ready. A failed supervisor shuts the application down with a nonzero exit status.

The public homepage works on mobile and desktop. Detailed tasks, filenames, destinations and user controls require a Telegram-issued sign-in link. Links are consumed once, removed from the browser address bar, and exchanged for a secure HttpOnly cookie lasting 24 hours. Public statistics, when enabled, expose only aggregate counts. User settings stay in Telegram; the website provides transfer controls and owner access/concurrency controls.

Use the exact HTTPS origin in **Website URL**. Browser changes from another origin are rejected. Do not share personal `/web` links; share the plain homepage URL instead.

## Speedtest results

`/speedtest` follows the [WZML-X plugin](https://github.com/SilentDemonSD/WZML-X/tree/wzv3/plugins/speedtest): the Python `speedtest-cli` client measures Speedtest.net download, upload and latency, then requests the provider’s official share image. The caption includes measured transfer sizes, timestamp, server location and sponsor. It uses Speedtest.net services, not an independently generated score or picture. If the provider refuses sharing or Telegram cannot send the photo, the real measured text result remains. Error details go to `/logs`.

## Storage, recovery and moving hosts

MongoDB is the source of truth. User records, owner settings, task sources, archive manifests, confirmed upload results, the Telegram session and portal sessions are stored there. Large archive manifests and results use separate collections to avoid MongoDB's document-size limit.

Downloaded bytes live in a sibling directory named `hf-data`: on the observed InstaPods layout this is `/home/instapod/hf-data`, outside `/home/instapod/app`. Successful tasks remove their working files. Failed/cancelled tasks remain until `/retry` or `/clean`. MongoDB does **not** store downloaded file contents.

After a restart, unfinished records become **Interrupted**. `/retry` uses retained files where present, otherwise downloads the original source again. Confirmed archive uploads are skipped. A host failure immediately after a remote commit but before its MongoDB record is saved can produce a duplicate on retry under the rename policy.

To move hosts, stop the old instance, point the new host at the same database, and keep the same Telegram bot identity and `API_HASH`. A MongoDB lease prevents two instances using the same bot; after an abrupt stop, it can take 60 seconds to expire. The new process waits and retries automatically instead of crashing. It does not start Telegram or recover tasks until it owns the lease. If another live host keeps renewing the lease, the new host continues waiting; stop the old host to complete the handover. Lease timestamps use MongoDB’s clock, and a separate heartbeat keeps the lease alive during Telegram startup and normal operation. Transfer queues are not automatically replayed after migration; use `/history` and `/retry`.

HF tokens, source URLs, archive passwords and Telegram sessions are encrypted before storage. The encryption key is derived from the Telegram API hash and bot ID. **Back up the database and preserve that API hash.** Changing it requires migrating or resetting encrypted records. Rotating the bot token for the same bot does not change the encryption key. Filenames and transfer metadata are not encrypted.

## Logs and troubleshooting

Use `/logs` in the owner’s private Telegram chat to download `logs.txt`. It contains real task acceptance, download/extraction/upload phases, confirmed results, worker tracebacks and service events. The current file rotates at 5 MiB; two previous files remain in `hf-data/logs`. Logging begins with this revision and cannot reconstruct earlier failures. Credentials, archive passwords and source URLs are redacted. This command collects application logs, not unrelated host journals.

Use the pod's deployment and application log views for systemd and build output. If your plan provides SSH or a terminal, these read-only commands match the observed InstaPods service layout:

```sh
cd /home/instapod/app
systemctl status app.service --no-pager
journalctl -u app.service -n 100 --no-pager
curl -i http://127.0.0.1:8000/healthz
.venv/bin/python --version
.venv/bin/aria2c --version
tools/7zz i
```

Use the actual platform port if it differs from 8000. Journal access depends on the privileges provided by your plan. Use the dashboard's Restart or Deploy action to restart services; root privileges are not required by this project.

| Symptom | What to check |
| --- | --- |
| Service exits after a build or “Ran … tests” | Set Start to `sh start.sh`, Build to `sh build.sh --build`. No test suite is included in this version. |
| `ModuleNotFoundError: uvicorn` after a successful install | Deploy the latest launcher: it checks dependencies in both `.venv` and `venv` and selects a complete environment. |
| `Project virtual environment is missing` in an older revision | Update the repository. A valid host-managed `venv` is now accepted even when `.venv` was never created. |
| PEP 668 / externally managed environment | Use the documented virtual-environment commands, never system-wide `pip install`. |
| Missing variables | Add all five required variables, save, then restart. The startup error names missing variables without printing their values. |
| MongoDB timeout | URI, database password, Atlas network rules and outbound connectivity. |
| Waiting for previous instance | A reboot can leave a lease valid for up to 60 seconds. Startup waits automatically. If waiting persists, stop any old pod still running this bot; do not delete a live instance’s lease. |
| `aria2c` or `7zz` missing | Run the build step and inspect its download/checksum output. Do not skip a failed build. |
| `No compatible virtual environment` | The message identifies missing or mismatched packages. Run `sh build.sh --build`, then start with `sh start.sh`. |
| HF 401 / 403 | Set a valid write token in your own `/usettings`; confirm access to the selected organization/repository. |
| Empty user destination after upgrade | Configure `/usettings` again. The previous environment-based HF settings are not imported. |
| Portal sign-in rejected | Set Website URL to the exact HTTPS origin and request a fresh `/web` link. |
| Telegram file cannot be fetched | The bot must have access to its original message; forward it to the bot again if necessary. |
| Disk limit reached | `/clean` retained tasks, lower concurrency, or increase storage. |
| Speedtest fails | Check `/logs` for the provider or connection error. A failed measurement allows another attempt; a successful measurement keeps its text result if photo sharing fails. |
| `Incomplete Telegram download` in older versions | Update this revision: downloads use a file handle so Telethon cannot silently append a filename extension. A real size mismatch is still rejected. |
| Archive `FileNotFoundError` in older versions | Ordinary ZIP/TAR now use Python. RAR/7Z and AES ZIP require the build-installed 7-Zip helper; missing executables now produce a specific repair message and a traceback in `/logs`. |

## Updating

Keep the repository URL and branch `main` in the dashboard. With auto-deploy enabled, pushes should trigger deployment through your configured webhook. Otherwise press **Deploy Now**. Review the build and application logs and check `/healthz` afterward.

Stop the old bot before starting a second host. Keep MongoDB and credentials stable; do not delete `hf-data` if you want to reuse retained files. A source ZIP backup is available from [GitHub](https://github.com/WikioApps/hf/archive/refs/heads/main.zip); it does not include secrets, downloaded data or your MongoDB backup.

## Source layout

See [docs/architecture.md](docs/architecture.md) for the complete file tree and module responsibilities. Bot commands, transfer engines, persistence and website assets are kept in separate folders. There is no committed test suite or Docker configuration.

## Validation and limits

This revision was built in a fresh Python 3.12 virtual environment. Local checks covered syntax/imports, shell and JavaScript syntax, real MongoDB reads/writes and leases, encrypted values, portal authentication and authorization, real aria2 HTTP downloads, and real ZIP/7Z/TAR-family extraction, RAR member extraction and link rejection, concurrent same-user task handling, automatic status deletion, and Uvicorn startup/shutdown on a dynamic port. Lease handover, concurrent acquisition, server-clock expiry, heartbeat renewal during startup, and cancellation while waiting were checked against a real local MongoDB instance. Launcher checks cover `.venv` only, `venv` only, incomplete `.venv` with a complete `venv`, the platform activating the wrong environment, and a supported active environment outside the checkout. These include actual startup from the auto-detected activation command without a build-created `.venv`. This update also checked Telethon filename normalization, ZIP operations without 7-Zip, a real HTTP download with Content-Disposition followed by extraction, settings-menu replacement, log redaction/access controls, and Speedtest photo/text delivery paths. Telegram/HF/Speedtest delivery was isolated where credentials or provider access were unavailable. These checks were run outside the repository.

The Python 3.12 container build was also checked locally, including native helper execution, UID/GID 10001, writable data storage, dynamic HTTP port, missing-configuration errors, readiness before initialization, and SIGTERM shutdown. The local build used a temporary certificate mount for this workspace’s network proxy; no proxy certificate is stored in the shipped Dockerfile or image.

This update additionally checks real HTTP LFS byte reporting after early Hub imports, actual overlapping ZIP extraction/uploads, prompt deletion, same-user collections through real aria2, cancellation isolation, competing disk reservations, zero-byte archive members, MEGA integrity boundaries and rejection of corrupt ciphertext, Drive folder paths, and a generated HLS stream through the bundled media tools. Public GitHub file and Internet Archive item resolution were checked live. Provider response fixtures cover parsing behavior, not a guarantee of live availability.

Live Telegram authentication, real HF commits, all host-specific websites, Speedtest.net and your actual InstaPods or Orkestr deployment require your credentials and network. A successful local build is not a verified production deployment. Archive support depends on the installed engine and the specific archive, and hosting storage/bandwidth limits still apply.

## Platform references

Deployment guidance is based on [InstaPods](https://instapods.com/), [GitHub deployment](https://instapods.com/deploy/github/), [About](https://instapods.com/about/), and the [official Python preset](https://docs.instapods.com/presets/python). The reported `/home/instapod/app`, Python 3.12, `.venv`, `app.service` and port 8000 are also consistent with the supplied pod logs. Plan-specific persistent storage, SSH permissions and health-check controls should be checked in your dashboard.

Interaction patterns are inspired by [WZML-X](https://github.com/SilentDemonSD/WZML-X). This project has its own HF transfer implementation and does not claim full mirror/leech feature parity.

## Source references

Downloader research and implementation boundaries are listed in [docs/sources.md](docs/sources.md). Dependency notices are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
