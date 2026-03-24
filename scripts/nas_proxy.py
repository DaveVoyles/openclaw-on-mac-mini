#!/usr/bin/env python3
"""
NAS Proxy — OpenClaw
Runs on the Mac Mini HOST (outside Docker) and forwards HTTP requests
to the Synology NAS DSM HTTPS API.

This is needed because Docker Desktop on macOS runs containers in a Linux VM
that cannot route to other LAN hosts (e.g. 192.168.1.8). The Mac Mini can
reach the NAS directly, so this proxy bridges the gap:

  Docker container → http://192.168.1.93:19500/webapi/... → https://192.168.1.8:5001/webapi/...

Usage:
  python3 scripts/nas_proxy.py            # default: listen on 0.0.0.0:19500
  NAS_PROXY_PORT=19501 python3 ...        # custom port
  NAS_TARGET=https://192.168.1.8:5001 python3 ...

To run at startup, install the provided LaunchAgent:
  cp scripts/com.openclaw.nasproxy.plist ~/Library/LaunchAgents/
  launchctl load ~/Library/LaunchAgents/com.openclaw.nasproxy.plist
"""

import http.server
import logging
import os
import subprocess
import sys
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] nas_proxy: %(message)s",
)
log = logging.getLogger("nas_proxy")

PROXY_HOST = os.getenv("NAS_PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("NAS_PROXY_PORT", "19501"))
NAS_TARGET = os.getenv("NAS_TARGET", "https://192.168.1.8:5001")


class NASProxyHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info(fmt, *args)

    def _proxy(self):
        target_url = NAS_TARGET.rstrip("/") + self.path

        # Forward the request body if present
        body = None
        if self.command in ("POST", "PUT", "PATCH"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else None

        # Build curl command — curl is already allowed LAN access by macOS
        cmd = [
            "curl",
            "--silent",
            "--show-error",
            "--insecure",          # allow self-signed NAS cert
            "--max-time", "15",
            "--write-out", "\n###STATUS:%{http_code}###",
            "-X", self.command,
            target_url,
        ]
        # Forward most headers (skip hop-by-hop)
        skip = {"host", "connection", "keep-alive", "transfer-encoding",
                "te", "trailers", "proxy-authorization", "proxy-authenticate",
                "accept-encoding"}  # strip so NAS returns plain JSON, not gzip
        for k, v in self.headers.items():
            if k.lower() not in skip:
                cmd += ["-H", f"{k}: {v}"]
        if body:
            cmd += ["--data-binary", "@-"]

        try:
            result = subprocess.run(
                cmd,
                input=body,
                capture_output=True,
                timeout=20,
            )
            raw = result.stdout

            # Parse the custom write-out footer to extract status code
            status = 200
            marker = b"\n###STATUS:"
            if marker in raw:
                idx = raw.rfind(marker)
                status_part = raw[idx + len(marker):]
                end = status_part.find(b"###")
                if end != -1:
                    try:
                        status = int(status_part[:end])
                    except ValueError:
                        pass
                raw = raw[:idx]

            if result.returncode != 0:
                err = result.stderr.decode(errors="replace").strip()
                log.error("curl error for %s: %s", target_url, err)
                self.send_error(502, f"curl error: {err}")
                return

            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        except subprocess.TimeoutExpired:
            self.send_error(504, "NAS request timed out")
        except Exception as e:
            log.error("Proxy error for %s: %s", target_url, e)
            self.send_error(502, f"NAS proxy error: {e}")

    do_GET = _proxy
    do_POST = _proxy
    do_PUT = _proxy
    do_DELETE = _proxy


def main():
    parsed = urlparse(NAS_TARGET)
    log.info("NAS Proxy starting — listening on %s:%d → %s", PROXY_HOST, PROXY_PORT, NAS_TARGET)
    log.info("Set NAS_URL=http://%s:%d in .env (or docker-compose environment)", "192.168.1.93", PROXY_PORT)

    server = http.server.HTTPServer((PROXY_HOST, PROXY_PORT), NASProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("NAS Proxy stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
