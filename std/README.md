# std/

The standard library for Lithon programs, in Lithon.

## `http/` — an HTTP client

A real package, imported the way Lithon imports anything:

```python
import std.http

resp: str[8192] = std.http.http_get("http://127.0.0.1:8080/api/items")
print(std.http.http_status(resp))      # 200
print(std.http.http_body(resp))        # {"items": [...]}
```

One call, one raw response: the status line, the headers and the body, exactly
as the server sent them. The helpers in the package read the parts a caller
usually wants -- `http_status`, `http_ok`, `http_body`, `http_headers`,
`http_header`, `http_status_line` -- and `std.http.url.url_join(base, path)`
joins a base URL and a path, since Lithon cannot add two strings together.

The five verbs have names (`http_get`, `http_post`, `http_put`, `http_patch`,
`http_delete`); anything else goes over the wire verbatim through
`http_request(method, url, body)`.

Modules (concatenate in order, or import them):

| File | Role |
|---|---|
| `http.lio` | The front door: the verbs, the helpers, the status line |
| `http/core.lio` | The `Chunk` buffer and the byte-order helpers |
| `http/url.lio` | URL parsing and joining |
| `http/request.lio` | Request assembly |
| `http/io.lio` | The socket: connect, send, receive |
| `http/inflate.lio` | Content-Encoding: gzip, x-gzip and deflate (decoded after the read) |
| `http/response.lio` | Reading a response |

A request that never made it -- a bad URL, a refused connection, a timeout --
comes back as a diagnostic that starts with `LITHON-HTTP-ERROR[n]: `, where `n`
is the error code; `http_status` reads 0 for it and `http_error` returns the text.
Codes: 1 bad URL, 2 DNS, 3 socket/connect, 4 connect timeout, 5 read timeout,
6 recv failure or connection closed early, 7 send failure, 8 redirect limit or
unusable Location, 9 HTTPS failure (openssl missing, certificate or host check
failed, or no reply), 10 response or decoded body larger than the 8 KiB buffer,
11 malformed chunked body, 12 malformed or unsupported Content-Encoding (including
gzip with chunked framing).

Limits, all deliberate:

- `https://` goes through `/usr/bin/openssl s_client` with certificate and host
  checks on (`http/tls.lio`). Host names only; no IPv6; connections are not reused.
- Keep-alive is explicit: `http_open`, `http_request_on`, `http_keep_alive`,
  `http_close`. Plain `http://` only.
- DNS goes to 8.8.8.8:53 over TCP; there is no system resolver.
- Linux/x86-64 syscall numbers; 8192-byte response cap (raw and decoded).
- Compressed bodies (gzip, x-gzip, deflate) are decoded; the caller adds
  `Accept-Encoding` itself. Gzip with chunked framing is refused (error 12).
- A decoded body containing NUL bytes is cut at the first NUL (str length).
- Bodies are opaque text -- JSON, HTML and plain text all travel the same way;
  only how you read the output distinguishes them.

`tools/check_net_http.py` proves all five verbs and the response helpers
against a real local server in all three tiers (interpreter/native/auto);
`tests/http/*.lio` is the same programs as readable source.
