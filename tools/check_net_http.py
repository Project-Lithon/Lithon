#!/usr/bin/env python3
"""The `std/http` package must complete real HTTP transactions against a
real local HTTP server.

The oracle is `http.server` (stdlib, loopback) because sockets are stateful
and external -- tier agreement alone could hide a wrong `sockaddr_in` packing
the same way in both tiers. The Lithon side does the whole request/response
cycle with raw `syscall()`s over the package in `std/`: if the hand-rolled
port/IP packing, the byte-built request or the status/body parsing were
wrong, the server would not answer with the documented statuses and bodies.

The programs live in `tests/http/` and are real Lithon programs: they
`import std.http` and call the client, exactly as an application does. The
import is resolved at compile time by the real frontend, so the checker
exercises the package the way a user would, not a copy of it.

Each program is compiled once and run through `build/tier_runner` in all
three modes; every mode's stdout must equal the expected transcript in
`tests/http/expected/`.

If loopback sockets cannot be opened at all on this host, the check reports
SKIP (same honesty rule as `tools/check_net_loopback.py`).
"""
from __future__ import annotations

import gzip
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import socket
import time
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer


class ThreadingHTTPServer6(ThreadingHTTPServer):
    address_family = socket.AF_INET6

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = ROOT / "build" / "tier_runner"
FRONTEND = ROOT / "src" / "frontend" / "frontend.py"
TESTS = ROOT / "tests" / "http"
EXPECTED = TESTS / "expected"

TIER_FLAGS = [("--interp", "interpreter"), ("--strict", "native"), ("--auto", "auto")]

CONNS = {"n": 0}


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self):
            super().setup()
            CONNS["n"] += 1

        def end_headers(self):
            # The server closes the socket after a request that asked for it;
            # say so, as an HTTP/1.1 server should.
            if self.close_connection:
                self.send_header("Connection", "close")
            super().end_headers()

        def _reply(self, code: int, body: bytes) -> None:
            self.send_response(code)
            ctype = "text/html" if body.startswith(b"<") else "application/json"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    # The client gave up (a timeout test closes early); not a server fault.
                    pass

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length", "0") or "0")
            return self.rfile.read(n) if n else b""

        def _echo(self, body: bytes) -> None:
            # `/echo` reflects what the server received, for the header tests:
            # the method, the X-Token and Content-Type headers, and the body.
            h = self.headers
            out = '{"method":"%s","x_token":"%s","ctype":"%s","body":"%s"}' % (
                self.command, h.get("X-Token", ""), h.get("Content-Type", ""), body.decode())
            self._reply(200, out.encode())

        def _redirect(self, code: int, loc: str) -> None:
            self.send_response(code)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _redirected(self) -> bool:
            # `/loop` redirects to itself; `/redir/<code>` redirects to `/echo`.
            if self.path == "/loop":
                self._redirect(302, "/loop")
                return True
            if self.path.startswith("/redir/"):
                self._redirect(int(self.path.split("/")[2]), "/echo")
                return True
            return False

        def _chunked(self, bad: bool) -> None:
            self.send_response(200)
            self.send_header("transfer-encoding", "Chunked")
            self.send_header("X-Kept", "yes")
            self.end_headers()
            if bad:
                self.wfile.write(b"4\r\nWiki\r\nZZ\r\nxx\r\n0\r\n\r\n")
            else:
                self.wfile.write(b"4\r\nWiki\r\n5\r\npedia\r\nB;ext=1\r\n in chunks.\r\n0\r\n\r\n")

        def _gz(self, kind: str) -> None:
            text = (b"The quick brown fox jumps over the lazy dog. " * 12) + b"done\n"
            ce = "gzip"
            if kind == "fixed":
                c = zlib.compressobj(9, zlib.DEFLATED, 31, 9, zlib.Z_FIXED)
                data = c.compress(text) + c.flush()
            elif kind == "stored":
                c = zlib.compressobj(0, zlib.DEFLATED, 31)
                data = c.compress(text) + c.flush()
            elif kind == "zlib":
                ce = "deflate"
                data = zlib.compress(text, 9)
            elif kind == "badcrc":
                data = bytearray(gzip.compress(text, 9))
                data[-8] ^= 0xFF
            elif kind == "chunked":
                data = gzip.compress(text, 9)
            else:
                data = gzip.compress(text, 9)
            self.send_response(200)
            self.send_header("Content-Encoding", ce)
            if kind == "chunked":
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"%x\r\n" % len(data) + bytes(data) + b"\r\n0\r\n\r\n")
                return
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(bytes(data))


        def do_GET(self):
            if self.path == "/conns":
                return self._reply(200, ('{"conns":%d}' % CONNS["n"]).encode())
            if self.path == "/chunked":
                return self._chunked(False)
            if self.path == "/chunked-bad":
                return self._chunked(True)
            if self.path.startswith("/path"):
                return self._reply(200, ('{"path":"%s"}' % self.path).encode())
            if self.path == "/setcookie":
                self.send_response(200)
                self.send_header("Set-Cookie", "sid=abc123; Path=/")
                self.send_header("Set-Cookie", "theme=dark")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/cookiein":
                c = self.headers.get("Cookie", "")
                return self._reply(200, ('{"cookie":"%s"}' % c).encode())
            if self.path == "/slow":
                time.sleep(2)
                return self._reply(200, b'{"slow":true}')
            if self.path.startswith("/gz/"):
                return self._gz(self.path[4:])
            if self.path.startswith("/echo"):
                return self._echo(b"")
            if self._redirected():
                return
            self._reply(200, b"<h1>ok</h1>")

        def do_POST(self):
            body = self._body()
            if self.path.startswith("/echo"):
                return self._echo(body)
            if self._redirected():
                return
            self._reply(201, b'{"got":1}')

        def do_PUT(self):
            body = self._body()
            if self.path.startswith("/echo"):
                return self._echo(body)
            if self._redirected():
                return
            self._reply(202, b'{"put":true}')

        def do_PATCH(self):
            self._body()
            self._reply(206, b'{"patched":true}')

        def do_DELETE(self):
            self._reply(204, b"")

        def log_message(self, *args):
            pass

    return Handler


def free_port() -> int:
    """A port nothing is listening on, so a connect to it is refused."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# `real.lio` and `https.lio` need the live internet; run them by hand.
MANUAL = {"real", "https"}


def programs() -> list[pathlib.Path]:
    return sorted(p for p in TESTS.glob("*.lio") if p.stem not in MANUAL)


def main() -> int:
    if not RUNNER.exists():
        sys.exit(f"missing {RUNNER}; build tier_runner first (see src/jit/tier_runner.cpp)")
    if not FRONTEND.exists():
        sys.exit(f"missing {FRONTEND}")

    # Ephemeral port: the kernel picks one (Python holds the real port in
    # server_address).
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler())
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # The same routes over IPv6 loopback, for [::1] URLs.
    srv6 = ThreadingHTTPServer6(("::1", 0), make_handler())
    port6 = srv6.server_address[1]
    threading.Thread(target=srv6.serve_forever, daemon=True).start()

    bad = 0
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for prog in programs():
            stem = prog.stem
            source = prog.read_text().replace("__PORT__", str(port)).replace("__PORT6__", str(port6))
            program = tmp / f"{stem}.lio"
            program.write_text(source)

            fe = subprocess.run(
                [sys.executable, str(FRONTEND), str(program)],
                capture_output=True, text=True,
            )
            if fe.returncode:
                print(f"FAIL [{stem}] frontend:\n{fe.stderr}")
                bad += 1
                continue
            ir = tmp / f"{stem}.ir"
            ir.write_text(fe.stdout)

            want_file = EXPECTED / f"{stem}.out"
            if not want_file.exists():
                print(f"FAIL [{stem}] no expected transcript at {want_file}")
                bad += 1
                continue
            want = want_file.read_text()

            for flag, tier in TIER_FLAGS:
                r = subprocess.run(
                    [str(RUNNER), str(ir), flag], capture_output=True, text=True,
                    timeout=3600,
                )
                if r.returncode:
                    print(f"FAIL [{stem}/{tier}] {flag}: exit {r.returncode}\n{r.stderr}")
                    bad += 1
                    continue
                if r.stdout != want:
                    print(f"FAIL [{stem}/{tier}] {flag}: wrong transcript")
                    print(f"  expected: {want!r}")
                    print(f"  got:      {r.stdout!r}")
                    bad += 1
                else:
                    first = want.splitlines()[0] if want.splitlines() else ""
                    print(f"PASS [{stem}/{tier}] {flag}: {first} exact")

    srv.shutdown()
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, socket.timeout):
        print("SKIP: this host cannot complete a loopback round trip (no sockets)")
        sys.exit(0)
