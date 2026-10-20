# bench_vid_maker

A tiny, reusable Python project (deps: `rich`, `pillow`, `psutil`; Python >= 3.11)
that turns a benchmark comparison (CPython vs Lithon, or anything else) into an
actual video file with a clean, honestly-timed live UI.

Three acts, always in this order:

1. **Typing** -- every task's source types itself onto screen, side by side, at
   40 chars/second. Pure intro; never counted toward any time shown.
2. **Starting** -- one large modal flashes centered over the (now fully typed)
   panels for ~1s and closes the instant the processes launch.
3. **Running** -- every task's process is launched at the *exact same instant*
   under a clock that starts clean at `00:00.000000`. This is the only timed
   part: each panel shows a live spinner, elapsed wall time, and CPU usage
   (cumulative user/system seconds via `psutil`) until exit, then a
   done/failed badge and a tinted background.

Every number is real: `time.perf_counter()` around each subprocess (spawn to
exit) for wall time, `psutil` process accounting for CPU time, six decimals
throughout; CPU% is cumulative CPU over elapsed wall time, not psutil's noisy,
Windows-quantized interval `cpu_percent()`. Tasks are polled every 50ms
(`TICK_SECONDS`) -- polling only drives UI smoothness, never the numbers.

If the running phase exceeds ~10s of real time, the *recorded video* of that
phase (never the live terminal, never the numbers shown) is time-lapsed down
to roughly that length, and the applied speed-up (e.g. "~175x", rounded to a
clean step) is reported on screen. A closing full-height summary shows a
"Time allocation" table (wall, CPU user/sys, share, bar, vs-slowest), an
environment block (OS, CPU + cores, RAM, Python, resolved commands, video
settings), and a Chrome-DevTools-style resource waterfall splitting each bar
into CPU user / sys / idle-wait on a shared time axis.

## Run the included comparison (WSL)

`bench_vid_maker` measures **inside WSL Ubuntu** -- both engines execute as
native Linux processes on the same OS, and static ffmpeg is easy to get there.
The repo lives at `/mnt/c/Users/tenuk/Projects/Lithon`; after the one-time
setup in the appendix below, the tool lives in `~/.venvs/lithon-bench`:

```
~/.venvs/lithon-bench/bin/bench-vid-maker bench_vid_maker/configs/fsum_1k.toml   # live UI
~/.venvs/lithon-bench/bin/bench-vid-maker bench_vid_maker/configs/fsum_1b.toml --video results/fsum_1b.mp4
~/.venvs/lithon-bench/bin/bench-vid-maker-all                                    # every config -> results/
```

### Measuring from Windows

You do not have to switch shells. On Windows the tool **re-executes itself
inside WSL** and lets WSL do the measuring, so both engines still run as
native Linux processes:

```
bench-vid-maker bench_vid_maker/configs/fsum_1b.toml --video results/fsum_1b.mp4
```

The re-exec happens automatically when the repo has no Windows-native
`tier_runner.exe` (which is the usual case: the engine is built on Linux).
Force or suppress it explicitly:

| flag | effect |
|---|---|
| `--wsl` | always re-execute inside WSL |
| `--no-wsl` | never re-execute inside WSL |
| `BENCH_VID_MAKER_NO_WSL=1` | environment equivalent of `--no-wsl` |

This matters for the numbers, not just for convenience:

- Lithon's fast tier is an x86-64 **native** tier (`build/tier_runner`); a
  Windows-to-WSL translation layer would measure the layer, not the engine.
- CPython's loop performance under WSL differs from its performance on a
  real Linux host, so the comparison is only honest when *both* engines run
  on the same Linux kernel.
- `--cores N` (CPU affinity) is only honoured on the Linux side.

### The engine is resolved for the platform it will run on

`{lithon}` is looked for in this order:

1. the repo's own virtualenv, using the subdirectory for the *current*
   platform (`.venv/bin/lithon` on Linux, `.venv/Scripts/lithon.exe` on
   Windows),
2. a sibling of the running interpreter -- this is what makes
   `~/.venvs/lithon-bench/bin/lithon` in WSL resolve correctly,
3. `lithon` on `PATH`,
4. the repo's `cli.py`, run through the current interpreter, so a repo that
   was cloned but never `pip install`ed still works.

A binary that exists but cannot run on this platform is skipped rather than
used: a Windows `lithon.exe` found from inside WSL is ignored, instead of
failing later with a confusing "native runner not found at C:\\...".

### Included configs

All in `bench_vid_maker/configs/`. Each one runs a `test_codes_python/**.py`
program against its `test_codes_lithon/**.lio` twin, launched together on
the same program.

| config | what it measures |
|---|---|
| `fsum_1k.toml` | 1,000-iteration sum; finishes in well under a second, so it is for checking the UI/video itself, not for speedup numbers |
| `fsum_1b.toml` | the real 1,000,000,000-iteration sum comparison |
| `fsum_1b_{1,4,6,8}core.toml` | the same with every task pinned to 1/4/6/8 CPU cores (`cores = N`; `--cores N` overrides any config) |
| `fsum_1b_simd.toml` | 1e9 half-increments into a `float[64]` -- the case the auto-vectorizer targets |
| `multi_ops_1b.toml` | 1e9 iterations of mixed integer arithmetic (multiply, xor, shift) |
| `euler_lim.toml` | a nested-loop workload (approximating `e` via its limit definition out to i=1,000) |
| `find_e.toml` | 8,000 terms of `1/n!`, computed with an explicit factorial loop |
| `fib_30.toml` | recursive `fib(30)` -- call overhead rather than loop throughput |

Windows-native works as a secondary option: it needs the repo's `.venv` to
contain the built engine (`.venv/Scripts/lithon.exe`), `ffmpeg` on `PATH`, and
the fonts below -- then `pip install -e bench_vid_maker` (or `uv`) and the
same commands.

## How the video is made

Each frame (the same `rich` renderable shown live in the terminal) is
rasterized to an RGB image with Pillow -- honoring every truecolor fg/bg,
bold, and dim -- on a cell grid derived from the font's metrics, and streamed
into `ffmpeg`'s stdin as raw `rgb24`; nothing but the final `.mp4` touches
disk. Dimensions are bumped to even numbers (libx264/yuv420p require it);
encoding is libx264/yuv420p with `+faststart`.

During the typing/starting/summary acts, each frame is held for exactly as
many ticks as real wall-clock time passed, so playback matches the real run
regardless of rasterizing/encoding speed. The *running* act is buffered
instead, then time-lapsed (once the real duration is known) to roughly
`RUNNING_VIDEO_TARGET_SECONDS` (10s, see `ui.py`); the final running frame is
held ~2s, the summary ~8s. Every number any frame shows remains the real
measurement -- only *playback speed* changes.

Text is rasterized with **JetBrains Mono Nerd Font**, looked for in this
order: `C:/Windows/Fonts/JetBrainsMonoNerdFontMono-*.ttf` (Windows),
`~/.local/share/fonts/JetBrainsMonoNerdFontMono-*.ttf` (WSL/Linux), Consolas
(`consola.ttf`), then DejaVu Sans Mono. Only icon glyphs confirmed present in
the Nerd Font's cmap are used in the UI. Stdio is reconfigured to UTF-8 first
so legacy Windows code pages can't crash a run mid-render.

Background music is **on by default**: two `lavfi` inputs synthesized by
ffmpeg itself at mux time -- a four-note A3 minor-7th arpeggio (A, C, E, G)
at 128 BPM in 16th notes under a 55 Hz (A1) pulsing bass -- mixed, faded in,
limited, muxed as AAC 160k; `-shortest` stops it when the video (stdin) ends.
No copyright to clear; `--no-music` skips it.

### Flags

| flag | default | meaning |
|---|---|---|
| `config` (positional) | — | TOML file describing the tasks to compare |
| `--save PATH` | `results/<config-stem>_<timestamp>.json` | write the results JSON to this path |
| `--no-save` | off | don't write any results JSON (saved by default) |
| `--video PATH` | — | also render an mp4 of the run to this path |
| `--video-only` | off | render the video without opening a live terminal UI |
| `--video-rows N` | 56 | video height in character cells (~2K-class at the default font size) |
| `--fps N` | 20 | video frame rate |
| `--video-cols N` | 234 | video width in character cells (~2K-class) |
| `--cores N` | the config's `cores`, else unrestricted | pin every task's process to N CPU cores |
| `--no-music` | off | don't mux in the procedurally-generated background music |
| `--wsl` | auto | re-execute the whole run inside WSL (both engines measured natively on Linux) |
| `--no-wsl` | off | never re-execute inside WSL, even when WSL is available |

With `--video-only` and no `--video` path, the mp4 goes to
`results/<config-stem>_<timestamp>.mp4`. Exit codes: `0` all tasks exited 0;
`1` at least one task failed; `2` bad/missing config, or video requested but
ffmpeg/fonts are missing.

## Config reference

Full TOML shape (`config.py`):

```toml
title = "1,000,000,000-iteration sum: CPython vs Lithon"   # defaults to the file's stem if omitted
subtitle = "optional, shown under the title"                # optional
cores = 4                                                   # optional: pin every task to N CPU cores

[[tasks]]
name = "CPython"
command = ["{python}", "test_codes_python/Loops/test_1b_fsum.py"]

[[tasks]]
name = "Lithon"
command = ["{lithon}", "test_codes_lithon/Loops/test_1b_fsum.lio"]
cwd = "."                                                   # optional, relative to repo root
```

Every task needs `name` and `command`; at least one `[[tasks]]` entry is
required or the run exits 2. All relative paths (command args and `cwd`)
resolve against the repo root -- found by walking up to the nearest `.git` --
so configs stay short and portable regardless of where you invoke the tool.

Placeholders expanded in every command token:

- `{python}` -- the interpreter running bench-vid-maker (`sys.executable`).
- `{lithon}` -- the engine, looked for in order: (1) `<repo>/.venv/Scripts/lithon.exe`,
  (2) `<repo>/.venv/bin/lithon`, (3) a sibling of the running interpreter
  (`lithon.exe` on Windows, `lithon` elsewhere -- this is what makes
  `~/.venvs/lithon-bench/bin/lithon` in WSL resolve correctly), (4) `lithon`
  on `PATH`. Missing everywhere is an error (exit 2). Add more placeholders in
  `src/bench_vid_maker/config.py::_expand` as new engines get compared.

## Add a new comparison

Paired test programs live in matching category folders -- the Python tree
mirrors the Lithon tree exactly, so a program and its twin are always one
folder apart:

```
test_codes_python/{Classes,DataTypes,Functions,Loops,NewTypes,Pointers,Structs}/*.py
test_codes_lithon/{Classes,DataTypes,Functions,Loops,NewTypes,Pointers,Structs}/*.lio
```

Lithon is python-syntax, so the same program shape runs as-is in both engines;
the Python side only mirrors the portable subset. Where a construct has no
CPython equivalent -- raw pointers, packed structs, width annotations -- the
Python file keeps the *shape* of the program and says so in its docstring.

Some programs are deliberately **pinned failures** on the Lithon side: they
exercise a gap that has not landed yet, and the engine is expected to refuse
them (see `test_codes_lithon/DataTypes/check_strings.lio`,
`test_codes_lithon/Pointers/pointer_error.lio` and
`test_codes_lithon/Classes/class_basics.lio`). Do not "fix" those files --
they are the record of what is missing.

Drop your program(s) into a matching category, then write a config, e.g.
`configs/my_comparison.toml`:

```toml
title = "Nested loops: CPython vs Lithon"
subtitle = "optional subtitle"

[[tasks]]
name = "CPython"
command = ["{python}", "test_codes_python/Loops/test_1b_loop.py"]

[[tasks]]
name = "Lithon"
command = ["{lithon}", "test_codes_lithon/Loops/test_1b_loop.lio"]
# cwd = "."   # optional, relative to the repo root
```

Any number of `[[tasks]]` entries works -- they all type in together, then
launch together, side by side. Then:

```
~/.venvs/lithon-bench/bin/bench-vid-maker bench_vid_maker/configs/my_comparison.toml --video results/my_comparison.mp4
```

The typewriter animation reads the task's last command argument as source; if
that isn't a real file (e.g. `python -c "..."`), the panel shows the raw
command line instead. For statistically rigorous (multi-run, median/min/max)
numbers rather than a single on-camera run, use `tools/bench.py` in the repo
root instead.

## bench-vid-maker-all

`bench-vid-maker-all [CONFIGS_DIR] [--out-dir DIR] [-- EXTRA_ARGS]` builds a
video + results JSON for every `configs/*.toml` (default dir `configs/`,
default out dir `results/`), writing `<stem>.mp4` and `<stem>.json` per
config. It runs each config sequentially as `--video-only` on purpose --
concurrent CPU-bound runs would make every number meaningless. Anything after
`--` is forwarded to every invocation, e.g. `bench-vid-maker-all -- --no-music
--fps 30`. Exit `0` when all built, `1` if any failed, `2` if no configs found.

## results/ outputs

`bench_vid_maker/results/` holds one `.mp4` and one `.json` per comparison.
The mp4s are committed; `results/*.json` are gitignored (see
`bench_vid_maker/.gitignore`).

## Appendix: one-time WSL setup

All user-space, no sudo. Venv (bootstrapped `--without-pip` via get-pip.py)
plus this project installed editable:

```sh
python3 -m venv --without-pip ~/.venvs/lithon-bench
curl -sS https://bootstrap.pypa.io/get-pip.py | ~/.venvs/lithon-bench/bin/python3
~/.venvs/lithon-bench/bin/pip install rich pillow psutil
~/.venvs/lithon-bench/bin/pip install -e /mnt/c/Users/tenuk/Projects/Lithon/bench_vid_maker
```

A tiny `lithon` wrapper next to the venv's python is all it takes for
`{lithon}` to resolve (it execs the repo's engine CLI):

```sh
cat > ~/.venvs/lithon-bench/bin/lithon <<'EOF'
#!/bin/sh
exec python3 /mnt/c/Users/tenuk/Projects/Lithon/cli.py "$@"
EOF
chmod +x ~/.venvs/lithon-bench/bin/lithon
```

Static ffmpeg: grab a `linux-amd64` build (johnvansickle.com or
BtbN/FFmpeg-Builds), extract `ffmpeg` into `~/.local/bin`, and put
`~/.local/bin` on `PATH` in `~/.bashrc`:

```sh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

JetBrains Mono Nerd Font into `~/.local/share/fonts` (where the recorder looks
on Linux/WSL), then rebuild the font cache:

```sh
mkdir -p ~/.local/share/fonts
curl -sSLO https://github.com/ryanoasis/nerd-fonts/releases/latest/download/JetBrainsMono.zip
unzip -o JetBrainsMono.zip 'JetBrainsMonoNerdFontMono*' -d ~/.local/share/fonts
rm JetBrainsMono.zip
fc-cache -f ~/.local/share/fonts
```
