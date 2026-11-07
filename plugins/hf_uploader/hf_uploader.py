"""Hugging Face destination for WZML-X v3's native mirror pipeline.

Downloads and archive operations are delegated to WZML-X itself. Hugging Face
list pages are recovered from Telegram message text and queried live from HF;
there is no plugin task/list cache in MongoDB.
"""

import asyncio
from contextlib import contextmanager, suppress
from html import escape
import io
from os import environ, path as ospath, walk
from pathlib import Path, PurePosixPath
from re import compile as re_compile, sub as re_sub
from threading import Lock
from time import monotonic, time
from urllib.parse import quote

from aiofiles.os import listdir, path as aiopath
from pyrogram.enums import ButtonStyle

from bot import (
    LOGGER, DOWNLOAD_DIR, bot_loop, task_dict, task_dict_lock,
    non_queued_up, non_queued_dl, queue_dict_lock,
)
from bot.core.config_manager import Config
from bot.core.plugin_manager import PluginBase, get_plugin_manager
from bot.helper.ext_utils.bot_utils import sync_to_async
from bot.helper.ext_utils.files_utils import (
    clean_download, get_path_size, join_files, remove_excluded_files,
    is_archive, is_first_archive_split,
)
from bot.helper.ext_utils.status_utils import (
    MirrorStatus, get_readable_file_size, get_readable_time,
)
from bot.helper.ext_utils.task_manager import check_running_tasks, start_from_queued
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.telegram_helper.message_utils import (
    edit_message, send_message, update_status_message,
)
from bot.modules.metadata import apply_metadata_title
from bot.modules.mirror_leech import Mirror


_PLUGIN = "hf_uploader"
_HF_READS = {}
_HF_READ_LOCK = Lock()
_HF_HOOKED = False
_PAGE_SIZE = 8
_SAFE_REPO = re_compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_SAFE_BRANCH = re_compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,95}$")


def _cfg():
    return dict(get_plugin_manager().effective_config(_PLUGIN))


def _folder(text):
    value = str(text or "").strip().strip("/")
    if not value or value == "(root)":
        return ""
    if len(value) > 450 or any(
        p in ("", ".", "..") or "\\" in p or any(ord(c) < 32 for c in p)
        for p in value.split("/")
    ):
        raise ValueError("Invalid Hugging Face folder path")
    return value


def _destination(cfg):
    token = environ.get("HF_TOKEN", "").strip()
    repo = environ.get("HF_REPO_ID", "").strip() or str(cfg.get("repo_id") or "").strip()
    typ = str(cfg.get("repo_type") or "dataset").strip()
    branch = str(cfg.get("branch") or "main").strip()
    if not token:
        raise ValueError("HF_TOKEN missing. Set it privately in your host environment.")
    if not _SAFE_REPO.fullmatch(repo):
        raise ValueError("Set Hugging Face repo_id (owner/repo) in plugin Settings.")
    if typ not in ("model", "dataset"):
        raise ValueError("HF repository type must be model or dataset")
    if not _SAFE_BRANCH.fullmatch(branch) or ".." in branch or "//" in branch or branch.endswith(("/", ".", ".lock")) or "/." in branch:
        raise ValueError("Invalid Hugging Face branch")
    return token, repo, typ, branch


def _link(repo, typ, branch, remote):
    prefix = "datasets/" if typ == "dataset" else ""
    # Keep slashes in remote path while escaping path components.
    return f"https://huggingface.co/{prefix}{repo}/blob/{quote(branch, safe='')}/{quote(remote, safe='/')}"


def _folder_link(repo, typ, branch, remote):
    prefix = "datasets/" if typ == "dataset" else ""
    base = f"https://huggingface.co/{prefix}{repo}/tree/{quote(branch, safe='')}"
    return f"{base}/{quote(remote, safe='/')}" if remote else base


def _hf_list(token, repo, typ, branch, prefix):
    """Read the actual remote files for each page; never use a DB listing."""
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    try:
        from huggingface_hub.hf_api import RepoFile
        tree = api.list_repo_tree(
            repo_id=repo, path_in_repo=prefix or None,
            repo_type=typ, revision=branch, recursive=True,
        )
        records = [item.path for item in tree if isinstance(item, RepoFile)]
    except (AttributeError, TypeError):
        records = api.list_repo_files(repo, repo_type=typ, revision=branch)
    if prefix:
        records = [p for p in records if p.startswith(prefix + "/")]
    return sorted(set(records), key=str.casefold)


def _reader_hook():
    """Reuse the instrumented HTTP-upload mechanism in WikioApps/hf.

    The hook is process-wide but paths have independent reporting callbacks.
    If the pinned Hub version changes, uploads continue without fake progress.
    """
    global _HF_HOOKED
    with _HF_READ_LOCK:
        if _HF_HOOKED:
            return
        environ.setdefault("HF_HUB_DISABLE_XET", "1")
        environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
        try:
            from huggingface_hub import _commit_api, constants
            constants.HF_HUB_DISABLE_XET = True
            constants.HF_HUB_ENABLE_HF_TRANSFER = False
        except (ImportError, AttributeError):
            return
        original = getattr(_commit_api, "_wzmlx_hf_original_tqdm_stream", None)
        if original is None:
            original = getattr(_commit_api, "tqdm_stream_file", None)
            if original is None:
                return
            _commit_api._wzmlx_hf_original_tqdm_stream = original

        @contextmanager
        def tracked(path):
            key = ospath.abspath(os.fspath(path))
            report = _HF_READS.get(key)
            if report is None:
                with original(path) as stream:
                    yield stream
                return

            class Reading(io.BufferedReader):
                def __init__(self, raw):
                    super().__init__(raw)
                    self.last = 0.0
                    self.highest = 0

                def read(self, amount=-1):
                    part = super().read(amount)
                    self.highest = max(self.highest, self.tell())
                    now = monotonic()
                    if now - self.last > 0.35 or not part:
                        report(self.highest)
                        self.last = now
                    return part

            with Reading(io.FileIO(path, "r")) as stream:
                yield stream
                report(stream.highest)

        _commit_api.tqdm_stream_file = tracked
        _HF_HOOKED = True


def _unique_remote(api, repo, typ, branch, remote, collision):
    if collision == "overwrite":
        return remote, False
    if not api.file_exists(repo, remote, repo_type=typ, revision=branch):
        return remote, False
    if collision == "skip":
        return remote, True
    part = PurePosixPath(remote)
    for i in range(1, 10001):
        candidate = str(part.with_name(f"{part.stem} ({i}){part.suffix}"))
        if not api.file_exists(repo, candidate, repo_type=typ, revision=branch):
            return candidate, False
    raise ValueError("Too many files with the same name in the destination")


def _upload_files(listener, records, repo_folder, cfg, destination):
    """Run blocking HF operations outside the bot event loop."""
    from huggingface_hub import CommitOperationAdd, HfApi

    token, repo, typ, branch = destination
    api = HfApi(token=token)
    _reader_hook()
    api.create_repo(repo, repo_type=typ, private=bool(cfg.get("private", True)), exist_ok=True)
    if branch != "main":
        api.create_branch(repo, branch=branch, repo_type=typ, exist_ok=True)

    collision = cfg.get("collision", "rename")
    if collision not in ("overwrite", "rename", "skip"):
        raise ValueError("Invalid HF filename collision option")

    uploaded = skipped = 0
    first_link = ""
    listener.hf_total = sum(p.stat().st_size for p, _ in records)
    listener.hf_done = 0
    listener.hf_last = monotonic()
    listener.hf_rate = 0.0
    listener.hf_phase = "Uploading to HF"
    transferred = 0
    for local_path, relative in records:
        if listener.is_cancelled:
            raise asyncio.CancelledError("Upload stopped")
        target = "/".join(x for x in (repo_folder, relative) if x)
        remote, already = _unique_remote(api, repo, typ, branch, target, collision)
        length = local_path.stat().st_size
        if already:
            skipped += 1
            transferred += length
            listener._report(transferred)
            continue
        if not first_link:
            first_link = _link(repo, typ, branch, remote)
        full_path = str(local_path.resolve())

        def report(read, *, base=transferred, size=length):
            listener._report(base + min(size, read))

        operation = CommitOperationAdd(path_in_repo=remote, path_or_fileobj=full_path)
        _HF_READS[full_path] = report
        try:
            api.preupload_lfs_files(
                repo, [operation], repo_type=typ, revision=branch, num_threads=1,
            )
            listener.hf_phase = "Committing to HF"
            for attempt in range(4):
                try:
                    api.create_commit(
                        repo_id=repo, repo_type=typ, revision=branch,
                        operations=[operation], commit_message=f"Upload {remote} from WZML-X",
                    )
                    break
                except Exception as error:
                    code = getattr(getattr(error, "response", None), "status_code", None)
                    if code not in (409, 429, 502, 503) or attempt == 3:
                        raise
                    from time import sleep
                    sleep(2**attempt)
            uploaded += 1
            transferred += length
            listener._report(transferred)
            listener.hf_phase = "Uploading to HF"
        finally:
            _HF_READS.pop(full_path, None)
    return uploaded, skipped, first_link


class HFUploadStatus:
    """Native status adapter: WZML's own /status, /cancel and refresh buttons."""

    engine = "HuggingFace Hub"

    def __init__(self, listener, gid):
        self.listener = listener
        self._gid = gid

    def gid(self):
        return self._gid

    def task(self):
        return self.listener

    def status(self):
        return MirrorStatus.STATUS_UPLOAD

    def name(self):
        return self.listener.name

    def size(self):
        return get_readable_file_size(self.listener.hf_total)

    def processed_bytes(self):
        return get_readable_file_size(self.listener.hf_done)

    def progress_raw(self):
        return (min(100.0, self.listener.hf_done * 100 / self.listener.hf_total)
                if self.listener.hf_total else 0.0)

    def progress(self):
        return f"{self.progress_raw():.2f}%"

    def speed(self):
        return f"{get_readable_file_size(self.listener.hf_rate)}/s" if self.listener.hf_rate else "-"

    def eta(self):
        if self.listener.hf_rate <= 0 or self.listener.hf_total <= self.listener.hf_done:
            return "-"
        return get_readable_time((self.listener.hf_total - self.listener.hf_done) / self.listener.hf_rate) or "0s"


class HFMirror(Mirror):
    """Inherit WZML's source engines, flags, queue and archive helpers."""

    def __init__(self, client, message):
        super().__init__(client, message, is_uphoster=True)
        self.hf_folder = ""
        self.hf_done = 0
        self.hf_total = 0
        self.hf_rate = 0.0
        self.hf_last = 0.0
        self.hf_mark = 0
        self.hf_phase = ""
        self.hf_worker = None
        self._hf_started = False

    async def before_start(self):
        # Mirror's parser provides -up for a destination; here it means HF folder.
        self.hf_folder = _folder(self.up_dest or _cfg().get("folder", ""))
        if self.category:
            raise ValueError("-gc is for Google Drive. Use -up HF_FOLDER for Hugging Face.")
        self.up_dest = "hf:"
        await super().before_start()
        self.up_dest = "hf:"

    def _set_mode_engine(self):
        super()._set_mode_engine()
        self.mode = (self.mode[0], "#HuggingFace")

    def _report(self, size):
        now = monotonic()
        self.hf_done = min(self.hf_total, max(self.hf_done, int(size)))
        gap = now - self.hf_last
        if gap >= 0.8:
            new = max(0, self.hf_done - self.hf_mark) / gap
            self.hf_rate = new if not self.hf_rate else (self.hf_rate * 0.65 + new * 0.35)
            self.hf_mark = self.hf_done
            self.hf_last = now

    async def cancel_task(self):
        """Called by native /cancel while uploading; no custom cancel UI."""
        self.is_cancelled = True
        # A running HF HTTP commit cannot be interrupted safely. Finish that
        # in-flight file before deleting its local source; stop before next file.

    async def _processed_path(self, path, gid):
        """Reuse the same native WZML-X stages as TaskListener."""
        self.up_dir = self.dir
        self.is_file = await aiopath.isfile(path)
        self.size = await get_path_size(path)
        await remove_excluded_files(self.dir, self.excluded_extensions)
        if self.join and not self.is_file:
            await join_files(path)
        if self.extract:
            original = path
            was_archive = self.is_file and (is_archive(path) or is_first_archive_split(path))
            path = await self.proceed_extract(path, gid)
            if was_archive and path == original:
                raise ValueError("Archive extraction failed; verify format and password")
            if not path or self.is_cancelled:
                raise ValueError("Extraction stopped or failed. Check archive/password.")
            self.is_file = await aiopath.isfile(path)
            self.name = path.replace(f"{self.dir}/", "").split("/", 1)[0]
            self.size = await get_path_size(path)
            self.clear()
            await remove_excluded_files(self.dir, self.excluded_extensions)
        if self.ffmpeg_cmds:
            path = await self.proceed_ffmpeg(path, gid)
            if not path or self.is_cancelled:
                raise ValueError("FFmpeg operation failed or stopped")
            self.is_file = await aiopath.isfile(path)
            self.size = await get_path_size(path)
            self.clear()
        if any((self.metadata_dict, self.audio_metadata_dict, self.video_metadata_dict, self.subtitle_metadata_dict)):
            path = await apply_metadata_title(
                self, path, gid, self.metadata_dict,
                self.audio_metadata_dict, self.video_metadata_dict,
                self.subtitle_metadata_dict,
            )
            if not path or self.is_cancelled:
                raise ValueError("Metadata processing failed or stopped")
            self.clear()
        if self.name_swap:
            path = await self.substitute(path)
            if not path or self.is_cancelled:
                raise ValueError("Filename substitution failed or stopped")
        if self.screen_shots:
            path = await self.generate_screenshots(path)
            if not path or self.is_cancelled:
                raise ValueError("Screenshot generation failed or stopped")
        if self.convert_audio or self.convert_video:
            path = await self.convert_media(path, gid)
            if not path or self.is_cancelled:
                raise ValueError("Media conversion failed or stopped")
            self.clear()
        if self.sample_video:
            path = await self.generate_sample_video(path, gid)
            if not path or self.is_cancelled:
                raise ValueError("Sample generation failed or stopped")
            self.clear()
        if self.compress:
            path = await self.proceed_compress(path, gid)
            if not path or self.is_cancelled:
                raise ValueError("ZIP creation failed or stopped")
            self.clear()
        self.name = ospath.basename(path.rstrip("/"))
        self.is_file = await aiopath.isfile(path)
        self.size = await get_path_size(path)
        return Path(path)

    def _records(self, path, cfg):
        if path.is_file():
            if path.is_symlink():
                raise ValueError("Symlinked files cannot be uploaded")
            return [(path, path.name)], self.hf_folder
        if not path.is_dir():
            raise ValueError("Downloaded data has no uploadable file or folder")
        root = path.resolve()
        records = []
        limit = int(cfg.get("max_files", 5000))
        for folder, dirs, names in walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(folder) / d).is_symlink()]
            for name in names:
                item = Path(folder) / name
                if item.is_symlink() or not item.is_file():
                    continue
                records.append((item, item.relative_to(root).as_posix()))
                if len(records) > limit:
                    raise ValueError(f"Archive has more than {limit} files; increase max_files in plugin Settings")
        if not records:
            raise ValueError("Archive/folder contains no uploadable files")
        # Use the original directory name to keep this upload's remote file list isolated.
        prefix = "/".join(x for x in (self.hf_folder, path.name) if x)
        return sorted(records, key=lambda x: x[1].casefold()), prefix

    async def on_download_complete(self):
        """Normal WZML download callback, but route upload to HF."""
        if self.is_cancelled or self._hf_started:
            return
        self._hf_started = True
        try:
            async with task_dict_lock:
                current = task_dict.get(self.mid)
                if current is None:
                    return
                self.name = current.name()
                gid = current.gid()
            # Names are provided by native WZML download engines.
            if self.folder_name:
                self.name = self.folder_name.strip("/").split("/", 1)[0]
            selected = Path(self.dir) / self.name
            if not selected.exists():
                names = await listdir(self.dir)
                if not names:
                    raise ValueError("WZML download completed but no files were found")
                # ytdlp can produce a thumbnail alongside the intended file.
                selected = Path(self.dir) / next((n for n in names if n != "yt-dlp-thumb"), names[0])
                self.name = selected.name
            if not Config.QUEUE_ALL:
                async with queue_dict_lock:
                    non_queued_dl.discard(self.mid)
                await start_from_queued()
            path = await self._processed_path(str(selected), gid)
            if self.is_cancelled:
                return
            cfg = _cfg()
            destination = _destination(cfg)
            records, prefix = self._records(path, cfg)
            queued, event = await check_running_tasks(self, "up")
            if queued:
                from bot.helper.mirror_leech_utils.status_utils.queue_status import QueueStatus
                async with task_dict_lock:
                    task_dict[self.mid] = QueueStatus(self, gid, "Up")
                await update_status_message(self.message.chat.id, force=True)
                await event.wait()
                if self.is_cancelled:
                    return
            self.hf_total = sum(p.stat().st_size for p, _ in records)
            self.hf_done = 0
            self.hf_mark = 0
            self.hf_last = monotonic()
            self.hf_phase = "Uploading to HF"
            async with task_dict_lock:
                task_dict[self.mid] = HFUploadStatus(self, gid)
            await update_status_message(self.message.chat.id, force=True)
            self.hf_worker = asyncio.create_task(
                asyncio.to_thread(_upload_files, self, records, prefix, cfg, destination)
            )
            try:
                uploaded, skipped, first_url = await asyncio.shield(self.hf_worker)
            except asyncio.CancelledError:
                # Keep local files until the uninterruptible SDK worker has stopped.
                with suppress(Exception):
                    await asyncio.shield(self.hf_worker)
                raise
            if self.is_cancelled:
                await send_message(self.message, "<b>Hugging Face upload stopped.</b> An in-flight file may have reached HF.")
            else:
                token, repo, typ, branch = destination
                try:
                    files = await asyncio.to_thread(_hf_list, token, repo, typ, branch, prefix)
                    text, buttons = _browser(repo, typ, branch, prefix, files, 0, self.user_id,
                                             uploaded=uploaded, skipped=skipped)
                except Exception as browse_error:
                    LOGGER.warning("Upload finished but HF listing failed: %s", browse_error)
                    text = (
                        "<b>〶 Hugging Face Upload Completed</b>\n"
                        f"┠ <b>Repository</b> → <code>{escape(repo)}</code>\n"
                        f"┠ <b>Folder</b> → <code>{escape(prefix or '(root)')}</code>\n"
                        f"┖ <b>Uploaded</b> → {uploaded} files ({skipped} skipped)"
                    )
                    buttons = ButtonMaker()
                    buttons.url_button("Open HF Folder", _folder_link(repo, typ, branch, prefix))
                    buttons = buttons.build_menu(1)
                await send_message(self.message, text, buttons)
            await self._finish()
        except asyncio.CancelledError:
            await self._finish()
        except Exception as error:
            LOGGER.error("HF native mirror failed: %s", error, exc_info=True)
            if not self.is_cancelled:
                await self.on_upload_error(str(error))
            else:
                await self._finish()

    async def _finish(self):
        async with task_dict_lock:
            if task_dict.get(self.mid) is not None:
                task_dict.pop(self.mid, None)
            pending = bool(task_dict)
        async with queue_dict_lock:
            non_queued_dl.discard(self.mid)
            non_queued_up.discard(self.mid)
        await start_from_queued()
        await clean_download(self.dir)
        if pending:
            await update_status_message(self.message.chat.id, force=True)
        else:
            await self.clean()


def _browser(repo, typ, branch, prefix, files, page, owner, uploaded=None, skipped=None):
    total = len(files)
    pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    index = max(0, min(int(page), pages - 1))
    # Metadata lives in this Telegram message and is parsed upon every click.
    # It survives process restarts, bot reloads and Redis/Mongo resets.
    text = (
        "<b>〶 Hugging Face File Explorer</b>\n"
        "┃\n"
        f"┠ <b>Repository:</b> <code>{escape(repo)}</code>\n"
        f"┠ <b>Type:</b> <code>{escape(typ)}</code>\n"
        f"┠ <b>Branch:</b> <code>{escape(branch)}</code>\n"
        f"┠ <b>Folder:</b> <code>{escape(prefix or '(root)')}</code>\n"
        f"┠ <b>Files:</b> {total}\n"
        f"┖ <b>Page:</b> {index + 1}/{pages}\n"
    )
    if uploaded is not None:
        text += f"\n<b>Upload Result:</b> {uploaded} uploaded, {skipped or 0} skipped\n"
    text += "\n<b>Files on Hugging Face</b>\n"
    selected = files[index * _PAGE_SIZE:(index + 1) * _PAGE_SIZE]
    if not selected:
        text += "<i>This folder currently contains no files.</i>"
    for i, remote in enumerate(selected, start=index * _PAGE_SIZE + 1):
        relative = remote[len(prefix):].lstrip("/") if prefix else remote
        label = relative if len(relative) < 170 else relative[:80] + "…" + relative[-70:]
        text += f"{i}. <a href=\"{escape(_link(repo, typ, branch, remote), quote=True)}\">{escape(label)}</a>\n"
    buttons = ButtonMaker()
    if index > 0:
        buttons.data_button("◀ Previous", f"hfpg {owner} {index - 1}", style=ButtonStyle.PRIMARY)
    if index + 1 < pages:
        buttons.data_button("Next ▶", f"hfpg {owner} {index + 1}", style=ButtonStyle.PRIMARY)
    buttons.url_button("Open HF Folder", _folder_link(repo, typ, branch, prefix), position="footer")
    return text, buttons.build_menu(2)


def _parse_browser(message):
    found = {}
    for line in (message.text or "").splitlines():
        for label in ("Repository", "Type", "Branch", "Folder"):
            key = f"{label}:"
            if key in line and line.strip().startswith("┠ "):
                found[label] = line.split(key, 1)[1].strip()
    if not all(found.get(key) for key in ("Repository", "Type", "Branch", "Folder")):
        raise ValueError("This message is not an HF file browser")
    return found["Repository"], found["Type"], found["Branch"], _folder(found["Folder"])


async def hfpage_callback(_, query):
    try:
        parts = query.data.split()
        if len(parts) != 3 or query.from_user.id != int(parts[1]):
            return await query.answer("This file explorer belongs to another user", show_alert=True)
        repo, typ, branch, prefix = _parse_browser(query.message)
        # The token is resolved privately at click time; no credentials or list in callback.
        token = environ.get("HF_TOKEN", "").strip()
        if not token:
            raise ValueError("HF_TOKEN not configured")
        await query.answer("Loading repository files…")
        files = await asyncio.to_thread(_hf_list, token, repo, typ, branch, prefix)
        text, buttons = _browser(repo, typ, branch, prefix, files, int(parts[2]), query.from_user.id)
        await edit_message(query.message, text, buttons)
    except Exception as error:
        LOGGER.warning("HF browser pagination failed: %s", error, exc_info=True)
        with suppress(Exception):
            await query.answer("Unable to load Hugging Face file list", show_alert=True)


async def hfmirror_command(client, message):
    try:
        _destination(_cfg())
        if Config.DISABLE_MIRROR:
            raise ValueError("Mirroring is disabled in WZML-X settings")
        # Accept legacy -p path as an alias of WZML's native -up flag.
        # Keep WZML's own parser for -e PASS, -z PASS, -n, -s, -j and engines.
        if message.text:
            message.text = re_sub(r"(?<!\S)-p(?=\s)", "-up", message.text)
            words = message.text.split()
            if any(x in words for x in ("-i", "-b", "-d", "-yt")):
                raise ValueError("Multi/bulk, seeding and YouTube-upload flags are not supported by /hfmirror.")
        bot_loop.create_task(HFMirror(client, message).new_event())
    except Exception as error:
        await send_message(message, f"<b>HF Mirror:</b> {escape(str(error))}")


class HuggingFacePlugin(PluginBase):
    """No background service, database tables, or own status loops."""

    async def on_unload(self):
        # Running tasks remain represented by native WZML status objects.
        # WZML process shutdown handles their cleanup.
        return True
