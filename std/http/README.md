# std/http — an HTTP/1.1 client

Imported the ordinary way; resolved at compile time.

```python
import std.http

resp: str[8192] = std.http.http_get("http://127.0.0.1:8080/api/items")
print(std.http.http_status(resp))      # 200
print(std.http.http_body(resp))
```

One call, one raw response: status line, headers and body as the server sent
them. Helpers read the parts a caller usually wants:

- `http_status(resp)` — status code, or 0 when the request never completed
- `http_ok(resp)` — 2xx/3xx
- `http_body(resp)` — body text (after chunked and gzip/deflate decoding)
- `http_headers(resp)` — header block
- `http_header(resp, name)` — one header, case-insensitive
- `http_status_line(resp)` — e.g. `"200 OK"`
- `http_error(resp)` / `http_error_code(resp)` — diagnostic text / its code, or empty / 0

Verbs: `http_get`, `http_post`, `http_put`, `http_patch`, `http_delete`.
Anything else, or custom headers and timeouts: `http_send(method, url, headers, body, timeout_ms)`.
`http_request` is the generic form.

## Behavior

- **Redirects** are followed (default limit in `net.lio`). 303 becomes GET with
  no body; 301/302 become GET only for POST; 307/308 keep method and body.
  A limit of 0 returns the 3xx unfollowed.
- **Timeouts**: `timeout_ms` (default 30000) applies to connect, send and each recv.
- **Chunked** bodies are decoded (`http/chunked.lio`).
- **Compressed** bodies: `Content-Encoding: gzip`, `x-gzip` and `deflate` are
  decoded (`http/inflate.lio`), with CRC-32/ISIZE or Adler-32 verified. Nothing
  is sent by default: add `Accept-Encoding: gzip` in the `headers` argument
  yourself. Gzip with chunked framing is refused with error 12 (not yet supported).
- **Cookies**: `Set-Cookie` values are kept and sent back (`http/cookie.lio`).
- **DNS**: names resolve through 8.8.8.8:53 over TCP (`http/dns.lio`). IPv4 and
  IPv6 literals work (`[::1]` form, `http/ip6.lio`).
- **Connections** are one per call by default (`Connection: close`). To reuse a
  socket, use the explicit handle API (see *Keep-alive* below).
- **HTTPS**: `https://` URLs go through `openssl s_client` (`http/tls.lio`). Lithon
  has no TLS library, so this needs `/usr/bin/openssl`. Certificates and the host
  name are verified (`-verify_return_error`, `-verify_hostname`). Failure is error 9.
  Host names only: letters, digits, `.`, `-`. IPv6 and userinfo are refused (error 1).
  Connections are never reused for HTTPS.

## Errors

A failed request returns `LITHON-HTTP-ERROR[n]: <text>`. Codes are listed in
`std/README.md`. Code 12 covers malformed or unsupported gzip/deflate bodies.

## Module map

| File | Role |
|---|---|
| `http.lio` | Public API: verbs, helpers, status line |
| `http/net.lio` | `http_exchange`, redirect following |
| `http/io.lio` | Socket connect, send, recv, timeouts |
| `http/dns.lio` | DNS query over TCP |
| `http/url.lio`, `http/ip6.lio` | URL parsing, IPv6 literals |
| `http/request.lio` | Request assembly |
| `http/response.lio` | Response parsing |
| `http/chunked.lio` | Chunked transfer decoding, header removal |
| `http/inflate.lio` | gzip/deflate decoding |
| `http/tls.lio` | HTTPS through `openssl s_client` (error 9) |
| `http/encode.lio`, `http/query.lio` | Percent-encoding, query strings |
| `http/cookie.lio` | Cookie jar for one call chain |
| `http/core.lio` | `Chunk` buffer, byte-order helpers |

## Limits

- Linux/x86-64 raw syscalls only (no libc).
- Response cap is 8192 bytes, raw and decoded. Larger responses fail with error 10.
- A body containing NUL bytes is cut at the first NUL (`str` length semantics).
- Whole response is buffered; no streaming.
- Keep-alive is opt-in and only for plain `http://`. A reply is reused only when it
  has Content-Length or chunked framing and no `Connection: close`.
- HTTPS reads each reply to EOF and never reuses the connection.
- **Compile cost**: each call that is inlined pulls in the full exchange path. In
  native builds a single call site lowers to roughly 40k IR lines, so programs with
  many call sites compile slowly.

## Keep-alive

```python
sock: int[64] = std.http.http_open("http://127.0.0.1:8080/")
r1: str[8192] = std.http.http_request_on(sock, "GET", "http://127.0.0.1:8080/a", "", "")
keep: bool = std.http.http_keep_alive(r1)   # True: the socket can carry another request
std.http.http_close(sock)
```

`http_open(url)` returns a socket, or a negative value (-1..-4) on failure, which
`http_open_error(code)` names. `http_request_on` does not follow redirects. Use
`http_keep_alive(resp)` before reusing; if it is False, open a new socket.

## Tests

`tools/check_net_http.py` runs every `tests/http/*.lio` against a local server
(IPv4 and IPv6) in all three tiers (`--interp`, `--strict`, `--auto`) and compares
the transcript to `tests/http/expected/<name>.out`. `tests/http/real.lio` and
`tests/http/https.lio` need the live internet and are run by hand; neither is in
the harness.
