"""Private, TLS-only phone REST transport; authentication is local HMAC."""

from __future__ import annotations

import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .desktop_bridge import BridgeError, DesktopBridge, MAX_BYTES, canonical_json, private_bind
from .validator import PlanValidationError, _read

PAIR_PATH = "/v1/assistant/pair"
PROPOSALS_PATH = "/v1/assistant/proposals"
RECEIPT_PATH = "/v1/assistant/receipt"
CONTEXT_PATH = "/v1/assistant/context"
DISCONNECT_PATH = "/v1/assistant/disconnect"


class _PrivateTLSServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, bridge: DesktopBridge):
        if ":" in bridge.bind:
            self.address_family = socket.AF_INET6
        self.bridge = bridge
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.minimum_version = ssl.TLSVersion.TLSv1_2
        self.tls.load_cert_chain(bridge.certificate_path, bridge.key_path)
        self._workers = threading.BoundedSemaphore(16)
        super().__init__((bridge.bind, bridge.port), _PhoneHandler)
        bridge.port = self.server_address[1]

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(10)
        try:
            return self.tls.wrap_socket(connection, server_side=True, do_handshake_on_connect=False), address
        except BaseException:
            connection.close()
            raise

    def process_request(self, request, client_address):
        if not self._workers.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._workers.release()

    def handle_error(self, request, client_address):
        # Never log request bodies, credential headers, or exception representations.
        pass


class _PhoneHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        self.connection = self.request
        self.connection.do_handshake()
        super().setup()

    def log_message(self, format, *args):
        pass

    def _reply(self, status: int, value: Any):
        body = canonical_json(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _route(self):
        try:
            private_bind(self.client_address[0])
            if "?" in self.path or "#" in self.path:
                raise BridgeError("query parameters are not supported", 404)
            routes = {("POST", PAIR_PATH), ("GET", PROPOSALS_PATH), ("POST", RECEIPT_PATH), ("POST", CONTEXT_PATH), ("POST", DISCONNECT_PATH)}
            if (self.command, self.path) not in routes:
                raise BridgeError("unsupported phone route", 404)
            if self.headers.get("Transfer-Encoding") is not None:
                raise BridgeError("chunked/transfer-encoded requests are not supported")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) > 1:
                raise BridgeError("duplicate Content-Length is not supported")
            if self.command == "POST" and len(lengths) != 1:
                raise BridgeError("Content-Length is required", 411)
            try:
                length = int(lengths[0]) if lengths else 0
            except ValueError as error:
                raise BridgeError("invalid Content-Length") from error
            limit = MAX_BYTES if self.path == CONTEXT_PATH else 8192
            if length < 0 or length > limit or (self.command == "GET" and length != 0):
                raise BridgeError("phone request body exceeds its permitted size", 413)
            if self.command == "POST" and self.headers.get_content_type() != "application/json":
                raise BridgeError("Content-Type must be application/json", 415)
            body = self.rfile.read(length)
            if len(body) != length:
                raise BridgeError("incomplete request body")
            bridge = self.server.bridge
            if self.path == PAIR_PATH:
                result = bridge.pair(_read(body))
            else:
                for field in ("X-Habits-Device", "X-Habits-Timestamp", "X-Habits-Nonce", "X-Habits-Signature"):
                    if len(self.headers.get_all(field, [])) != 1:
                        raise BridgeError("request authentication failed", 401)
                device = bridge.authenticate(self.command, self.path, body, self.headers)
                if self.path == PROPOSALS_PATH:
                    result = bridge.pending_proposals(device)
                elif self.path == RECEIPT_PATH:
                    result = bridge.acknowledge(device, _read(body))
                elif self.path == DISCONNECT_PATH:
                    result = bridge.disconnect(device, _read(body))
                else:
                    result = bridge.share_context(device, _read(body))
            self._reply(200, result)
        except BridgeError as error:
            self._reply(error.status, {"error": str(error)})
        except PlanValidationError as error:
            self._reply(400, {"error": str(error)})
        except (OSError, ValueError, TypeError):
            self._reply(400, {"error": "invalid phone request; no application action was performed"})

    do_GET = _route
    do_POST = _route


class PhoneBridgeServer:
    def __init__(self, bridge: DesktopBridge):
        self.server = _PrivateTLSServer(bridge)
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is not None:
            raise BridgeError("phone bridge server is already running")
        self.thread = threading.Thread(target=self.server.serve_forever, name="habits-phone-bridge", daemon=True)
        self.thread.start()

    def close(self) -> None:
        if self.thread is not None:
            self.server.shutdown()
            self.thread.join(timeout=5)
        self.server.server_close()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
