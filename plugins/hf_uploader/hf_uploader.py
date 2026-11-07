"""Native WZML-X Hugging Face mirror plugin.

No Telethon bot, web server, Mongo client or background service is started.
Requires HF_TOKEN in the WZML-X host environment; other settings live in
/plugins -> Installed -> hf_uploader -> Settings.
"""

import asyncio
from html import escape
from contextlib import contextmanager
import io
from time import monotonic, time
from types import SimpleNamespace
from threading import Lock
import ipaddress
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import socket
import tempfile
from urllib.parse import quote, unquote, urljoin, urlsplit

from aiohttp import ClientSession, ClientTimeout
from pyrogram.enums import ChatType

from bot import DOWNLOAD_DIR, LOGGER, task_dict, task_dict_lock, intervals
from bot.core.plugin_manager import PluginBase, get_plugin_manager
from bot.core.tg_client import TgClient
from bot.helper.ext_utils.status_utils import (
    MirrorStatus, get_readable_file_size, get_readable_time,
)
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.message_utils import (
    send_message, send_status_message, update_status_message, delete_status,
)


JOBS = {}
_HF_PROGRESS = {}
_HF_STREAM_PATCHED = False
_HF_HOOK_LOCK = Lock()


def _rate(job):
    """Update a smoothed transfer rate without misleading negative speeds."""
    now = monotonic()
    then = job.get("rate_at", now)
    if now - then >= 0.8:
        previous = job.get("rate_done", 0)
        amount = max(0, job["done"] - previous)
        instant = amount / max(0.001, now - then)
        job["bytes_per_second"] = instant if not job.get("bytes_per_second") else (
            0.65 * job["bytes_per_second"] + 0.35 * instant
        )
        job["rate_at"] = now
        job["rate_done"] = job["done"]


def _phase(job, name, *, total=None, reset=False):
    job["phase"] = name
    if total is not None:
        job["total"] = int(total)
    if reset:
        job["done"] = 0
        job["bytes_per_second"] = 0.0
        job["rate_at"] = monotonic()
        job["rate_done"] = 0


class HuggingFaceStatus:
    """Expose HF jobs to the unmodified WZML-X v3 /status and /cancel APIs."""

    def __init__(self, job, message, source):
        self.job = job
        self.listener = SimpleNamespace(
            message=message,
            mid=message.id,
            user_id=message.from_user.id,
            subname="",
            progress=True,
            is_super_chat=message.chat.type in (
                ChatType.SUPERGROUP, ChatType.CHANNEL, ChatType.FORUM,
            ),
            is_qbit=False,
            is_torrent=False,
            is_nzb=False,
            mode=("#TgMedia" if source == "Telegram" else "#HTTP", "#HuggingFace"),
        )
        self.engine = "WzPyro HF" if source == "Telegram" else "AioHttp HF"

    def status(self):
        if self.job["phase"] == "Queued":
            return MirrorStatus.STATUS_QUEUEDL
        if self.job["phase"] in ("Preparing HF upload", "Uploading to HF", "Committing to HF"):
            return MirrorStatus.STATUS_UPLOAD
        return MirrorStatus.STATUS_DOWNLOAD

    def name(self):
        return self.job.get("filename") or "Hugging Face mirror"

    def gid(self):
        return self.job["id"]

    def size(self):
        return get_readable_file_size(self.job["total"])

    def processed_bytes(self):
        return get_readable_file_size(self.job["done"])

    def progress_raw(self):
        total = max(0, self.job["total"])
        return min(100.0, max(0.0, 100.0 * self.job["done"] / total)) if total else 0.0

    def progress(self):
        return f"{self.progress_raw():.2f}%"

    def speed(self):
        if self.job["phase"] in ("Preparing HF upload", "Committing to HF", "Queued"):
            return "-"
        return f"{get_readable_file_size(self.job['bytes_per_second'])}/s"

    def eta(self):
        speed = self.job["bytes_per_second"]
        remaining = self.job["total"] - self.job["done"]
        if speed <= 0 or remaining <= 0:
            return "-"
        return get_readable_time(remaining / speed) or "0s"

    def task(self):
        return self

    async def cancel_task(self):
        """Called by WZML-X's native /cancel handler or cancel-all menu."""
        worker = self.job.get("task")
        if worker and not worker.done():
            worker.cancel()


async def _unregister(job):
    """Remove only our entry; do not disturb other WZML-X tasks."""
    JOBS.pop(job["id"], None)
    async with task_dict_lock:
        if task_dict.get(job["mid"]) is job.get("status_obj"):
            task_dict.pop(job["mid"], None)
        remaining = bool(task_dict)
    if remaining:
        await update_status_message(job["chat_id"], force=True)
    else:
        # Match TaskListener.clean() so the bot does not leave an orphan status page.
        for tick in list(intervals["status"].values()):
            tick.cancel()
        intervals["status"].clear()
        await delete_status()


def _install_hf_progress_hook():
    """Observe HF HTTP file reads while preserving SDK's default path for other calls.

    This is specific to the pinned huggingface_hub==0.36.0 API. Files are
    keyed by absolute path, so concurrent HF jobs do not overwrite callbacks.
    """
    global _HF_STREAM_PATCHED
    with _HF_HOOK_LOCK:
        if _HF_STREAM_PATCHED:
            return
        # The original HF uploader also disables Xet for instrumented HTTP uploads.
        os.environ["HF_HUB_DISABLE_XET"] = "1"
        os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
        from huggingface_hub import _commit_api, constants
        constants.HF_HUB_DISABLE_XET = True
        constants.HF_HUB_ENABLE_HF_TRANSFER = False
        # On WZML plugin reload, unwrap our previous hook instead of nesting it.
        original = getattr(_commit_api, "_wz_hf_original_stream_file", None)
        if original is None:
            original = getattr(_commit_api, "tqdm_stream_file", None)
            if original is None:
                return
            _commit_api._wz_hf_original_stream_file = original

        @contextmanager
        def tracked(path):
            report = _HF_PROGRESS.get(os.path.abspath(os.fspath(path)))
            if report is None:
                with original(path) as stream:
                    yield stream
                return

            class Progress(io.BufferedReader):
                def __init__(self, raw):
                    super().__init__(raw)
                    self.last = 0.0

                def read(self, length=-1):
                    result = super().read(length)
                    now = monotonic()
                    if result and (now - self.last >= 0.3 or self.tell() == self.raw_size):
                        report(self.tell())
                        self.last = now
                    return result

            with Progress(io.FileIO(path, "r")) as stream:
                stream.raw_size = os.path.getsize(path)
                yield stream
                report(stream.tell())

        _commit_api.tqdm_stream_file = tracked
        _HF_STREAM_PATCHED = True

DEFAULT_GB = 1024**3
SAFE_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
FILE_MEDIA = ("document", "video", "audio", "animation", "voice", "video_note", "photo")


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
                job["total"] = size
                name = override or _content_filename(response.headers, str(response.url))
                target = directory / name
                job["filename"] = name
                _phase(job, "Downloading HTTP", total=size)
                _free_space(directory, cfg.get("min_free_mb", 512))
                with target.open("wb") as out:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        job["done"] += len(chunk)
                        _rate(job)
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
    job["total"] = total
    job["filename"] = override or name
    _phase(job, "Downloading Telegram", total=total)
    _free_space(directory, cfg.get("min_free_mb", 512))

    async def progress(current, expected):
        job["done"] = current
        _rate(job)
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
    """Hugging Face SDK operations run in a worker; never block the bot loop."""
    _install_hf_progress_hook()
    from huggingface_hub import CommitOperationAdd, HfApi

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

    source = os.path.abspath(os.fspath(file_path))
    size = os.path.getsize(source)

    def report(done):
        job["phase"] = "Uploading to HF"
        job["done"] = min(size, max(job["done"], int(done)))
        _rate(job)

    operation = CommitOperationAdd(path_in_repo=remote, path_or_fileobj=source)
    _HF_PROGRESS[source] = report
    try:
        api.preupload_lfs_files(
            repo_id=repo, additions=[operation], repo_type=repo_type,
            revision=branch, num_threads=1,
        )
        job["phase"] = "Committing to HF"
        # Treat the commit as complete only after HF confirms it.
        api.create_commit(
            repo_id=repo, operations=[operation], repo_type=repo_type,
            revision=branch, commit_message=f"Upload {remote} from WZML-X",
        )
        job["done"] = size
    finally:
        _HF_PROGRESS.pop(source, None)
    return _hf_link(repo, repo_type, branch, remote), remote, False


def _hf_link(repo, repo_type, branch, remote):
    prefix = "datasets/" if repo_type == "dataset" else ""
    return f"https://huggingface.co/{prefix}{repo}/blob/{quote(branch, safe='')}/{quote(remote, safe='/')}"


async def _run(job, message, url, override, subfolder, cfg):
    upload_task = None
    try:
        token, repo, repo_type, branch = _destination(cfg)
        root = Path(DOWNLOAD_DIR)
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="hf-plugin-", dir=root) as tmp:
            directory = Path(tmp)
            if url.startswith(("https://t.me/", "https://telegram.me/", "https://telegram.dog/", "https://telegram.space/")):
                from bot.helper.telegram_helper.message_utils import TgSource
                messages, requested, *_ = await TgSource.resolve(url, limit=1)
                if requested != 1 or not messages:
                    raise ValueError("Provide one Telegram message link or reply to a file")
                path = await _telegram_download(messages[0], directory, override, job, cfg)
            elif url:
                path = await _http_download(url, directory, override, job, cfg)
            else:
                path = await _telegram_download(message.reply_to_message, directory, override, job, cfg)

            folder = subfolder if subfolder is not None else _folder(cfg.get("folder", ""))
            _phase(job, "Preparing HF upload", total=path.stat().st_size, reset=True)
            job["status_obj"].engine = "HuggingFace Hub"
            await update_status_message(job["chat_id"], force=True)
            upload_task = asyncio.create_task(asyncio.to_thread(
                _hf_upload, path, path.name, folder, cfg, token, repo, repo_type, branch, job,
            ))
            try:
                url_out, remote, skipped = await asyncio.shield(upload_task)
            except asyncio.CancelledError:
                # The SDK's HTTP worker cannot safely be killed. Keep the file
                # on disk until that worker stops reading it, even if cancelled.
                while not upload_task.done():
                    try:
                        await asyncio.shield(upload_task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                raise
            _phase(job, "Completed", total=path.stat().st_size)
            label = "Already exists (skipped)" if skipped else "Uploaded"
            buttons = ButtonMaker()
            buttons.url_button("View on Hugging Face", url_out)
            await send_message(
                message,
                f"<b>〶 Hugging Face {label}</b>\n"
                f"┃\n┠ <b>File</b> → <code>{escape(remote)}</code>\n"
                f"┠ <b>Repository</b> → <code>{escape(repo)}</code>\n"
                f"┠ <b>Size</b> → {get_readable_file_size(path.stat().st_size)}\n"
                f"┖ <b>Out Mode</b> → #HuggingFace",
                buttons.build_menu(1),
            )
    except asyncio.CancelledError:
        job["phase"] = "Cancelled"
        try:
            await send_message(message, "<b>〶 Hugging Face transfer cancelled.</b> An upload already in flight may still finish remotely.")
        except Exception:
            LOGGER.warning("Failed to send Hugging Face cancellation notice", exc_info=True)
    except Exception as error:
        job["phase"] = "Failed"
        LOGGER.error("hf_uploader transfer %s failed: %s", job["id"], error, exc_info=True)
        try:
            await send_message(message, f"<b>〶 Hugging Face transfer failed</b>\n┖ <b>Reason</b> → {escape(str(error)[:800])}")
        except Exception:
            LOGGER.warning("Failed to send Hugging Face failure notice", exc_info=True)
    finally:
        await _unregister(job)


async def hfmirror_command(_, message):
    try:
        url, override, subfolder = _request(message)
        cfg = dict(_settings())
        _destination(cfg)
        if message.from_user is None:
            raise ValueError("Run the command from your Telegram user account.")
        source = "Telegram" if not url or url.startswith(("https://t.me/", "https://telegram.me/", "https://telegram.dog/", "https://telegram.space/")) else "HTTP"
        job_id = secrets.token_hex(8)
        job = {
            "id": job_id,
            "mid": message.id,
            "chat_id": message.chat.id,
            "phase": "Queued",
            "done": 0,
            "total": 0,
            "filename": override or "Hugging Face mirror",
            "task": None,
            "status_obj": None,
            "bytes_per_second": 0.0,
            "rate_at": monotonic(),
            "rate_done": 0,
        }
        obj = HuggingFaceStatus(job, message, source)
        job["status_obj"] = obj
        async with task_dict_lock:
            if len(JOBS) >= int(cfg.get("max_jobs", 2)):
                raise ValueError("All Hugging Face mirror slots are busy. Check /status.")
            if message.id in task_dict:
                raise ValueError("This message already has an active WZML-X task")
            task_dict[message.id] = obj
            JOBS[job_id] = job
        job["task"] = asyncio.create_task(_run(job, message, url, override, subfolder, cfg))
        # If cancelled before the coroutine even starts, its finally block cannot run.
        def ensure_cleanup(done):
            if JOBS.get(job["id"]) is job:
                asyncio.create_task(_unregister(job))
        job["task"].add_done_callback(ensure_cleanup)
        # Use WZML-X's automatic status message, pagination and refresh controls.
        # A single status message per chat is managed by core WZML-X.
        try:
            await send_status_message(message)
        except Exception:
            LOGGER.warning("HF task started but WZML-X status page could not be sent", exc_info=True)
    except (ValueError, OSError) as error:
        await send_message(message, f"<b>Usage/error:</b> {escape(str(error))}")


class HuggingFacePlugin(PluginBase):
    async def on_unload(self):
        pending = [job["task"] for job in JOBS.values() if job.get("task") and not job["task"].done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return True

    async def on_disable(self):
        return await self.on_unload()