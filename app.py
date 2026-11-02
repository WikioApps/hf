"""Select the project virtual environment before importing application packages."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def prepare_environment():
    environment = ROOT / '.venv'
    python = environment / 'bin' / 'python'
    if not (environment / 'pyvenv.cfg').is_file() or not os.access(python, os.X_OK):
        raise SystemExit('Project virtual environment is missing. Run: sh build.sh --build')

    os.environ['PATH'] = str(environment / 'bin') + os.pathsep + str(ROOT / 'tools') + os.pathsep + os.environ.get('PATH', '')
    os.environ['VIRTUAL_ENV'] = str(environment)
    os.environ['PYTHONUNBUFFERED'] = '1'
    os.environ.pop('PYTHONHOME', None)
    if Path(sys.prefix).resolve() == environment.resolve():
        os.environ.pop('_HF_RUNTIME_REEXEC', None)
        return

    if os.environ.get('_HF_RUNTIME_REEXEC') == str(environment):
        raise SystemExit('Project Python did not activate .venv. Rebuild with: sh build.sh --build')
    os.environ['_HF_RUNTIME_REEXEC'] = str(environment)
    print('Switching to the project Python environment (.venv).', file=sys.stderr, flush=True)
    os.execve(str(python), [str(python), str(ROOT / 'app.py'), *sys.argv[1:]], os.environ.copy())


def main():
    prepare_environment()
    from bot.core.application import main as run_application
    run_application()


if __name__ == '__main__':
    main()
