"""`bench-vid-maker` command line: bench-vid-maker <config.toml> [--save PATH | --no-save]"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import sys
import time

from rich.console import Console

from .config import PROJECT_ROOT, load_config
from .recorder import VideoRecorder
from .ui import run_with_ui
from .wsl import have_wsl, reexec_in_wsl

# Sized so the default rendered frame lands comfortably in 2K+ territory
# with the (larger, more legible) default font_size -- see VideoRecorder's
# cell metrics for how width_chars/height_chars/font_size turn into pixels.
DEFAULT_VIDEO_COLS = 234
DEFAULT_VIDEO_ROWS = 56
DEFAULT_FONT_SIZE = 34


def _wsl_preferred() -> bool:
    """Whether to prefer a WSL run over a Windows-native one by default.

    The Lithon engine's fast tier is an x86-64 native binary; on Windows
    it needs `build/tier_runner.exe`, and the repository is frequently
    built on Linux/WSL only. Rather than failing with a confusing
    "native runner not found" error, the tool measures on WSL when it can.
    Set `BENCH_VID_MAKER_NO_WSL=1` to opt out.
    """
    if os.environ.get("BENCH_VID_MAKER_NO_WSL"):
        return False
    from .wsl import wsl_executable

    if wsl_executable() is None:
        return False
    # A Windows-native engine build is a perfectly good place to measure --
    # only prefer WSL when the repo has no Windows runner at all.
    return not (PROJECT_ROOT / "build" / "tier_runner.exe").exists()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bench-vid-maker",
        description="Run a set of benchmark tasks defined in a TOML config, with a clean, "
        "accurately-timed live UI suited for screen recording.",
    )
    ap.add_argument("config", type=pathlib.Path, help="TOML file describing the tasks to compare")
    ap.add_argument(
        "--save", type=pathlib.Path, default=None, help="write results as JSON to this path"
    )
    ap.add_argument("--no-save", action="store_true", help="do not write a results JSON file")
    ap.add_argument(
        "--video", type=pathlib.Path, default=None,
        help="also render an mp4 of the run to this path (default: results/<config-stem>_<timestamp>.mp4 "
        "when --video is passed with no path)",
    )
    ap.add_argument("--video-only", action="store_true", help="render --video without opening a live terminal UI")
    ap.add_argument("--fps", type=int, default=20, help="video frame rate (default: 20)")
    ap.add_argument(
        "--video-cols", type=int, default=DEFAULT_VIDEO_COLS,
        help=f"video width in character cells (default: {DEFAULT_VIDEO_COLS}, ~2K-class)",
    )
    ap.add_argument(
        "--video-rows", type=int, default=None,
        help=f"video height in character cells (default: {DEFAULT_VIDEO_ROWS}, ~2K-class)",
    )
    ap.add_argument(
        "--cores", type=int, default=None,
        help="pin every task's process to this many CPU cores (overrides the config's 'cores', if any)",
    )
    ap.add_argument(
        "--no-music", action="store_true",
        help="don't mux the procedurally-generated (non-copyright) background music into the video",
    )
    ap.add_argument(
        "--wsl", action="store_true",
        help="re-execute the whole run inside WSL (both engines measured natively on Linux)",
    )
    ap.add_argument(
        "--no-wsl", action="store_true",
        help="never re-execute inside WSL, even when WSL is available",
    )
    args = ap.parse_args(argv)

    # WSL re-exec: the honest place to measure is a Linux kernel with both
    # engines running natively. The child re-enters this same entry point,
    # so the guard below keeps it from re-execing twice.
    if args.wsl and not args.no_wsl and have_wsl():
        return reexec_in_wsl()
    if not args.no_wsl and not args.wsl and have_wsl() and _wsl_preferred():
        return reexec_in_wsl()

    try:
        cfg = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"bench-vid-maker: {e}", file=sys.stderr)
        return 2
    if args.cores is not None:
        cfg = dataclasses.replace(cfg, cores=args.cores)

    recorder = None
    if args.video is not None or args.video_only:
        video_path = args.video
        if video_path is None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            video_path = PROJECT_ROOT / "results" / f"{cfg.path.stem}_{stamp}.mp4"
        video_rows = args.video_rows or DEFAULT_VIDEO_ROWS
        try:
            recorder = VideoRecorder(
                video_path, width_chars=args.video_cols, height_chars=video_rows, fps=args.fps,
                font_size=DEFAULT_FONT_SIZE, music=not args.no_music,
            )
        except FileNotFoundError as e:
            print(f"bench-vid-maker: {e}", file=sys.stderr)
            return 2
    else:
        video_rows = args.video_rows or DEFAULT_VIDEO_ROWS

    console = (
        Console(
            file=open(os.devnull, "w", encoding="utf-8", errors="replace"),
            width=args.video_cols,
            height=video_rows,
        )
        if args.video_only
        else None
    )
    results = run_with_ui(cfg, console=console, recorder=recorder)

    if recorder is not None:
        recorder.close()
        print(f"wrote {recorder.out_path}")

    if not args.no_save:
        out = args.save
        if out is None:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            out = PROJECT_ROOT / "results" / f"{cfg.path.stem}_{stamp}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "title": cfg.title,
                    "config": str(cfg.path),
                    "results": [dataclasses.asdict(r) for r in results],
                },
                indent=2,
            )
        )
        print(f"wrote {out}")

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
