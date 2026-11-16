"""CI-only real nginx/rsyslog, loopback fake backend and disposable certificate."""
from __future__ import annotations

import contextlib
import http.client
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "infra" / "nginx"
SECRET = "SYNTHETIC_OAUTH_SECRET_MUST_NOT_PERSIST"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("isolated audit process did not become ready")


@contextlib.contextmanager
def process(args, log):
    with log.open("wb") as output:
        child = subprocess.Popen(args, stdout=output, stderr=output)
        try:
            yield child
        finally:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


class FakeBackend(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(303)
        self.send_header("Location", "/callback?code=" + SECRET)
        self.end_headers()
        self.wfile.write(SECRET.encode())

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true,"db":true}')

    def log_message(self, *_args):
        pass


def test_sim_tls_routes_and_audit_never_persist_oauth_payload():
    nginx, rsyslog = shutil.which("nginx"), shutil.which("rsyslogd")
    assert nginx and rsyslog, "CI must install nginx-core and rsyslog"
    # Short path: AF_UNIX socket names have a strict length limit.
    with tempfile.TemporaryDirectory(prefix="sim-audit-") as directory:
        root = Path(directory)
        cert, key = root / "cert.pem", root / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=sim.example.test", "-addext",
                        "subjectAltName=DNS:sim.example.test,DNS:lbot.example.test",
                        "-keyout", str(key), "-out", str(cert)], check=True, capture_output=True)
        audit = (SOURCE / "rsyslog-sim.conf").read_text().replace(
            "/run/sim-nginx-audit/socket", str(root / "audit.sock")).replace(
            "/var/log/sim-nginx/error-audit.log", str(root / "errors.log"))
        audit_path = root / "rsyslog.conf"
        audit_path.write_text(audit)
        subprocess.run([rsyslog, "-N1", "-f", str(audit_path)], check=True, capture_output=True)
        backend = ThreadingHTTPServer(("127.0.0.1", 0), FakeBackend)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        port, unavailable = free_port(), free_port()
        security = (SOURCE / "sim-server-security.conf").read_text().replace(
            "listen 8443 ssl http2;", f"listen 127.0.0.1:{port} ssl;").replace(
            "/etc/ssl/lbot/lbot.crt", str(cert)).replace("/etc/ssl/lbot/lbot.key", str(key)).replace(
            "/var/log/sim-nginx/access-audit.log", str(root / "access.log")).replace(
            "/run/sim-nginx-audit/socket", str(root / "audit.sock"))
        config = root / "nginx.conf"
        config.write_text(f"""pid {root / 'nginx.pid'};
error_log {root / 'global-errors.log'} warn;
events {{ worker_connections 64; }}
http {{
    {(SOURCE / 'sim-audit.conf').read_text()}
    server {{
        listen 127.0.0.1:{port} ssl;
        server_name lbot.example.test;
        ssl_certificate {cert}; ssl_certificate_key {key};
        access_log {root / 'neighbor.log'};
        location / {{ return 200 'neighbor'; }}
    }}
    server {{
        server_name sim.example.test;
        {security}
        location / {{ proxy_pass http://127.0.0.1:{backend.server_port}; }}
        location /oauth/unavailable {{ proxy_pass http://127.0.0.1:{unavailable}; }}
    }}
}}
""")
        context = ssl.create_default_context(cafile=str(cert))

        def request(host, method, path, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            connection.sock = context.wrap_socket(socket.create_connection(("127.0.0.1", port), 5),
                                                 server_hostname=host)
            try:
                headers = {"Host": host}
                if host == "sim.example.test":
                    headers.update({"Authorization": "Bearer " + SECRET, "Cookie": "session=" + SECRET,
                                    "Referer": "https://sim.example.test/?state=" + SECRET})
                connection.request(method, path, body, headers)
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()

        try:
            with process([rsyslog, "-n", "-i", str(root / "audit.pid"), "-f", str(audit_path)],
                         root / "audit-process.log"):
                wait_for(lambda: (root / "audit.sock").exists())
                subprocess.run([nginx, "-t", "-p", str(root), "-c", str(config)],
                               check=True, capture_output=True)
                with process([nginx, "-p", str(root), "-c", str(config), "-g", "daemon off;"],
                             root / "nginx-process.log"):
                    wait_for(lambda: (root / "nginx.pid").exists())
                    assert request("lbot.example.test", "GET", "/")[1] == b"neighbor"
                    assert request("sim.example.test", "GET", "/api/ready?code=" + SECRET)[0] == 200
                    assert request("sim.example.test", "POST", "/oauth/token?state=" + SECRET,
                                   "code=" + SECRET)[0] == 303
                    assert request("sim.example.test", "GET", "/oauth/unavailable?code=" + SECRET)[0] == 502
                    assert request("lbot.example.test", "GET", "/")[1] == b"neighbor"
                    with pytest.raises(ssl.SSLCertVerificationError):
                        request("wrong.example.test", "GET", "/")
                    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                        sock.sendto(("<131>Nov 16 12:00:00 host sim_nginx: " + SECRET).encode(),
                                    str(root / "audit.sock"))
                    wait_for(lambda: (root / "errors.log").exists()
                             and len((root / "errors.log").read_text().splitlines()) >= 2)
        finally:
            backend.shutdown()
            backend.server_close()
        rows = [json.loads(line) for line in (root / "access.log").read_text().splitlines()]
        assert [(r["method"], r["path"], r["status"]) for r in rows] == [
            ("GET", "/api/ready", 200), ("POST", "/oauth/token", 303), ("GET", "/oauth/unavailable", 502)]
        assert all(set(r) == {"at", "request_id", "method", "path", "status", "duration"} for r in rows)
        errors = [json.loads(line) for line in (root / "errors.log").read_text().splitlines()]
        assert all(set(r) == {"at", "severity", "event", "details"} for r in errors)
        for logfile in root.glob("*.log"):
            assert SECRET not in logfile.read_text(), logfile.name
