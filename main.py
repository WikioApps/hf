#!/usr/bin/env python3
"""Wispbyte single-file launcher for https://github.com/WikioApps/hf.

Upload this file and an optional requirements.txt to a clean Wispbyte Python
server. Set the Wispbyte PY_FILE field to ``main.py``. No Docker, sudo, or
credentials embedded in this file are required. If Wispbyte does not export
custom variables, place a private .env beside main.py.

The original HF app and all its code remain upstream. This script installs the
upstream project in its own virtual environment and then replaces this process
with the real application. Persistent state is under ./hf-data in the hosting
server's working directory.
"""

from __future__ import annotations

import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile


# The only upstream source to run. The GitHub source remains public.
REPO_URL = "https://github.com/WikioApps/hf.git"
REPO_BRANCH = "main"
ARCHIVE_URL = "https://github.com/WikioApps/hf/archive/refs/heads/main.zip"

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR / "hf_project"
BUILD_MARKER = PROJECT_DIR / ".wispbyte-build.sha256"
DEFAULT_PORT = 8080
STATUS = "Preparing HF Uploader"
ACTIVE_CHILD: subprocess.Popen | None = None


def log(message: str) -> None:
    print(f"[HF Wispbyte] {message}", flush=True)


def require_host() -> int:
    if platform.system() != "Linux":
        raise RuntimeError("This launcher needs a Linux Wispbyte Python server.")
    if not ((3, 11) <= sys.version_info[:2] < (3, 14)):
        raise RuntimeError(
            f"Python {sys.version.split()[0]} is unsupported. Set the Python runtime to 3.12 "
            "(supported upstream: 3.11-3.13)."
        )
    if platform.machine() not in ("x86_64", "aarch64"):
        raise RuntimeError(f"Unsupported machine architecture: {platform.machine()}.")
    for name in ("WISPBYTE_PORT", "SERVER_PORT", "PORT"):
        raw = os.environ.get(name, "").strip()
        if raw:
            try:
                port = int(raw)
                if not 1 <= port <= 65535:
                    raise ValueError
            except ValueError:
                raise RuntimeError(f"{name} must contain a valid TCP port, got {raw!r}.") from None
            return port
    return DEFAULT_PORT


# Wispbyte/Pterodactyl only exposes variables configured for the running egg.
# Never print their values. The aliases below support alternate naming in panels.
REQUIRED_CREDENTIALS = ("BOT_TOKEN", "API_ID", "API_HASH", "OWNER_ID", "DATABASE_URL")
CREDENTIAL_ALIASES = {
    "BOT_TOKEN": ("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN"),
    "API_ID": ("TELEGRAM_API_ID", "TG_API_ID"),
    "API_HASH": ("TELEGRAM_API_HASH", "TG_API_HASH"),
    "OWNER_ID": ("TELEGRAM_OWNER_ID", "ADMIN_ID", "BOT_OWNER_ID"),
    "DATABASE_URL": ("MONGODB_URI", "MONGO_URI", "MONGODB_URL", "MONGO_URL", "DATABASE_URI", "DB_URI"),
}


def cleaned_value(value: str) -> str:
    """Remove accidental outer whitespace and quotes, never alter inner content."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    """Read a conventional KEY=value file without any third-party packages."""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise RuntimeError(f"Cannot read configuration file {path.name}: {type(error).__name__}") from error
    variables: dict[str, str] = {}
    for lineno, raw in enumerate(content.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            # Ignore ordinary notes, but fail for a malformed credential line.
            if any(line.upper().startswith(key) for key in REQUIRED_CREDENTIALS):
                raise RuntimeError(f"Malformed credential at {path.name} line {lineno}; use KEY=value.")
            continue
        key, value = line.split("=", 1)
        key = key.strip().upper()
        if key not in REQUIRED_CREDENTIALS and not any(key in a for a in CREDENTIAL_ALIASES.values()):
            continue
        value = cleaned_value(value)
        if value:
            variables[key] = value
    return variables


def load_configuration() -> None:
    """Read real container env first, then common private files as fallback.

    A value entered into a hosting UI is not necessarily exported to the Python
    process. We cannot recover secrets the host has not provided. Files are
    checked only in the container's own application directories.
    """
    # Import case-insensitive keys and common aliases from the actual process.
    provided = {key.upper(): cleaned_value(value)
                for key, value in os.environ.items() if isinstance(value, str)}
    sources: list[str] = []
    for name in REQUIRED_CREDENTIALS:
        for alias in (name, *CREDENTIAL_ALIASES[name]):
            value = provided.get(alias, "")
            if value:
                os.environ[name] = value
                sources.append(name)
                break

    # Private files are a fallback only: never overwrite an already provided
    # value, regardless of its source. The common .env.txt case can occur when
    # some file managers automatically append .txt.
    candidates = [
        BASE_DIR / ".env",
        BASE_DIR / ".env.txt",
        BASE_DIR / "config.env",
        BASE_DIR / "env.txt",
        PROJECT_DIR / ".env",
        Path("/home/container/.env"),
    ]
    explicit = os.getenv("HF_ENV_FILE", "").strip()
    if explicit:
        custom = Path(explicit).expanduser()
        candidates.insert(0, custom if custom.is_absolute() else BASE_DIR / custom)

    seen: set[Path] = set()
    found_files: list[str] = []
    for path in candidates:
        canonical = path.resolve()
        if canonical in seen:
            continue
        seen.add(canonical)
        if not path.is_file():
            continue
        found_files.append(str(path))
        values = parse_env_file(path)
        for name in REQUIRED_CREDENTIALS:
            if os.environ.get(name, "").strip():
                continue
            for alias in (name, *CREDENTIAL_ALIASES[name]):
                value = values.get(alias, "")
                if value:
                    os.environ[name] = value
                    break

    available = [name for name in REQUIRED_CREDENTIALS if os.environ.get(name, "").strip()]
    missing = [name for name in REQUIRED_CREDENTIALS if name not in available]
    # Keys/status only — never log secret values, lengths or substrings.
    log("Credential availability: " + ", ".join(
        f"{name}={'available' if name in available else 'MISSING'}" for name in REQUIRED_CREDENTIALS
    ))
    if found_files:
        log("Private config file(s) located: " + ", ".join(
            p.replace(str(BASE_DIR), "<app>") for p in found_files
        ))
    else:
        log("No private .env file found next to main.py or in hf_project/.")

    if missing:
        raise RuntimeError(
            "The running Python process cannot access: " + ", ".join(missing)
            + ". If these are filled in on Wispbyte's site, the active Python "
            "egg is not exporting them. Put a private /home/container/.env file "
            "beside main.py (one KEY=value per line), or ask Wispbyte to expose "
            "those keys to your server. Do not paste credentials in public logs."
        )

    if not re.fullmatch(r"[0-9]+", os.environ["OWNER_ID"]):
        raise RuntimeError("OWNER_ID must be a positive numeric Telegram user ID.")
    if not re.fullmatch(r"[0-9]+", os.environ["API_ID"]):
        raise RuntimeError("API_ID must be numeric.")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", os.environ["API_HASH"]):
        raise RuntimeError("API_HASH must contain 32 hexadecimal characters.")
    if not re.fullmatch(r"[0-9]+:[\w-]+", os.environ["BOT_TOKEN"]):
        raise RuntimeError("BOT_TOKEN does not match Telegram's expected token format.")
    if not os.environ["DATABASE_URL"].startswith(("mongodb://", "mongodb+srv://")):
        raise RuntimeError("DATABASE_URL must start with mongodb:// or mongodb+srv://.")
    log("All five required credentials are available; values are kept private.")


class SetupHandler(BaseHTTPRequestHandler):
    """Temporary HTTP endpoint; the upstream ASGI app replaces it after build."""

    def do_GET(self) -> None:
        message = html.escape(STATUS, quote=True)
        body = (
            "<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta http-equiv='refresh' content='12'>"
            "<title>HF Uploader starting</title>"
            "<style>body{font:16px system-ui;background:#101827;color:#edf2f7;"
            "max-width:660px;margin:12vh auto;padding:24px}"
            "p{color:#bac7d7}code{color:#a5e8e0}</style>"
            "<h1>HF Uploader</h1><p>Preparing the Wispbyte server.</p>"
            f"<p><code>{message}</code></p>"
            "<p>This setup page will automatically refresh. "
            "See the Wispbyte console for deployment logs.</p></html>"
        ).encode("utf-8")
        self.send_response(503 if self.path.split("?", 1)[0] == "/healthz" else 200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


def setup_server(port: int) -> ThreadingHTTPServer | None:
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), SetupHandler)
    except OSError as exc:
        log(f"Temporary HTTP setup page unavailable on port {port}: {exc}")
        return None
    threading.Thread(target=server.serve_forever, daemon=True, name="setup-http").start()
    log(f"Temporary setup page listening on 0.0.0.0:{port}")
    return server


def close_server(server: ThreadingHTTPServer | None) -> None:
    if server is not None:
        server.shutdown()
        server.server_close()


def run(*args: str, cwd: Path = BASE_DIR, env: dict[str, str] | None = None) -> None:
    global ACTIVE_CHILD
    log("Running: " + " ".join(args))
    ACTIVE_CHILD = subprocess.Popen(
        list(args), cwd=str(cwd), env=env or os.environ.copy(), start_new_session=True
    )
    try:
        code = ACTIVE_CHILD.wait()
    finally:
        ACTIVE_CHILD = None
    if code:
        raise RuntimeError(f"Command failed (exit {code}): {' '.join(args)}")


def terminate(signum: int, _frame: object) -> None:
    if ACTIVE_CHILD is not None and ACTIVE_CHILD.poll() is None:
        try:
            os.killpg(ACTIVE_CHILD.pid, signum)
        except ProcessLookupError:
            pass
    raise SystemExit(128 + signum)


def usable_source() -> bool:
    return (PROJECT_DIR / "app.py").is_file() and (PROJECT_DIR / "build.sh").is_file() \
        and (PROJECT_DIR / "requirements.txt").is_file() and (PROJECT_DIR / "start.sh").is_file()


def download_archive() -> None:
    """Fetch a clean public archive if the host has no Git executable."""
    log("Downloading GitHub source archive (Git not available or clone failed)")
    with tempfile.TemporaryDirectory(prefix="hf-source-", dir=BASE_DIR) as name:
        staging = Path(name)
        archive_path = staging / "source.zip"
        request = urllib.request.Request(
            ARCHIVE_URL, headers={"User-Agent": "Wispbyte-HF-Launcher/1.0"}
        )
        length = 0
        with urllib.request.urlopen(request, timeout=90) as source, archive_path.open("wb") as out:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                length += len(chunk)
                if length > 80 * 1024 * 1024:
                    raise RuntimeError("GitHub source archive exceeds the permitted size.")
                out.write(chunk)
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if sum(x.file_size for x in members) > 250 * 1024 * 1024:
                raise RuntimeError("The extracted source is unexpectedly large.")
            for member in members:
                parts = Path(member.filename).parts
                if member.filename.startswith("/") or ".." in parts:
                    raise RuntimeError("Unsafe path in GitHub source archive.")
            archive.extractall(staging / "extract")
        roots = [p for p in (staging / "extract").iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("Unexpected GitHub archive layout.")
        if not (roots[0] / "build.sh").is_file():
            raise RuntimeError("GitHub archive does not contain build.sh.")
        if PROJECT_DIR.exists():
            raise RuntimeError("Cannot replace an existing source directory without Git. Remove it first.")
        shutil.move(str(roots[0]), str(PROJECT_DIR))


def sync_source() -> None:
    if shutil.which("git"):
        try:
            if (PROJECT_DIR / ".git").is_dir():
                run("git", "-C", str(PROJECT_DIR), "fetch", "--depth", "1", "origin", REPO_BRANCH)
                run("git", "-C", str(PROJECT_DIR), "reset", "--hard", "FETCH_HEAD")
            elif not PROJECT_DIR.exists():
                run("git", "clone", "--depth", "1", "--branch", REPO_BRANCH,
                    REPO_URL, str(PROJECT_DIR))
            else:
                log("Using existing archive checkout (no Git metadata)")
        except (OSError, RuntimeError) as exc:
            if usable_source():
                log(f"GitHub update failed, using previously downloaded source: {exc}")
            else:
                # This directory is owned by the launcher, never by user data.
                # Recover a partial clone rather than failing on every restart.
                log(f"Git clone/update failed without usable source: {exc}")
                if PROJECT_DIR.exists():
                    shutil.rmtree(PROJECT_DIR)
                download_archive()
    elif not PROJECT_DIR.exists():
        download_archive()
    else:
        log("Git unavailable; using previously downloaded source")
    if not usable_source():
        if not shutil.which("git"):
            log("Repairing incomplete archive checkout")
            shutil.rmtree(PROJECT_DIR)
            download_archive()
        if not usable_source():
            raise RuntimeError("Incomplete source checkout; expected app.py, build.sh, start.sh and requirements.txt.")


def build_key() -> str:
    digest = hashlib.sha256()
    digest.update(sys.version.encode("utf-8"))
    digest.update(platform.machine().encode("utf-8"))
    for relative in (
        "requirements.txt", "build.sh", "scripts/install_tools.py", "scripts/install_media_tools.py"
    ):
        file = PROJECT_DIR / relative
        digest.update(relative.encode("utf-8"))
        digest.update(file.read_bytes())
    return digest.hexdigest()


def runtime_is_ready() -> bool:
    python = PROJECT_DIR / ".venv" / "bin" / "python"
    required = (
        python,
        PROJECT_DIR / "tools" / "7zz",
        PROJECT_DIR / "tools" / "ffmpeg",
        PROJECT_DIR / "tools" / "ffprobe",
        PROJECT_DIR / ".venv" / "bin" / "aria2c",
    )
    if not all(path.exists() for path in required):
        return False
    result = subprocess.run(
        [str(python), "-c", "import app; p=app.runtime_problem(); "
         "raise SystemExit(0 if p is None else 1)"],
        cwd=PROJECT_DIR, timeout=40, check=False,
    )
    return result.returncode == 0


def prepare_project() -> None:
    key = build_key()
    if BUILD_MARKER.is_file() and BUILD_MARKER.read_text().strip() == key and runtime_is_ready():
        log("Existing virtual environment and helper binaries are ready; skipping install.")
        return
    global STATUS
    STATUS = "Installing pinned Python dependencies and download/archive tools"
    log("Building original HF repository (may be slow on 0.35 vCPU)")
    env = os.environ.copy()
    env["PIP_NO_CACHE_DIR"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    # Upgrade pip INSIDE the project's venv. Wispbyte itself runs its own
    # pip before main.py; we cannot alter the host's pre-start pip notice.
    run(sys.executable, "-m", "venv", ".venv", cwd=PROJECT_DIR, env=env)
    try:
        run(str(PROJECT_DIR / ".venv" / "bin" / "python"), "-m", "pip",
            "install", "--upgrade", "--no-cache-dir", "--disable-pip-version-check", "pip",
            cwd=PROJECT_DIR, env=env)
    except RuntimeError as error:
        log(f"pip upgrade unavailable; continuing with the venv's existing pip: {error}")
    run("sh", "build.sh", "--build", cwd=PROJECT_DIR, env=env)
    if not runtime_is_ready():
        raise RuntimeError("Build completed but Python dependencies or helper binaries failed verification.")
    BUILD_MARKER.write_text(key + "\n")
    log("All pinned dependencies, aria2, 7-Zip, FFmpeg and FFprobe are ready.")


def launch_app(port: int) -> None:
    python = PROJECT_DIR / ".venv" / "bin" / "python"
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    env["VIRTUAL_ENV"] = str(PROJECT_DIR / ".venv")
    env["PATH"] = os.pathsep.join((
        str(PROJECT_DIR / ".venv" / "bin"), str(PROJECT_DIR / "tools"),
        env.get("PATH", ""),
    ))
    env.pop("PYTHONHOME", None)
    os.chdir(PROJECT_DIR)
    log(f"Launching HF Uploader and portal on 0.0.0.0:{port}")
    log("Health endpoint: /healthz (503 until MongoDB and Telegram are ready)")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execve(str(python), [str(python), str(PROJECT_DIR / "app.py")], env)


def main() -> None:
    global STATUS
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    port = require_host()
    os.environ["PORT"] = str(port)
    # Wispbyte egg variables may not be exported. Try private local .env files.
    # Validate before downloading/building the large upstream application.
    load_configuration()
    server = setup_server(port)
    try:
        STATUS = "Fetching latest source from GitHub"
        sync_source()
        prepare_project()
        STATUS = "Starting Telegram, MongoDB and web portal"
    finally:
        close_server(server)
    launch_app(port)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.TimeoutExpired, zipfile.BadZipFile) as error:
        log(f"ERROR: {error}")
        raise SystemExit(1)
