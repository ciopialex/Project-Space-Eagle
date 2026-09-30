"""Clean, authenticated FastAPI and WebSocket server for Space-Eagle Dashboard.

Decoupled architecture:
  - OS firewall manipulation handled by dashboard.netaccess.
  - Authenticated Encrypt-then-MAC session encryption handled by dashboard.crypto.
  - Rate-limited pairing credentials, bounded token lifecycles, and path-contained uploads.
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import hashlib
import json
import os
import secrets
import socket
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, File, Header, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
import uvicorn

from core import user_paths
from dashboard import crypto
from dashboard import netaccess

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
#: The user's data, never the app checkout: an update resets the checkout, and
#: what a phone sends is the user's, like everything else in this directory.
UPLOADS_DIR = user_paths.user_data_dir() / "uploads"
CERT_DIR = user_paths.user_data_dir() / "certs"
CERT_KEY = CERT_DIR / "dashboard.key"
CERT_FILE = CERT_DIR / "dashboard.crt"


def ensure_certificate() -> bool:
    """A private certificate for this machine, made once. True when it exists.

    The dashboard served plain HTTP on every network interface. Its commands
    are encrypted with a per-pairing secret -- and that secret reached the
    phone in the login response, in the clear, so anyone on the same network
    who saw the pairing could read everything after it. Self-signed, so the
    phone warns once; after that the pairing itself is protected.
    """
    if CERT_KEY.is_file() and CERT_FILE.is_file():
        return True
    try:
        import datetime
        import ipaddress
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Aethelark")])
        sans = [x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
        try:
            sans.append(x509.IPAddress(ipaddress.ip_address(_local_ip())))
        except ValueError:
            pass
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(name).issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(x509.SubjectAlternativeName(sans), critical=False)
                .sign(key, hashes.SHA256()))
        user_paths.ensure_private_dir(CERT_DIR)
        user_paths.write_private(CERT_KEY, key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode())
        user_paths.write_private(CERT_FILE, cert.public_bytes(
            serialization.Encoding.PEM).decode())
        return True
    except Exception as e:
        print(f"[Dashboard] could not make a certificate ({e}); serving plain HTTP")
        return False

PORT: int = 8000
PORT_ALIAS: int = 8001
MAX_UPLOAD_MB: float = 50.0
TOKEN_TTL: float = 12 * 3600.0  # 12 hours
MAX_TOKENS: int = 64
LOGIN_WINDOW: float = 60.0
LOGIN_MAX_ATTEMPTS: int = 10
HISTORY: int = 300
KEY_ALPHABET: str = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _local_ip() -> str:
    """Discovers LAN IP without socket leaks or external DNS reliance."""
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return "127.0.0.1"


class _Sessions:
    """Manages active session tokens, one-time pairing keys, and device tokens."""

    def __init__(self, max_tokens: int = MAX_TOKENS, token_ttl: float = TOKEN_TTL) -> None:
        self.max_tokens = max_tokens
        self.token_ttl = token_ttl
        self._tokens: Dict[str, float] = {}              # token -> expiry
        self._token_secrets: Dict[str, str] = {}        # token -> root secret
        self._pending_keys: Dict[str, Tuple[float, str]] = {}  # pin -> (expiry, root secret)
        self._device_secrets: Dict[str, str] = {}       # device_token -> root secret

    def sweep(self) -> None:
        now = time.monotonic()
        self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
        self._token_secrets = {t: s for t, s in self._token_secrets.items() if t in self._tokens}
        self._pending_keys = {k: v for k, v in self._pending_keys.items() if v[0] > now}

    def cap(self) -> None:
        if len(self._tokens) > self.max_tokens:
            # Sort by expiry and keep only the latest max_tokens
            sorted_tokens = sorted(self._tokens.items(), key=lambda item: item[1], reverse=True)[:self.max_tokens]
            self._tokens = dict(sorted_tokens)
            self._token_secrets = {t: s for t, s in self._token_secrets.items() if t in self._tokens}

    def new_key(self, expiry_secs: float = 300.0) -> Tuple[str, str]:
        self.sweep()
        pin = "".join(secrets.choice(KEY_ALPHABET) for _ in range(6))
        secret = crypto.new_secret()
        self._pending_keys[pin.upper()] = (time.monotonic() + expiry_secs, secret)
        return pin, secret

    def redeem_key(self, pin: str) -> Optional[str]:
        self.sweep()
        pin_clean = str(pin).strip().upper()
        if pin_clean in self._pending_keys:
            exp, secret = self._pending_keys.pop(pin_clean)
            if exp > time.monotonic():
                return secret
        return None

    def issue_token(self, secret: str, ttl: Optional[float] = None) -> str:
        self.sweep()
        effective_ttl = self.token_ttl if ttl is None else ttl
        tok = secrets.token_urlsafe(32)
        self._tokens[tok] = time.monotonic() + effective_ttl
        self._token_secrets[tok] = secret
        self.cap()
        return tok

    def validate_token(self, tok: str) -> bool:
        self.sweep()
        if tok in self._tokens:
            if self._tokens[tok] > time.monotonic():
                return True
            self._tokens.pop(tok, None)
            self._token_secrets.pop(tok, None)
        return False

    def secret_for(self, tok: str) -> Optional[str]:
        if self.validate_token(tok):
            return self._token_secrets.get(tok)
        return None

    def register_device(self, device_tok: str, secret: str) -> None:
        self._device_secrets[device_tok] = secret

    def redeem_device(self, device_tok: str) -> Optional[str]:
        return self._device_secrets.get(device_tok)

    def revoke_devices(self) -> int:
        n = len(self._device_secrets)
        self._device_secrets.clear()
        return n


class _RateLimiter:
    """Fixed-window rate limiter per client IP."""

    def __init__(self, window: float = LOGIN_WINDOW, max_attempts: int = LOGIN_MAX_ATTEMPTS) -> None:
        self.window = window
        self.max_attempts = max_attempts
        self._attempts: Dict[str, List[float]] = {}
        self._last_sweep = 0.0

    def _sweep(self, now: float) -> None:
        """Forget IPs whose attempts have all aged out.

        Every distinct peer used to leave a permanent entry: the lookup was on
        a defaultdict, so merely READING an unknown IP created one. This server
        binds 0.0.0.0 and opens the firewall for itself, so on any network with
        scanners on it the table grew for the life of the process and nothing
        ever removed a key.

        Gating the WebSocket handshakes made that worse rather than better —
        the limiter is now reached by unauthenticated peers, which is the point
        of it, so it has to be bounded.
        """
        self._last_sweep = now
        fresh = {}
        for ip, hits in self._attempts.items():
            kept = [t for t in hits if now - t < self.window]
            if kept:
                fresh[ip] = kept
        self._attempts = fresh

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        if now - self._last_sweep > self.window:
            self._sweep(now)
        history = [t for t in self._attempts.get(ip, ()) if now - t < self.window]
        if len(history) >= self.max_attempts:
            self._attempts[ip] = history
            return False
        history.append(now)
        self._attempts[ip] = history
        return True


class DashboardServer:
    """Space-Eagle Web and WebSocket Dashboard Server."""

    MAX_TOKENS = MAX_TOKENS
    TOKEN_TTL = TOKEN_TTL
    MAX_UPLOAD_MB = MAX_UPLOAD_MB

    def __init__(self, host: str = "0.0.0.0", port: int = PORT) -> None:
        self.host = host
        self.port = port
        self._ip = _local_ip()
        self._command_queue: asyncio.Queue[str] = asyncio.Queue()
        self._clients: Set[WebSocket] = set()
        self._history: collections.deque[Dict[str, Any]] = collections.deque(maxlen=HISTORY)
        self._running_tasks: Set[asyncio.Task] = set()

        self._connect_callback: Optional[Callable[[], None]] = None
        self._wake_callback: Optional[Callable[[], None]] = None
        self._upload_callback: Optional[Callable[[Path], None]] = None
        self._uploads_dir: Path = UPLOADS_DIR

        self._sessions = _Sessions(max_tokens=self.MAX_TOKENS, token_ttl=self.TOKEN_TTL)
        self._limiter = _RateLimiter()

        self.app = FastAPI(title="Aethelark Dashboard", docs_url=None, redoc_url=None)
        self._setup_middleware()
        self._setup_routes()

    @property
    def _tokens(self) -> Dict[str, float]:
        return self._sessions._tokens

    def set_connect_callback(self, cb: Callable[[], None]) -> None:
        self._connect_callback = cb

    def set_wake_callback(self, cb: Callable[[], None]) -> None:
        self._wake_callback = cb

    def set_upload_callback(self, cb: Callable[[Path], None]) -> None:
        """Told the path of each file a phone sends, once it is on disk."""
        self._upload_callback = cb

    def new_key(self, expiry_secs: float = 300.0) -> str:
        pin, _ = self._sessions.new_key(expiry_secs=expiry_secs)
        return pin

    def get_url(self) -> str:
        protocol = "https" if self._ssl_enabled() else "http"
        return f"{protocol}://{self._ip}:{self.port}"

    def get_manual_url(self) -> str:
        return f"{self._ip}:{self.port}"

    def _secret_for(self, tok: str) -> Optional[str]:
        return self._sessions.secret_for(tok)

    def _ssl_enabled(self) -> bool:
        return CERT_KEY.is_file() and CERT_FILE.is_file()

    def _spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)
        return task

    async def broadcast(self, message: Dict[str, Any]) -> None:
        self._history.append(message)
        if not self._clients:
            return

        dead_clients = set()

        async def send_one(ws: WebSocket) -> None:
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2.0)
            except Exception:
                dead_clients.add(ws)

        await asyncio.gather(*(send_one(ws) for ws in list(self._clients)), return_exceptions=True)
        for dead in dead_clients:
            self._clients.discard(dead)

    def _extract_token(self, auth_header: Optional[str] = None, query_token: Optional[str] = None) -> Optional[str]:
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        if query_token:
            return query_token.strip()
        return None

    def _require_auth(self, request: Request, auth_header: Optional[str] = None, query_token: Optional[str] = None) -> str:
        # Check instance-level monkeypatched TTL
        self._sessions.token_ttl = self.TOKEN_TTL
        self._sessions.max_tokens = self.MAX_TOKENS

        header = auth_header or request.headers.get("Authorization")
        q_tok = query_token or request.query_params.get("token")
        tok = self._extract_token(header, q_tok)
        if not tok or not self._sessions.validate_token(tok):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return tok

    def _authorise_ws(self, ws: WebSocket) -> bool:
        """The same gate every HTTP route has, for the two that had none.

        A browser cannot set headers on a WebSocket handshake, so the token
        travels in the query string. That is not a weakening: `/uploads/`
        already accepts `?token=` for the same reason, and the handshake is a
        real HTTP GET carrying a real session token either way.

        Rate limited on the same limiter as /login and keyed on the TCP peer,
        because an unauthenticated socket is otherwise a free oracle for
        guessing tokens as fast as the machine will answer.
        """
        self._sessions.token_ttl = self.TOKEN_TTL
        self._sessions.max_tokens = self.MAX_TOKENS

        ip = ws.client.host if ws.client else "127.0.0.1"
        if not self._limiter.allow(ip):
            return False
        tok = self._extract_token(None, ws.query_params.get("token"))
        return bool(tok) and self._sessions.validate_token(tok)

    def _setup_middleware(self) -> None:
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _setup_routes(self) -> None:
        app = self.app

        @app.get("/", response_class=HTMLResponse)
        async def root():
            app_html = STATIC_DIR / "app.html"
            if app_html.exists():
                return app_html.read_text(encoding="utf-8")
            return "<h1>Aethelark Dashboard</h1>"

        @app.get("/login", response_class=HTMLResponse)
        async def login_page():
            login_html = STATIC_DIR / "login.html"
            if login_html.exists():
                return login_html.read_text(encoding="utf-8")
            return "<h1>Aethelark Login</h1>"

        @app.post("/login")
        async def login(req: Request):
            client_ip = req.client.host if req.client else "127.0.0.1"
            if not self._limiter.allow(client_ip):
                raise HTTPException(status_code=429, detail="Too many login attempts. Please wait.")

            self._sessions.token_ttl = self.TOKEN_TTL
            self._sessions.max_tokens = self.MAX_TOKENS

            try:
                body = await req.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON")

            pin = body.get("pin") or body.get("key") or ""
            secret = self._sessions.redeem_key(str(pin))
            if not secret:
                raise HTTPException(status_code=401, detail="Invalid or expired PIN")

            token = self._sessions.issue_token(secret)
            if self._connect_callback:
                try:
                    self._connect_callback()
                except Exception:
                    pass

            return {"ok": True, "token": token}

        @app.get("/auto-login", response_class=HTMLResponse)
        async def auto_login(req: Request, key: str = Query("")):
            # Same limiter as /login, because this is the same secret.
            # /login guarded `redeem_key` and this did not, so one of the two
            # doors to a 31^6 pairing key answered as fast as the machine
            # could — and this is the door that pays better, since it issues a
            # device token and `redeem_device` has no expiry at all.
            #
            # Measured before fixing: 2031 guesses/s against a key that lives
            # 300 s, so ~609k of 887,503,681 per pairing window — about 0.07%,
            # not a practical break on its own. Closed because it costs one
            # line and because the module docstring claims "rate-limited
            # pairing credentials", which was true of only one route.
            client_ip = req.client.host if req.client else "127.0.0.1"
            if not self._limiter.allow(client_ip):
                raise HTTPException(status_code=429, detail="Too many attempts. Please wait.")
            secret = self._sessions.redeem_key(key)
            if not secret:
                return HTMLResponse("<html><body><h2>Link Expired</h2><p>This pairing link has expired.</p></body></html>", status_code=200)

            device_token = secrets.token_urlsafe(32)
            self._sessions.register_device(device_token, secret)

            html = f"""<!DOCTYPE html>
<html>
<head><title>Aethelark Pairing</title></head>
<body>
<h2>Pairing Device...</h2>
<script>
localStorage.setItem('aethelark_device_token','{device_token}');
window.location.href = '/';
</script>
</body>
</html>"""
            return HTMLResponse(html)

        @app.post("/api/device-login")
        async def device_login(req: Request):
            try:
                body = await req.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON")

            device_token = body.get("device_token", "")
            secret = self._sessions.redeem_device(device_token)
            if not secret:
                raise HTTPException(status_code=401, detail="Invalid device token")

            token = self._sessions.issue_token(secret)
            return {"ok": True, "token": token}

        @app.post("/api/revoke-devices")
        async def revoke_devices(req: Request):
            self._require_auth(req)
            count = self._sessions.revoke_devices()
            return {"ok": True, "revoked": count}

        @app.post("/api/command")
        async def command(req: Request):
            tok = self._require_auth(req)
            try:
                body = await req.json()
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid JSON")

            cmd_text = ""
            if "enc" in body:
                secret = self._sessions.secret_for(tok)
                if not secret:
                    raise HTTPException(status_code=401, detail="Session expired")
                keys = crypto.derive(secret)
                try:
                    cmd_text = crypto.decrypt(keys, body["enc"])
                except crypto.BadMessage as e:
                    raise HTTPException(status_code=400, detail=f"Bad message: {e}")
            elif "text" in body:
                cmd_text = str(body["text"])

            if cmd_text:
                await self._command_queue.put(cmd_text)
                return {"ok": True, "command": cmd_text}

            raise HTTPException(status_code=400, detail="No command provided")

        @app.post("/api/wake")
        async def wake(req: Request):
            self._require_auth(req)
            delivered = False
            if self._wake_callback:
                try:
                    self._wake_callback()
                    delivered = True
                except Exception:
                    pass
            return {"ok": True, "delivered": delivered}

        @app.post("/api/upload")
        async def upload_file(req: Request, file: UploadFile = File(...)):
            self._require_auth(req)
            self._uploads_dir.mkdir(parents=True, exist_ok=True)

            raw_name = Path(file.filename or "upload.bin").name
            clean_name = raw_name.replace("/", "").replace("\\", "").replace("..", "")
            if not clean_name:
                clean_name = "upload.bin"

            dest = self._uploads_dir / clean_name
            if dest.exists():
                stem = dest.stem
                suffix = dest.suffix
                counter = 1
                while (self._uploads_dir / f"{stem}_{counter}{suffix}").exists():
                    counter += 1
                dest = self._uploads_dir / f"{stem}_{counter}{suffix}"

            max_bytes = int(self.MAX_UPLOAD_MB * 1024 * 1024)
            size = 0

            try:
                with open(dest, "wb") as f:
                    while chunk := await file.read(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            f.close()
                            if dest.exists():
                                dest.unlink()
                            raise HTTPException(status_code=413, detail="File too large")
                        f.write(chunk)
            except HTTPException:
                raise
            except Exception as e:
                if dest.exists():
                    dest.unlink()
                raise HTTPException(status_code=500, detail=str(e))

            if self._upload_callback:
                try:
                    self._upload_callback(dest)
                except Exception as e:
                    print(f"[Dashboard] upload callback failed: {e}")
            return {"ok": True, "name": dest.name, "size": size}

        @app.get("/api/files")
        async def list_files(req: Request):
            self._require_auth(req)
            self._uploads_dir.mkdir(parents=True, exist_ok=True)
            files = []
            for p in self._uploads_dir.iterdir():
                if p.is_file() and not p.name.startswith("."):
                    files.append({"name": p.name, "size": p.stat().st_size})
            return {"ok": True, "files": files}

        @app.get("/uploads/{filename}")
        async def get_upload(filename: str, req: Request, token: Optional[str] = None):
            self._require_auth(req, query_token=token)
            uploads_root = self._uploads_dir.resolve()
            target = (self._uploads_dir / filename).resolve()

            # Enforce path containment and reject symlink escapes
            try:
                target.relative_to(uploads_root)
            except ValueError:
                raise HTTPException(status_code=404, detail="File not found")

            # Check if target is a symlink pointing outside uploads_root
            if target.is_symlink() or (self._uploads_dir / filename).is_symlink():
                real_target = (self._uploads_dir / filename).resolve()
                try:
                    real_target.relative_to(uploads_root)
                except ValueError:
                    raise HTTPException(status_code=404, detail="File not found")

            if not target.exists() or not target.is_file():
                raise HTTPException(status_code=404, detail="File not found")

            return FileResponse(target)

        @app.get("/static/crypto.js")
        async def get_crypto_js():
            js_file = STATIC_DIR / "crypto-js.min.js"
            if js_file.exists():
                return FileResponse(js_file, media_type="application/javascript")
            raise HTTPException(status_code=404, detail="CryptoJS not cached")

        @app.get("/static/aethelark.css")
        async def get_aethelark_css():
            css_file = STATIC_DIR / "aethelark.css"
            if css_file.exists():
                return FileResponse(css_file, media_type="text/css")
            raise HTTPException(status_code=404, detail="CSS not found")

        @app.get("/static/starfield.js")
        async def get_starfield_js():
            js_file = STATIC_DIR / "starfield.js"
            if js_file.exists():
                return FileResponse(js_file, media_type="application/javascript")
            raise HTTPException(status_code=404, detail="JS not found")

        @app.get("/static/dashboard.js")
        async def get_dashboard_js():
            js_file = STATIC_DIR / "dashboard.js"
            if js_file.exists():
                return FileResponse(js_file, media_type="application/javascript")
            raise HTTPException(status_code=404, detail="JS not found")

        @app.get("/static/fonts/{fontname}")
        async def get_static_font(fontname: str):
            # Contained the same way /uploads/ is. Today the router alone
            # stops a traversal here — a path parameter does not match "/",
            # and encoded slashes are decoded before matching, so `../` never
            # survives to reach the join. Measured: every payload 404s.
            #
            # That is a property of the router, not of this route, and this
            # route is unauthenticated. It sits two directories above
            # config/api_keys.json.
            fonts_root = (STATIC_DIR / "fonts").resolve()
            font_file = (STATIC_DIR / "fonts" / fontname).resolve()
            try:
                font_file.relative_to(fonts_root)
            except ValueError:
                raise HTTPException(status_code=404, detail="Font not found")
            if font_file.is_file():
                return FileResponse(font_file)
            raise HTTPException(status_code=404, detail="Font not found")

        @app.get("/api/swarm/events")
        async def swarm_events(req: Request):
            self._require_auth(req)

            async def event_generator():
                last_hash = ""
                while True:
                    snapshot = {"status": "running", "timestamp": time.time()}
                    dump = json.dumps(snapshot, sort_keys=True)
                    h = hashlib.md5(dump.encode("utf-8"), usedforsecurity=False).hexdigest()
                    if h != last_hash:
                        last_hash = h
                        yield f"data: {dump}\n\n"
                    await asyncio.sleep(1.0)

            return StreamingResponse(event_generator(), media_type="text/event-stream")

        @app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            # Refused before accept(), so an unauthorised peer is never sent
            # the buffered history and never gets to speak into the queue.
            if not self._authorise_ws(ws):
                await ws.close(code=1008)
                return
            await ws.accept()
            self._clients.add(ws)
            try:
                # Send buffered history
                for msg in list(self._history):
                    await ws.send_json(msg)

                while True:
                    data = await ws.receive_text()
                    try:
                        parsed = json.loads(data)
                        if isinstance(parsed, dict) and "command" in parsed:
                            await self._command_queue.put(str(parsed["command"]))
                    except Exception:
                        pass
            except WebSocketDisconnect:
                pass
            finally:
                self._clients.discard(ws)

    def _find_available_port(self, start_port: int, max_tries: int = 15) -> int:
        import socket
        for p in range(start_port, start_port + max_tries):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind((self.host, p))
                    return p
                except OSError:
                    continue
        return start_port

    async def serve(self) -> None:
        """Starts uvicorn ASGI server and runs firewall unblocking in a thread."""
        try:
            self.port = self._find_available_port(self.port)
            await asyncio.to_thread(netaccess.ensure_open, self.port)

            use_ssl = await asyncio.to_thread(ensure_certificate)
            ssl_key, ssl_cert = CERT_KEY, CERT_FILE

            cfg = uvicorn.Config(
                self.app,
                host=self.host,
                port=self.port,
                log_level="warning",
                ssl_keyfile=str(ssl_key) if use_ssl else None,
                ssl_certfile=str(ssl_cert) if use_ssl else None,
            )
            server = uvicorn.Server(cfg)
            try:
                await server.serve()
            except (OSError, SystemExit) as e:
                print(f"[Dashboard] Warning: Could not bind dashboard on {self.host}:{self.port} ({e})")
        except Exception as e:
            print(f"[Dashboard] Warning: Dashboard server start error ({e})")
