"""Select an installed virtual environment before starting the application."""
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def runtime_problem():
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        return 'Python 3.11–3.13 is required; select Python 3.12.'
    if sys.prefix == sys.base_prefix:
        return 'This interpreter is not running in a virtual environment.'
    for line in (ROOT / 'requirements.txt').read_text().splitlines():
        requirement = line.split('#', 1)[0].strip()
        if not requirement:
            continue
        name, separator, expected = requirement.partition('==')
        if not separator:
            return 'Runtime requirements must specify exact versions.'
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            return 'Missing dependency: ' + name
        if installed != expected:
            return f'{name}: installed {installed}, required {expected}'
    try:
        importlib.import_module('bot.core.application')
    except ImportError as error:
        return 'Runtime import failed: ' + (error.name or type(error).__name__)
    return None


def environments():
    candidates = [ROOT / '.venv', ROOT / 'venv']
    if sys.prefix != sys.base_prefix:
        candidates.append(Path(sys.prefix))
    if os.environ.get('VIRTUAL_ENV'):
        candidates.append(Path(os.environ['VIRTUAL_ENV']))
    seen = set()
    for environment in candidates:
        resolved = environment.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        python = environment / 'bin' / 'python'
        if (environment / 'pyvenv.cfg').is_file() and os.access(python, os.X_OK):
            yield environment, python


def check_candidate(environment, python):
    if Path(sys.prefix).resolve() == environment.resolve():
        return runtime_problem()
    code = (
        'import json,runpy,sys; '
        'sys.path.insert(0,sys.argv[1]); '
        'module=runpy.run_path(sys.argv[1]+"/app.py"); '
        'problem=module["runtime_problem"](); '
        'print(json.dumps({"problem":problem}))'
    )
    child_env = os.environ.copy()
    child_env.pop('PYTHONHOME', None)
    child_env['VIRTUAL_ENV'] = str(environment)
    try:
        result = subprocess.run([str(python), '-c', code, str(ROOT)], cwd=ROOT,
                                env=child_env, capture_output=True, text=True, timeout=20)
        if result.returncode:
            return 'Interpreter or application imports failed.'
        return json.loads(result.stdout)['problem']
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        return 'Could not verify this Python environment.'


def select_environment():
    problems = []
    for environment, python in environments():
        problem = check_candidate(environment, python)
        if problem is None:
            return environment, python
        problems.append(f'{environment.name}: {problem}')
    detail = '; '.join(problems) or 'Neither .venv nor venv contains a usable Python installation.'
    raise SystemExit('No compatible virtual environment. ' + detail + '\nRun: sh build.sh --build')


def prepare_environment():
    environment, python = select_environment()
    os.environ['PATH'] = str(environment / 'bin') + os.pathsep + str(ROOT / 'tools') + os.pathsep + os.environ.get('PATH', '')
    os.environ['VIRTUAL_ENV'] = str(environment)
    os.environ['PYTHONUNBUFFERED'] = '1'
    os.environ.pop('PYTHONHOME', None)
    if Path(sys.prefix).resolve() == environment.resolve():
        os.environ.pop('_HF_RUNTIME_REEXEC', None)
        return
    if os.environ.get('_HF_RUNTIME_REEXEC') == str(environment):
        raise SystemExit('The selected Python environment did not activate. Run: sh build.sh --build')
    os.environ['_HF_RUNTIME_REEXEC'] = str(environment)
    print(f'Using Python environment: {environment}', file=sys.stderr, flush=True)
    os.execve(str(python), [str(python), str(ROOT / 'app.py'), *sys.argv[1:]], os.environ.copy())


def main():
    prepare_environment()
    from bot.core.application import main as run_application
    run_application()


if __name__ == '__main__':
    main()
