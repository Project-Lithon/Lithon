"""Re-executes `bench_vid_maker` inside WSL (Windows Subsystem for Linux).

Why this exists
---------------
`bench_vid_maker` measures CPython against Lithon, and both engines have to
run *natively* for the numbers to mean anything:

* Lithon's fast tier is an x86-64 **native** tier (`build/tier_runner`);
  running it through a Windows-to-WSL translation layer measures the
  translation layer, not the engine.
* CPython's loop performance under WSL differs from its performance on a
  real Linux host, so the comparison is only honest when *both* engines
  run on the same Linux kernel, side by side.
* CPU affinity (`--cores N`) is only honoured on the Linux side, and the
  WSL VM has its own CPU budget independent of the Windows host.

So instead of measuring "Windows launching a Linux process", this module
re-executes the *whole tool* inside WSL and lets it do the measuring
there. Every number the tool reports is then produced by a Linux process,
and the Windows side only relays the exit code and the output.

The repository lives on a Windows drive, which WSL mounts at
`/mnt/<drive>/...`; the same files are visible on both sides, so no
copying is involved.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

#: Marker set in the child's environment so a re-exec never loops.
_REEXEC_MARKER = "BENCH_VID_MAKER_IN_WSL"


def wsl_executable() -> str | None:
    """Path to `wsl.exe`, or None when this is not Windows / WSL is absent."""
    if not sys.platform.startswith("win"):
        return None
    return shutil.which("wsl.exe") or shutil.which("wsl")


def have_wsl() -> bool:
    """True when this process could plausibly re-exec itself in WSL."""
    if wsl_executable() is None:
        return False
    if os.environ.get(_REEXEC_MARKER):
        return False  # already inside WSL
    return True


def to_wsl_path(path: pathlib.Path | str) -> str:
    """Map a Windows path to its WSL `/mnt/<drive>/...` form.

    Paths that are already POSIX-style are returned unchanged, so the same
    helper works for a config authored on either side.
    """
    text = str(path)
    p = pathlib.PureWindowsPath(text)
    drive = p.drive
    if len(drive) == 2 and drive[1] == ":":
        rest = str(p)[2:].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive[0].lower()}/{rest}" if rest else f"/mnt/{drive[0].lower()}"
    return text.replace("\\", "/")


def wsl_to_win_path(path: str) -> str:
    """Map a WSL `/mnt/<drive>/...` path back to a Windows path."""
    text = path.replace("\\", "/")
    if text.startswith("/mnt/") and len(text) >= 7 and text[5] == "/":
        drive = text[5].upper()
        rest = text[7:]
        return f"{drive}:\\{rest.replace('/', os.sep)}" if rest else f"{drive}:\\"
    return text


def shell_join(command: list[str]) -> str:
    """Join an argv into a single shell command line, quoting each token."""
    return " ".join(_shquote(tok) for tok in command)


def _shquote(token: str) -> str:
    if token and all(c.isalnum() or c in "-_./=:" for c in token):
        return token
    return "'" + token.replace("'", "'\\''") + "'"


def reexec_in_wsl(argv: list[str] | None = None) -> int:
    """Run the current tool inside WSL and return its exit code.

    The child's arguments are translated to their WSL equivalents (the
    config path and any `--save`/`--video` path), the working directory is
    carried across, and the environment marker keeps the child from
    re-execing again.
    """
    exe = wsl_executable()
    if exe is None:
        print(
            "bench-vid-maker: wsl.exe not found; cannot run inside WSL.",
            file=sys.stderr,
        )
        return 2

    args = list(sys.argv[1:] if argv is None else argv)
    wsl_args = _translate_args(args)
    env = dict(os.environ)
    env[_REEXEC_MARKER] = "1"

    # The WSL side runs the *same* installed tool. `bench-vid-maker` is on
    # PATH inside WSL when it was installed into a WSL venv; otherwise fall
    # back to the module invocation next to this file.
    command = _child_command(wsl_args)

    print(
        f"bench-vid-maker: re-executing inside WSL -> {shell_join(command)}",
        file=sys.stderr,
    )
    try:
        proc = subprocess.run([exe, "-e", "bash", "-lc", shell_join(command)], env=env, check=False)
    except OSError as e:
        print(f"bench-vid-maker: could not start WSL: {e}", file=sys.stderr)
        return 2
    return proc.returncode


def _child_command(wsl_args: list[str]) -> list[str]:
    """The command the WSL side runs."""
    exe = shutil.which("bench-vid-maker")
    if exe:
        return [exe, *wsl_args]
    # Fall back to the package's own module entry point, using whichever
    # interpreter is running this process.
    return [sys.executable, "-m", "bench_vid_maker.cli", *wsl_args]


def _translate_args(args: list[str]) -> list[str]:
    """Translate Windows-side arguments into their WSL equivalents.

    Only the flags that carry a filesystem path need translating; the rest
    are passed through verbatim so the child sees exactly the same run.
    """
    path_flags = {"--save", "--video"}
    out: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok in path_flags and i + 1 < len(args):
            out.append(tok)
            out.append(to_wsl_path(args[i + 1]))
            i += 2
            continue
        if "=" in tok and tok.split("=", 1)[0] in path_flags:
            flag, _, value = tok.partition("=")
            out.append(f"{flag}={to_wsl_path(value)}")
            i += 1
            continue
        out.append(tok)
        i += 1
    return out
