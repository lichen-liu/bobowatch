"""Private WSGI API; HTTP parser/connection management belongs to Waitress."""

import hmac
import json
import logging
import sqlite3
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .access import read_access
from .config import load, token_hash
from .network import allowed_authority, local_ipv4
from .protocol import MAX_BODY, Conflict, Invalid, validate
from .storage import Busy

log = logging.getLogger(__name__)
ASSETS = {
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class Application:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self._reload_lock = threading.RLock()
        self.admin_hash = token_hash(config.admin_token_file.read_text().strip())
        self.tokens = {r["id"]: r["token_hash"] for r in config.replicas if not r.get("retired")}
        self.dashboard_access = read_access(config.path.parent)
        self._state = (self.config, self.admin_hash, self.tokens, self.dashboard_access)
        self.assets = {
            url: ((Path(__file__).parent / "web" / name).read_bytes(), mime)
            for url, (name, mime) in ASSETS.items()
        }

    def reload(self):
        with self._reload_lock:
            return self._reload()

    def _reload(self):
        config = load(self.config.path)
        admin_hash = token_hash(config.admin_token_file.read_text().strip())
        access = read_access(config.path.parent)
        if (
            config == self.config
            and admin_hash == self.admin_hash
            and access == self.dashboard_access
        ):
            return False
        if (
            config.path,
            config.database,
            config.host,
            config.port,
            config.demo,
            config.ram_required,
        ) != (
            self.config.path,
            self.config.database,
            self.config.host,
            self.config.port,
            self.config.demo,
            self.config.ram_required,
        ):
            raise Invalid("Listener, storage and demo-mode changes require a restart")
        if config.ram_required and config.quota_bytes > self.store.storage_status()["volume_bytes"]:
            raise Invalid(
                "Requested quota exceeds the mounted RAM capacity; stop and replace the volume"
            )
        tokens = {r["id"]: r["token_hash"] for r in config.replicas if not r.get("retired")}
        self.store.reload_inventory(config)
        self.config, self.admin_hash, self.tokens = config, admin_hash, tokens
        self.dashboard_access = access
        self._state = (config, admin_hash, tokens, access)
        return True

    def __call__(self, environ, start_response):
        status, body, mime = 500, {"error": "Internal service error"}, "application/json"
        try:
            status, body, mime = self.dispatch(environ)
        except Invalid as exc:
            status, body = 400, {"error": str(exc)}
        except Conflict as exc:
            status, body = 409, {"error": str(exc)}
        except (Busy, sqlite3.OperationalError):
            status, body = 503, {"error": "Storage unavailable; retry with the same sequence"}
        except Exception:
            log.exception("Unhandled monitoring request")
        if not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
        phrases = {
            200: "OK",
            202: "Accepted",
            400: "Bad Request",
            401: "Unauthorized",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
            409: "Conflict",
            413: "Content Too Large",
            415: "Unsupported Media Type",
            500: "Internal Server Error",
            503: "Service Unavailable",
        }
        headers = [
            ("Content-Type", mime),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            (
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            ),
        ]
        if status == 503:
            headers.append(("Retry-After", "2"))
        start_response(f"{status} {phrases[status]}", headers)
        return [body]

    def dispatch(self, env):
        config, admin_hash, tokens, access = self._state
        method, path = env["REQUEST_METHOD"], env.get("PATH_INFO", "/")
        host = env.get("HTTP_HOST", "")
        if not local_ipv4(env.get("REMOTE_ADDR", "")):
            return 403, {"error": "Access is limited to local-network clients"}, "application/json"
        if not allowed_authority(host, config):
            return 403, {"error": "Unrecognized Host header"}, "application/json"
        origin = env.get("HTTP_ORIGIN")
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                return 403, {"error": "Invalid request origin"}, "application/json"
            if parsed.scheme != "http" or parsed.netloc != host or parsed.path:
                return 403, {"error": "Cross-origin requests are not allowed"}, "application/json"
        if method == "GET" and path in self.assets:
            body, mime = self.assets[path]
            return 200, body, mime
        if method == "GET" and path == "/health":
            return 200, {"status": "ok"}, "application/json"
        if path == "/api/access":
            if method != "GET":
                return 405, {"error": "Use GET"}, "application/json"
            return 200, {"dashboard_access": access}, "application/json"
        if path not in ("/api/overview", "/v1/ingest", "/api/storage", "/api/control"):
            return 404, {"error": "Not found"}, "application/json"
        auth = env.get("HTTP_AUTHORIZATION", "")
        digest = token_hash(auth[7:]) if auth.startswith("Bearer ") and len(auth) < 512 else ""
        if path in ("/api/storage", "/api/control"):
            if env.get("REMOTE_ADDR") != "127.0.0.1" or not hmac.compare_digest(digest, admin_hash):
                return 403, {"error": "Local administrator access required"}, "application/json"
            from .control import dispatch

            return dispatch(self, env, path)
        if path == "/api/overview":
            if method != "GET":
                return 405, {"error": "Use GET"}, "application/json"
            if access != "local" and not hmac.compare_digest(digest, admin_hash):
                return 401, {"error": "Admin token required"}, "application/json"
            raw_query = env.get("QUERY_STRING", "")
            if len(raw_query) > 2048:
                raise Invalid("Query is too long")
            try:
                query = parse_qs(raw_query, keep_blank_values=True, max_num_fields=10)
            except ValueError:
                raise Invalid("Too many query fields") from None
            if set(query) - {"project", "site", "host", "replica", "window"} or any(
                len(v) != 1 for v in query.values()
            ):
                raise Invalid("Invalid filter parameters")
            window = query.get("window", ["300"])[0]
            if window not in ("30", "60", "300", "900", "1800", "3600", "21600", "86400"):
                raise Invalid("Unsupported time window")
            filters = {
                key + "_id" if key != "replica" else "id": query[key][0]
                for key in ("project", "site", "host", "replica")
                if query.get(key, [""])[0]
            }
            data = self.store.overview(filters, int(window))
            data["storage"] = self.store.storage_status()
            data["dashboard_access"] = access
            return 200, data, "application/json"
        if method != "POST":
            return 405, {"error": "Use POST"}, "application/json"
        replica = next(
            (key for key, value in tokens.items() if hmac.compare_digest(digest, value)), None
        )
        if replica is None:
            return 401, {"error": "Producer token required"}, "application/json"
        if env.get("CONTENT_TYPE", "").split(";")[0] != "application/json":
            return 415, {"error": "Use application/json"}, "application/json"
        try:
            length = int(env.get("CONTENT_LENGTH", "0"))
        except ValueError:
            raise Invalid("Invalid Content-Length") from None
        if not 0 < length <= MAX_BODY:
            return 413, {"error": "Snapshot must be between 1 byte and 256 KiB"}, "application/json"
        body = env["wsgi.input"].read(length)
        if len(body) != length:
            raise Invalid("Incomplete request body")

        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise Invalid("Duplicate JSON field")
                result[key] = value
            return result

        try:
            payload = json.loads(
                body,
                object_pairs_hook=unique_object,
                parse_constant=lambda _: (_ for _ in ()).throw(Invalid("Nonfinite JSON number")),
            )
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise Invalid("Malformed JSON snapshot") from None
        result = self.store.ingest(validate(payload), replica)
        return 200, result, "application/json"
