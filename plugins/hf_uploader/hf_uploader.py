"""Native WZML-X Hugging Face mirror plugin.

No Telethon bot, web server, Mongo client or background service is started.
Requires HF_TOKEN in the WZML-X host environment; other settings live in
/plugins -> Installed -> hf_uploader -> Settings.
"""

import asyncio
from html import escape
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

from bot import DOWNLOAD_DIR, LOGGER
from bot.core.plugin_manager import PluginBase, get_plugin_manager
from bot.core.tg_client import TgClient
from bot.helper.telegram_helper.message_utils import send_message


JOBS = {}
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
                job["phase"] = "Downloading HTTP"
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
    job["total"] = total
    job["filename"] = override or name
    job["phase"] = "Downloading Telegram"
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


def _hf_upload(file_path, filename, subfolder, cfg, token, repo, repo_type, branch):
    """Blocking Hugging Face SDK work, always executed in a separate thread."""
    from huggingface_hub import HfApi

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
    api.upload_file(
        path_or_fileobj=str(file_path),
        path_in_repo=remote,
        repo_id=repo,
        repo_type=repo_type,
        revision=branch,
        commit_message=f"Upload {remote} from WZML-X",
    )
    return _hf_link(repo, repo_type, branch, remote), remote, False


def _hf_link(repo, repo_type, branch, remote):
    prefix = "datasets/" if repo_type == "dataset" else ""
    return f"https://huggingface.co/{prefix}{repo}/blob/{quote(branch, safe='')}/{quote(remote, safe='/')}"


async def _run(job, message, url, override, subfolder, cfg):
    job_id = job["id"]
    path = None
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
            job["phase"] = "Uploading to HF"
            folder = subfolder if subfolder is not None else _folder(cfg.get("folder", ""))
            upload_task = asyncio.create_task(
                asyncio.to_thread(_hf_upload, path, path.name, folder, cfg, token, repo, repo_type, branch)
            )
            try:
                url_out, remote, skipped = await asyncio.shield(upload_task)
            except asyncio.CancelledError:
                # The underlying synchronous SDK call cannot be interrupted safely.
                # Keep the source file until its worker stops reading it.
                try:
                    await upload_task
                except Exception:
                    pass
                raise
            job["phase"] = "Completed"
            label = "Already exists (skipped)" if skipped else "Uploaded"
            await send_message(
                message,
                f"<b>Hugging Face {label}</b>\n"
                f"<b>File:</b> <code>{escape(remote)}</code>\n"
                f"<b>Repository:</b> <code>{escape(repo)}</code>\n"
                f"<b>Link:</b> <a href=\"{escape(url_out, quote=True)}\">Open on Hugging Face</a>\n"
                f"<b>Task:</b> <code>{job_id}</code>",
            )
    except asyncio.CancelledError:
        job["phase"] = "Cancelled"
        try:
            await send_message(message, f"HF transfer <code>{job_id}</code> cancelled. An HF upload already in progress may still finish remotely.")
        except Exception:
            pass
    except Exception as error:
        job["phase"] = "Failed"
        LOGGER.error("hf_uploader transfer %s failed: %s", job_id, error, exc_info=True)
        try:
            await send_message(message, f"<b>HF transfer failed:</b> <code>{escape(str(error)[:800])}</code>\nTask: <code>{job_id}</code>")
        except Exception:
            pass
    finally:
        JOBS.pop(job_id, None)


async def hfmirror_command(_, message):
    try:
        url, override, subfolder = _request(message)
        cfg = _settings()
        _destination(cfg)
        if len(JOBS) >= int(cfg.get("max_jobs", 2)):
            raise ValueError("All Hugging Face mirror slots are busy. Use /hfstatus.")
        job_id = secrets.token_hex(4)
        job = {"id": job_id, "phase": "Queued", "done": 0, "total": 0,
               "filename": override or "Unknown", "task": None}
        JOBS[job_id] = job
        job["task"] = asyncio.create_task(_run(job, message, url, override, subfolder, dict(cfg)))
        await send_message(
            message, f"HF mirror started. Task: <code>{job_id}</code>\n"
                     "Use /hfstatus for progress or /hfcancel TASK_ID to stop."
        )
    except (ValueError, OSError) as error:
        await send_message(message, f"<b>Usage/error:</b> {escape(str(error))}")


async def hfstatus_command(_, message):
    if not JOBS:
        return await send_message(message, "No active Hugging Face mirror tasks.")
    lines = ["<b>Hugging Face active tasks</b>"]
    for job in list(JOBS.values()):
        done, total = job["done"], job["total"]
        progress = f"{done / 1024**2:.1f} MiB"
        if total:
            progress += f" / {total / 1024**2:.1f} MiB ({done * 100 / total:.1f}%)"
        lines.append(
            f"\n<code>{job['id']}</code> — {escape(job['phase'])}\n"
            f"{escape(job['filename'])}\n{progress}"
        )
    await send_message(message, "\n".join(lines))


async def hfcancel_command(_, message):
    args = (message.text or "").split(maxsplit=1)
    job_id = args[1].strip() if len(args) > 1 else ""
    job = JOBS.get(job_id)
    if not job:
        return await send_message(message, "No active task with that ID.")
    job["task"].cancel()
    await send_message(message, f"Cancellation requested for <code>{job_id}</code>. In-flight HF commits may still complete.")


class HuggingFacePlugin(PluginBase):
    async def on_unload(self):
        for job in list(JOBS.values()):
            if job["task"]:
                job["task"].cancel()
        return True

    async def on_disable(self):
        return await self.on_unload()
