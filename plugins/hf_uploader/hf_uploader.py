"""Native WZML-X Hugging Face mirror plugin.

No Telethon bot, web server, Mongo client or background service is started.
Requires HF_TOKEN in the WZML-X host environment; other settings live in
/plugins -> Installed -> hf_uploader -> Settings.
"""

import asyncio
from contextlib import contextmanager
from html import escape
from io import BufferedReader, FileIO
import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import socket
import tempfile
from time import monotonic, time
from types import SimpleNamespace
from urllib.parse import quote, unquote, urljoin, urlsplit

from aiohttp import ClientSession, ClientTimeout
from pyrogram.enums import ButtonStyle, ChatType

from bot import DOWNLOAD_DIR, LOGGER, task_dict, task_dict_lock
from bot.core.plugin_manager import PluginBase, get_plugin_manager
from bot.core.tg_client import TgClient
from bot.helper.ext_utils.status_utils import (
    MirrorStatus, get_readable_file_size, get_readable_time,
)
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.message_utils import (
    delete_status, send_message, send_status_message, update_status_message,
)


JOBS = {}
_UPLOAD_FILES = {}
_PROGRESS_HOOK_INSTALLED = False
DEFAULT_GB = 1024**3
SAFE_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
FILE_MEDIA = ("document", "video", "audio", "animation", "voice", "video_note", "photo")



def _phase(job, name, total=0):
    """Reset phase accounting so WZML-X shows meaningful speed and ETA."""
    job["phase"] = name
    job["done"] = 0
    job["total"] = int(total or 0)
    job["phase_start"] = monotonic()


class HFNativeStatus:
    """Provide the exact task protocol expected by WZML-X v3 /status and /cancel."""

    def __init__(self, job, message, telegram_source):
        self.job = job
        self.engine = "WzPyro HF-Hub"
        self.listener = SimpleNamespace(
            message=message,
            user_id=job["user_id"],
            name=job["filename"],
            subname="",
            progress=True,
            is_super_chat=message.chat.type in (
                ChatType.SUPERGROUP, ChatType.CHANNEL, ChatType.FORUM
            ),
            is_torrent=False,
            is_qbit=False,
            is_nzb=False,
            mode=("#Telegram" if telegram_source else "#HTTP", "#HuggingFace"),
        )

    def name(self):
        return self.job["filename"]

    def gid(self):
        return self.job["id"]

    def status(self):
        stage = self.job.get("phase", "")
        if stage.startswith("Downloading"):
            return MirrorStatus.STATUS_DOWNLOAD
        if stage == "Queued":
            return MirrorStatus.STATUS_QUEUEDL
        return MirrorStatus.STATUS_UPLOAD

    def processed_bytes(self):
        return get_readable_file_size(self.job.get("done", 0))

    def size(self):
        total = self.job.get("total", 0)
        return get_readable_file_size(total) if total else "Unknown"

    def progress(self):
        done, total = self.job.get("done", 0), self.job.get("total", 0)
        if not total:
            return "0.00%"
        return f"{min(100, max(0, done / total * 100)):.2f}%"

    def _speed(self):
        elapsed = max(0.001, monotonic() - self.job.get("phase_start", monotonic()))
        return self.job.get("done", 0) / elapsed

    def speed(self):
        return f"{get_readable_file_size(self._speed())}/s"

    def eta(self):
        done, total = self.job.get("done", 0), self.job.get("total", 0)
        speed = self._speed()
        if total <= 0 or speed <= 0:
            return "-"
        return get_readable_time((total - min(done, total)) / speed) or "0s"

    def task(self):
        return self

    async def cancel_task(self):
        current = self.job.get("task")
        if current and not current.done():
            current.cancel()


def _buttons(job):
    buttons = ButtonMaker()
    buttons.data_button(
        "♻️ Refresh Status", f"hfu:refresh:{job['id']}",
        style=ButtonStyle.PRIMARY,
    )
    buttons.data_button(
        "✖ Cancel Transfer", f"hfu:cancel:{job['id']}",
        style=ButtonStyle.DANGER,
    )
    return buttons.build_menu(2)


def _hook_hf_progress():
    """Count bytes read for Hub LFS uploads, without serializing concurrent jobs.

    WZML-X's regular /status remains usable even if an SDK transfer path does
    not expose byte-level progress (e.g. Git-based small-file commits).
    """
    global _PROGRESS_HOOK_INSTALLED
    if _PROGRESS_HOOK_INSTALLED:
        return
    from huggingface_hub import _commit_api

    original = _commit_api.tqdm_stream_file

    class MeteredFile(BufferedReader):
        def __init__(self, path, job):
            super().__init__(FileIO(path, "r"))
            self.job = job
            self.high_water = 0

        def read(self, size=-1):
            block = super().read(size)
            self.high_water = max(self.high_water, self.tell())
            self.job["done"] = self.high_water
            return block

    @contextmanager
    def monitor(path):
        key = os.path.abspath(os.fspath(path))
        job = _UPLOAD_FILES.get(key)
        if job is None:
            with original(path) as handle:
                yield handle
        else:
            with MeteredFile(key, job) as handle:
                yield handle

    _commit_api.tqdm_stream_file = monitor
    _PROGRESS_HOOK_INSTALLED = True


def _settings():
    return get_plugin_manager().effective_config("hf_uploader")


def _safe_name(value):
    name = unquote(str(value or "").strip()).replace("\\", "/").split("/")[-1]
    if not name or name in (".", "..") or any(ord(char) < 32 for char in name):
        return "download.bin"
    return name[:240]


def _folder(value):
    raw = str(value or "").strip("/").strip()
    if not raw:
        return ""
    parts = raw.split("/")
    if any(p in ("", ".", "..") or "\\" in p or any(ord(c) < 32 for c in p) for p in parts):
        raise ValueError("Invalid HF folder: use normal / separated subfolders without ..")
    return "/".join(parts)


def _request(message):
    body = (message.text or "").partition(" ")[2]
    args = shlex.split(body)
    url = ""
    name = ""
    folder = None
    i = 0
    while i < len(args):
        word = args[i]
        if word in ("-n", "-p"):
            i += 1
            if i >= len(args):
                raise ValueError(f"{word} requires a value")
            if word == "-n":
                if "/" in args[i] or "\\" in args[i]:
                    raise ValueError("-n must be a filename, not a path; use -p for folders")
                name = _safe_name(args[i])
            else:
                folder = _folder(args[i])
        elif not url and word.startswith(("http://", "https://")):
            url = word
        else:
            raise ValueError(f"Unknown argument: {word}")
        i += 1
    if not url and not message.reply_to_message:
        raise ValueError(
            "Reply to a Telegram file with /hfmirror, or use "
            "/hfmirror https://example.com/file.zip -p backups"
        )
    return url, name, folder


def _source_message(message):
    for kind in FILE_MEDIA:
        media = getattr(message, kind, None)
        if media:
            ext = ".jpg" if kind == "photo" else ".bin"
            name = getattr(media, "file_name", None) or f"telegram_{message.id}{ext}"
            return _safe_name(name), int(getattr(media, "file_size", 0) or 0)
    raise ValueError("The replied Telegram message contains no downloadable media")


def _destination(cfg):
    token = os.getenv("HF_TOKEN", "").strip()
    repo = (os.getenv("HF_REPO_ID", "").strip() or str(cfg.get("repo_id", "")).strip())
    if not token:
        raise ValueError("HF_TOKEN is missing. Add it as a private hosting environment secret.")
    if not SAFE_REPO.fullmatch(repo):
        raise ValueError("Set repo_id in /plugins -> hf_uploader -> Settings (e.g. user/my-files).")
    typ = str(cfg.get("repo_type", "dataset"))
    if typ not in ("dataset", "model"):
        raise ValueError("repo_type must be model or dataset")
    branch = str(cfg.get("branch", "main") or "main").strip()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,95}", branch)
        or ".." in branch
        or branch.endswith(("/", ".", ".lock"))
        or "//" in branch
        or "/." in branch
    ):
        raise ValueError("Invalid repository branch")
    return token, repo, typ, branch


async def _public_url(url):
    """Reject localhost, private IPs and non-HTTP URLs including redirects."""
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username:
        raise ValueError("Only public HTTP(S) file URLs are supported")
    host = parsed.hostname.strip("[]")
    if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local")):
        raise ValueError("Local or private-network URLs are not supported")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        records = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        )
        addresses = [ipaddress.ip_address(rec[4][0]) for rec in records]
    if not addresses or any(not addr.is_global for addr in addresses):
        raise ValueError("The download URL resolves to a non-public address")


def _free_space(directory, min_free_mb):
    free = shutil.disk_usage(directory).free
    if free < int(min_free_mb) * 1024**2:
        raise ValueError("Insufficient free disk space on the WZML-X host")


def _content_filename(headers, url):
    header = headers.get("Content-Disposition", "")
    match = re.search(r"filename\*\s*=\s*(?:UTF-8''|utf-8'')([^;]+)", header, re.I)
    if match:
        return _safe_name(match.group(1).strip('"'))
    match = re.search(r"filename\s*=\s*(?:\"([^\"]+)\"|([^;]+))", header, re.I)
    if match:
        return _safe_name(match.group(1) or match.group(2))
    return _safe_name(urlsplit(url).path.rstrip("/").split("/")[-1] or "download.bin")


async def _http_download(url, directory, override, job, cfg):
    max_bytes = int(cfg.get("max_file_gb", 10)) * DEFAULT_GB
    timeout = ClientTimeout(total=None, connect=30, sock_read=180)
    async with ClientSession(timeout=timeout, trust_env=False) as session:
        for _ in range(9):
            await _public_url(url)
            async with session.get(url, allow_redirects=False, headers={"Accept-Encoding": "identity"}) as response:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise ValueError("Redirect without a Location header")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                if "text/html" in response.headers.get("Content-Type", "").lower():
                    raise ValueError("This URL returns a webpage, not a file. Supply a direct download URL.")
                size = int(response.headers.get("Content-Length") or 0)
                if size > max_bytes:
                    raise ValueError("Source file exceeds the configured max_file_gb limit")
                _phase(job, "Downloading HTTP", size)
                name = override or _content_filename(response.headers, str(response.url))
                target = directory / name
                job["filename"] = name
                _free_space(directory, cfg.get("min_free_mb", 512))
                with target.open("wb") as out:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        job["done"] += len(chunk)
                        if job["done"] > max_bytes:
                            raise ValueError("Downloaded file exceeds the configured limit")
                        if (job["done"] // (16 * 1024**2)) != ((job["done"] - len(chunk)) // (16 * 1024**2)):
                            _free_space(directory, cfg.get("min_free_mb", 512))
                        out.write(chunk)
                if not target.stat().st_size or (size and target.stat().st_size != size):
                    raise ValueError("Empty or incomplete HTTP download")
                return target
    raise ValueError("Too many HTTP redirects")


async def _telegram_download(message, directory, override, job, cfg):
    name, total = _source_message(message)
    limit = int(cfg.get("max_file_gb", 10)) * DEFAULT_GB
    if total > limit:
        raise ValueError("Telegram file exceeds max_file_gb")
    _phase(job, "Downloading Telegram", total)
    job["filename"] = override or name
    _free_space(directory, cfg.get("min_free_mb", 512))

    async def progress(current, expected):
        job["done"] = current
        job["total"] = int(expected or total)
        if current > limit:
            raise ValueError("Telegram file exceeds max_file_gb")
        if (current // (16 * 1024**2)) != ((current - max(1, 1024**2)) // (16 * 1024**2)):
            _free_space(directory, cfg.get("min_free_mb", 512))

    target = directory / job["filename"]
    path = await TgClient.bot.download_media(message, file_name=str(target), progress=progress)
    if not path:
        raise ValueError("Telegram download failed or returned no file")
    actual = Path(path)
    if not actual.is_file() or actual.stat().st_size == 0:
        raise ValueError("Downloaded Telegram file is empty")
    if actual.stat().st_size > limit:
        raise ValueError("Telegram file exceeds max_file_gb")
    if actual != target:
        actual.rename(target)
    return target


def _hf_upload(file_path, filename, subfolder, cfg, token, repo, repo_type, branch, job):
    """Blocking Hub SDK upload, with native /status byte tracking for LFS."""
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    from huggingface_hub import HfApi, constants

    constants.HF_HUB_DISABLE_XET = True
    _hook_hf_progress()
    api = HfApi(token=token)
    api.create_repo(repo_id=repo, repo_type=repo_type, private=bool(cfg.get("private", True)), exist_ok=True)
    if branch != "main":
        api.create_branch(repo_id=repo, branch=branch, repo_type=repo_type, exist_ok=True)
    remote = "/".join(filter(None, (subfolder, filename)))
    collision = str(cfg.get("collision", "rename"))
    if collision not in ("rename", "overwrite", "skip"):
        raise ValueError("Invalid collision setting")
    if collision != "overwrite" and api.file_exists(repo, remote, repo_type=repo_type, revision=branch):
        if collision == "skip":
            return _hf_link(repo, repo_type, branch, remote), remote, True
        path = PurePosixPath(remote)
        for n in range(1, 10000):
            candidate = str(path.with_name(f"{path.stem} ({n}){path.suffix}"))
            if not api.file_exists(repo, candidate, repo_type=repo_type, revision=branch):
                remote = candidate
                break
        else:
            raise ValueError("Too many colliding filenames in the repository")
    key = os.path.abspath(os.fspath(file_path))
    _UPLOAD_FILES[key] = job
    try:
        api.upload_file(
            path_or_fileobj=str(file_path),
            path_in_repo=remote,
            repo_id=repo,
            repo_type=repo_type,
            revision=branch,
            commit_message=f"Upload {remote} from WZML-X",
        )
        job["done"] = job["total"]
    finally:
        _UPLOAD_FILES.pop(key, None)
    return _hf_link(repo, repo_type, branch, remote), remote, False


def _hf_link(repo, repo_type, branch, remote):
    prefix = "datasets/" if repo_type == "dataset" else ""
    return f"https://huggingface.co/{prefix}{repo}/blob/{quote(branch, safe='')}/{quote(remote, safe='/')}"



async def _run(job, message, url, override, subfolder, cfg):
    job_id = job["id"]
    try:
        token, repo, repo_type, branch = _destination(cfg)
        root = Path(DOWNLOAD_DIR)
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="hf-plugin-", dir=root) as tmp:
            directory = Path(tmp)
            if url.startswith((
                "https://t.me/", "https://telegram.me/",
                "https://telegram.dog/", "https://telegram.space/"
            )):
                from bot.helper.telegram_helper.message_utils import TgSource
                messages, requested, *_ = await TgSource.resolve(url, limit=1)
                if requested != 1 or not messages:
                    raise ValueError("Provide one Telegram message link or reply to a file")
                path = await _telegram_download(
                    messages[0], directory, override, job, cfg
                )
            elif url:
                path = await _http_download(url, directory, override, job, cfg)
            else:
                path = await _telegram_download(
                    message.reply_to_message, directory, override, job, cfg
                )
            folder = subfolder if subfolder is not None else _folder(cfg.get("folder", ""))
            _phase(job, "Uploading to HF", path.stat().st_size)
            upload_task = asyncio.create_task(
                asyncio.to_thread(
                    _hf_upload, path, path.name, folder,
                    cfg, token, repo, repo_type, branch, job
                )
            )
            try:
                file_link, remote, skipped = await asyncio.shield(upload_task)
            except asyncio.CancelledError:
                # The SDK's synchronous transfer can still be reading this file.
                # Keep it on disk until the thread actually exits.
                try:
                    await upload_task
                except Exception:
                    pass
                raise
            job["phase"] = "Completed"
            job["done"] = job.get("total", 0)
            buttons = ButtonMaker()
            buttons.url_button(
                "🔗 Open HF File", file_link, style=ButtonStyle.PRIMARY
            )
            repo_prefix = "datasets/" if repo_type == "dataset" else ""
            buttons.url_button(
                "📁 Repository",
                f"https://huggingface.co/{repo_prefix}{repo}",
            )
            state = "Already Exists (Skipped)" if skipped else "Upload Completed"
            await send_message(
                message,
                f"〶 <b><u>Hugging Face {state}</u></b>\n"
                f"┃\n┠ <b>File</b> → <code>{escape(remote)}</code>\n"
                f"┠ <b>Size</b> → {get_readable_file_size(path.stat().st_size)}\n"
                f"┠ <b>Repository</b> → <code>{escape(repo)}</code>\n"
                f"┠ <b>Branch</b> → <code>{escape(branch)}</code>\n"
                f"┠ <b>Out Mode</b> → #HuggingFace\n"
                f"┖ <b>Time</b> → {get_readable_time(time() - job['started']) or '0s'}",
                buttons.build_menu(2),
            )
    except asyncio.CancelledError:
        job["phase"] = "Cancelled"
        try:
            await send_message(
                message,
                f"〶 <b>Hugging Face Transfer Cancelled</b>\n"
                f"┖ <b>Task</b> → <code>{job_id}</code>\n"
                "<i>An in-flight HF commit may still finish remotely.</i>",
            )
        except Exception:
            pass
    except Exception as error:
        job["phase"] = "Failed"
        LOGGER.error(
            "hf_uploader transfer %s failed: %s", job_id, error, exc_info=True
        )
        try:
            await send_message(
                message,
                f"〶 <b>Hugging Face Transfer Failed</b>\n"
                f"┟ <b>Reason</b> → {escape(str(error)[:700])}\n"
                f"┖ <b>Task</b> → <code>{job_id}</code>",
            )
        except Exception:
            pass
    finally:
        JOBS.pop(job_id, None)
        async with task_dict_lock:
            if isinstance(task_dict.get(message.id), HFNativeStatus) and (
                task_dict[message.id].job is job
            ):
                del task_dict[message.id]
            count = len(task_dict)
        try:
            if count == 0:
                await delete_status()
            else:
                await update_status_message(message.chat.id, force=True)
        except Exception as error:
            LOGGER.warning("Could not refresh WZML-X status after HF task: %s", error)


async def hfmirror_command(_, message):
    try:
        url, override, subfolder = _request(message)
        cfg = dict(_settings())
        _destination(cfg)
        if len(JOBS) >= int(cfg.get("max_jobs", 2)):
            raise ValueError("All Hugging Face mirror slots are busy.")
        job_id = secrets.token_hex(8)
        job = {
            "id": job_id,
            "user_id": (message.from_user or message.sender_chat).id,
            "chat_id": message.chat.id,
            "phase": "Queued",
            "phase_start": monotonic(),
            "started": time(),
            "done": 0,
            "total": 0,
            "filename": override or "Preparing...",
            "task": None,
        }
        via_telegram = not url or url.startswith((
            "https://t.me/", "https://telegram.me/",
            "https://telegram.dog/", "https://telegram.space/"
        ))
        native_status = HFNativeStatus(job, message, via_telegram)
        async with task_dict_lock:
            if message.id in task_dict:
                raise ValueError("A task already exists for this command.")
            task_dict[message.id] = native_status
        JOBS[job_id] = job
        job["task"] = asyncio.create_task(
            _run(job, message, url, override, subfolder, cfg)
        )
        # WZML-X now owns this status message, its refresh button and timer.
        try:
            await send_status_message(message)
        except Exception as error:
            LOGGER.warning("Initial WZML-X status could not be sent: %s", error)
        await send_message(
            message,
            f"〶 <b><u>Hugging Face Mirror Started</u></b>\n"
            f"┃\n┟ <b>Engine</b> → WZML-X / HF Hub\n"
            f"┠ <b>Task ID</b> → <code>{job_id}</code>\n"
            f"┖ <b>Status</b> → Automatically updated in the WZML-X task panel",
            _buttons(job),
        )
    except (ValueError, OSError) as error:
        await send_message(message, f"<b>HF Mirror:</b> {escape(str(error))}")


async def hfstatus_command(_, message):
    """Optional shortcut to the SAME native WZML-X status, not a separate UI."""
    if not JOBS:
        return await send_message(message, "No active Hugging Face mirror tasks.")
    await send_status_message(message)


async def hfcancel_command(_, message):
    args = (message.text or "").split(maxsplit=1)
    job_id = args[1].strip() if len(args) > 1 else ""
    job = JOBS.get(job_id)
    if not job or job["user_id"] != (message.from_user or message.sender_chat).id:
        return await send_message(message, "No active Hugging Face task with that ID.")
    current = job.get("task")
    if current and not current.done():
        current.cancel()
    await send_message(
        message, f"Cancellation requested for <code>{escape(job_id)}</code>."
    )


async def hf_action_callback(_, query):
    """Owner-only inline actions, matching WZML-X v3 button styles."""
    try:
        _, action, job_id = query.data.decode().split(":", 2)
        job = JOBS.get(job_id)
        if not job or query.from_user.id != job["user_id"]:
            return await query.answer(
                "This task has finished or is not yours.", show_alert=True
            )
        if action == "refresh":
            await update_status_message(job["chat_id"], force=True)
            return await query.answer("WZML-X status refreshed.")
        if action == "cancel":
            current = job.get("task")
            if current and not current.done():
                current.cancel()
            return await query.answer("Cancellation requested.", show_alert=True)
        return await query.answer("Unknown action.", show_alert=True)
    except Exception as error:
        LOGGER.warning("HF action callback failed: %s", error)
        await query.answer("Unable to perform this action.", show_alert=True)


class HuggingFacePlugin(PluginBase):
    async def on_unload(self):
        for job in list(JOBS.values()):
            if job.get("task"):
                job["task"].cancel()
        return True

    async def on_disable(self):
        return await self.on_unload()
