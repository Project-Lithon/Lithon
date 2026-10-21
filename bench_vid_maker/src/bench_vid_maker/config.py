"""Loads a comparison config (TOML) into a list of runnable Tasks.

Config shape::

    title = "1,000,000,000-iteration sum: CPython vs Lithon"
    subtitle = "optional, shown under the title"            # optional
    cores = 4                                                # optional: pin every task to N CPU cores

    [[tasks]]
    name = "CPython"
    command = ["{python}", "test_codes_python/Loops/test_1b_fsum.py"]

    [[tasks]]
    name = "Lithon"
    command = ["{lithon}", "test_codes_lithon/Loops/test_1b_fsum.lio"]
    cwd = "."                                                 # optional, relative to repo root

All relative paths (command args and ``cwd``) are resolved against the repo
root, so configs stay short and portable regardless of where you invoke
``bench-vid-maker`` from. ``{python}`` and ``{lithon}`` are expanded to the
interpreters/executables described below; add new placeholders in
``_placeholders`` as new engines get compared.
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import shutil
import sys
import tomllib


def find_repo_root(start: pathlib.Path) -> pathlib.Path:
    """Walk upward from `start` looking for a `.git` directory; fall back to
    the bench_vid_maker project's own parent so the tool still works if it is
    ever copied outside the Lithon repo."""
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start.parent


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]  # .../bench_vid_maker
REPO_ROOT = find_repo_root(PROJECT_ROOT)


def _is_runnable(path: pathlib.Path) -> bool:
    """True when `path` exists *and* can actually be executed on this OS.

    A Windows `.venv/Scripts/lithon.exe` sitting in the repo is a real trap
    when the tool runs inside WSL: the file exists, so a plain `exists()`
    check picks it, and it then fails with a baffling "native runner not
    found at C:\\...\\tier_runner.exe" -- a Windows path, resolved from a
    Linux process. The runner is only usable if this platform can run it.
    """
    if not path.exists():
        return False
    if path.suffix.lower() == ".exe" and not sys.platform.startswith("win"):
        return False  # a Windows binary is useless on Linux/macOS
    if not path.suffix and sys.platform.startswith("win"):
        return False  # a POSIX binary is useless on Windows
    return os.access(path, os.X_OK) or path.suffix.lower() == ".exe"


def _find_lithon() -> str:
    """Locate a `lithon` executable that can run *on this platform*.

    Order:
      1. the repo's own virtualenv (platform-correct subdirectory),
      2. a sibling of the running interpreter (this is what makes
         `~/.venvs/lithon-bench/bin/lithon` in WSL resolve correctly),
      3. `lithon` on PATH,
      4. the repo's `cli.py`, run through the current interpreter -- the
         engine's own source entry point, so a repo that has been cloned
         but not `pip install`ed still works.
    """
    venv_names = (
        (".venv/Scripts/lithon.exe", ".venv/bin/lithon")
        if sys.platform.startswith("win")
        else (".venv/bin/lithon", ".venv/Scripts/lithon.exe")
    )
    for rel in venv_names:
        p = REPO_ROOT / rel
        if _is_runnable(p):
            return str(p)

    # A venv running bench-vid-maker itself (e.g. ~/.venvs/lithon-bench in
    # WSL) that has the repo installed: lithon sits next to our interpreter.
    for name in ("lithon.exe", "lithon"):
        sibling = pathlib.Path(sys.executable).parent / name
        if _is_runnable(sibling):
            return str(sibling)

    found = shutil.which("lithon")
    if found and _is_runnable(pathlib.Path(found)):
        return found

    # Last resort: run the engine straight from its source entry point.
    cli = REPO_ROOT / "cli.py"
    if cli.exists():
        return str(cli)

    raise FileNotFoundError(
        "could not find a runnable `lithon` executable (looked in "
        f"{REPO_ROOT / '.venv/bin'} and {REPO_ROOT / '.venv/Scripts'}, next to "
        f"{sys.executable}, on PATH, and {cli}). Build/install Lithon first."
    )


@dataclasses.dataclass(frozen=True)
class Task:
    name: str
    command: list[str]
    cwd: pathlib.Path


@dataclasses.dataclass(frozen=True)
class BenchConfig:
    title: str
    subtitle: str | None
    tasks: list[Task]
    path: pathlib.Path
    cores: int | None = None  # pin every task to this many CPU cores (affinity); None = unrestricted


def _expand(token: str) -> str:
    if token == "{python}":
        return sys.executable
    if token == "{lithon}":
        return _find_lithon()
    return token


def _expand_command(command: list[str]) -> list[str]:
    """Expand the placeholders in a whole command.

    `{lithon}` may resolve to the engine's Python entry point (`cli.py`)
    when no built executable is available -- in that case the command has
    to be run *through* the interpreter, so the interpreter is prepended
    and the marker is kept out of the final argv.
    """
    expanded: list[str] = []
    for tok in command:
        if tok == "{lithon}":
            lithon = _find_lithon()
            if lithon.endswith(".py"):
                expanded.append(sys.executable)
                expanded.append(lithon)
            else:
                expanded.append(lithon)
        else:
            expanded.append(_expand(tok))
    return expanded


def load_config(path: pathlib.Path) -> BenchConfig:
    path = pathlib.Path(path).resolve()
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    raw_tasks = data.get("tasks")
    if not raw_tasks:
        raise ValueError(f"{path}: no [[tasks]] entries")

    tasks: list[Task] = []
    for i, t in enumerate(raw_tasks):
        if "name" not in t or "command" not in t:
            raise ValueError(f"{path}: tasks[{i}] is missing 'name' or 'command'")
        command = _expand_command(t["command"])
        cwd = REPO_ROOT / t.get("cwd", ".")
        tasks.append(Task(name=t["name"], command=command, cwd=cwd.resolve()))

    return BenchConfig(
        title=data.get("title", path.stem),
        subtitle=data.get("subtitle"),
        tasks=tasks,
        path=path,
        cores=data.get("cores"),
    )
