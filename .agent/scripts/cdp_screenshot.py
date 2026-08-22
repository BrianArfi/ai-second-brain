#!/usr/bin/env python3
"""Screenshot a local page by driving the already-running Chrome over CDP.

Launching a fresh headless Chrome hangs on this box (the same signature as the
meetbot navigation timeout), but the browser systemd already runs on :9222
answers fine. This attaches to it, which is what its remote-debugging port is
for, and never kills or restarts it.

No websocket library is installed, so the client below is the minimum of RFC6455
that CDP needs: a masked text frame out, an unmasked frame in.
"""
import base64, json, os, socket, struct, sys, threading, time, urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

CDP = "http://127.0.0.1:9222"
DIR = os.path.dirname(os.path.abspath(__file__))
PAGE = sys.argv[1] if len(sys.argv) > 1 else "harness.html"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DIR, "shot.png")
W, H, SCALE = 780, 1400, 2

class WS:
    def __init__(self, url):
        _, rest = url.split("://", 1)
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.path = "/" + path
        self.s = socket.create_connection((host, int(port)), timeout=25)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {self.path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.s.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError("ws handshake failed: " + buf.split(b"\r\n")[0].decode())
        self.buf = buf.split(b"\r\n\r\n", 1)[1]
        self.n = 0

    def _recv(self, k):
        while len(self.buf) < k:
            chunk = self.s.recv(65536)
            if not chunk:
                raise RuntimeError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:k], self.buf[k:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode()
        # Clients MUST mask. FIN + opcode 1 (text).
        head = bytes([0x81])
        n = len(data)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        m = os.urandom(4)
        self.s.sendall(head + m + bytes(b ^ m[i % 4] for i, b in enumerate(data)))

    def recv(self):
        while True:
            b0, b1 = self._recv(2)
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv(8))[0]
            payload = self._recv(ln)
            if (b0 & 0x0F) == 1:
                return json.loads(payload)
            # ignore ping/pong/binary

    def call(self, method, params=None, timeout=30):
        self.n += 1
        mid = self.n
        self.send({"id": mid, "method": method, "params": params or {}})
        end = time.time() + timeout
        while time.time() < end:
            msg = self.recv()
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
        raise TimeoutError(method)

# Serve the folder: a file:// URL is not reliably readable by a browser that was
# started elsewhere, and an http origin is what the real app runs under anyway.
httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=DIR))
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{port}/{PAGE}"

# Chrome 111+ requires PUT here; a GET answers 405.
req = urllib.request.Request(f"{CDP}/json/new?{url}", method="PUT")
target = json.load(urllib.request.urlopen(req, timeout=20))
tid = target["id"]
try:
    ws = WS(target["webSocketDebuggerUrl"])
    ws.call("Page.enable")
    ws.call(
        "Emulation.setDeviceMetricsOverride",
        {"width": W, "height": H, "deviceScaleFactor": SCALE, "mobile": False},
    )
    ws.call("Page.navigate", {"url": url})
    # Poll readyState rather than sleeping blind: fonts and the stylesheet decide
    # the layout being measured here.
    for _ in range(60):
        r = ws.call("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True})
        if r.get("result", {}).get("value") == "complete":
            break
        time.sleep(0.25)
    time.sleep(0.6)
    full = ws.call(
        "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True}, timeout=45
    )
    open(OUT, "wb").write(base64.b64decode(full["data"]))
    print("wrote", OUT, os.path.getsize(OUT), "bytes")
finally:
    try:
        urllib.request.urlopen(f"{CDP}/json/close/{tid}", timeout=10).read()
    except Exception:
        pass
    httpd.shutdown()
