#!/usr/bin/env python3
"""静态托管：只放页面与快照，挡掉 .env / .git / 状态文件 / 日志。"""
import os
import re
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.environ.get("SERVE_ROOT", "/data/home/airadar")
PORT = int(os.environ.get("SERVE_PORT", "8091"))

DENY = re.compile(
    r"^/(\..*|seen\.json|archive\.jsonl|ntfy_topic\.txt|.*\.log|.*\.py|.*\.sh)$",
    re.I,
)


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def do_GET(self):
        path = self.path.split("?")[0]
        if DENY.match(path):
            self.send_error(404, "Not Found")
            return
        super().do_GET()

    def do_HEAD(self):
        path = self.path.split("?")[0]
        if DENY.match(path):
            self.send_error(404, "Not Found")
            return
        super().do_HEAD()

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'self'",
        )
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
