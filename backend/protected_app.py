import json
import threading
import time
from collections import defaultdict, deque
from io import BytesIO

from werkzeug.wrappers import Request, Response

from backend.server import app as flask_app


WINDOW_SECONDS = 60
MAX_DISCOVERY_REQUESTS = 5
CACHE_TTL_SECONDS = 900
MAX_CACHE_ENTRIES = 100

_lock = threading.Lock()
_requests = defaultdict(deque)
_cache = {}


def _client_key(environ):
    forwarded = environ.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return environ.get("REMOTE_ADDR", "unknown")


def _normalized_payload(raw_body):
    try:
        data = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if not isinstance(data, dict):
        return None, None

    need = str(data.get("need") or "General Financial Assistance").strip().lower()
    location = str(data.get("location") or "Location not specified").strip().lower()
    scope = str(data.get("scope") or "local").strip().lower()
    goal = data.get("goal")
    if goal in ("", None):
        goal = None
    key = json.dumps(
        {"need": need, "location": location, "goal": goal, "scope": scope},
        sort_keys=True,
        separators=(",", ":"),
    )
    return key, data


def _clone_environ(environ, body):
    copied = environ.copy()
    copied["wsgi.input"] = BytesIO(body)
    copied["CONTENT_LENGTH"] = str(len(body))
    return copied


def _cached_response(entry):
    response = Response(
        entry["body"],
        status=entry["status"],
        content_type=entry["content_type"],
    )
    response.headers["X-DeepSearch-Cache"] = "HIT"
    response.headers["X-DeepSearch-Cache-TTL"] = str(CACHE_TTL_SECONDS)
    return response


class DiscoveryGuard:
    """Protect credit-consuming discovery requests with rate limiting and short caching."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET").upper()

        if path == "/api/test-discovery":
            response = Response(
                "The public discovery smoke-test endpoint is disabled.",
                status=404,
                content_type="text/plain; charset=utf-8",
            )
            return response(environ, start_response)

        if path != "/api/discover" or method != "POST":
            return self.app(environ, start_response)

        request = Request(environ)
        raw_body = request.get_data(cache=False)
        cache_key, _ = _normalized_payload(raw_body)
        now = time.monotonic()

        if cache_key:
            with _lock:
                entry = _cache.get(cache_key)
                if entry and now - entry["created"] < CACHE_TTL_SECONDS:
                    return _cached_response(entry)(environ, start_response)
                if entry:
                    _cache.pop(cache_key, None)

        key = _client_key(environ)
        with _lock:
            recent = _requests[key]
            while recent and now - recent[0] >= WINDOW_SECONDS:
                recent.popleft()
            if len(recent) >= MAX_DISCOVERY_REQUESTS:
                response = Response(
                    "Discovery request limit reached. Please wait before searching again.",
                    status=429,
                    content_type="text/plain; charset=utf-8",
                    headers={"Retry-After": str(WINDOW_SECONDS)},
                )
                return response(environ, start_response)
            recent.append(now)

        captured = {}

        def capture_start_response(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = headers
            return lambda data: None

        app_iter = self.app(_clone_environ(environ, raw_body), capture_start_response)
        try:
            body = b"".join(app_iter)
        finally:
            close = getattr(app_iter, "close", None)
            if close:
                close()

        status_line = captured.get("status", "500 Internal Server Error")
        status_code = int(status_line.split(" ", 1)[0])
        headers = captured.get("headers", [])
        content_type = next(
            (value for name, value in headers if name.lower() == "content-type"),
            "application/json",
        )

        if cache_key and 200 <= status_code < 300:
            with _lock:
                if len(_cache) >= MAX_CACHE_ENTRIES:
                    oldest_key = min(_cache, key=lambda item: _cache[item]["created"])
                    _cache.pop(oldest_key, None)
                _cache[cache_key] = {
                    "created": now,
                    "status": status_code,
                    "content_type": content_type,
                    "body": body,
                }

        response = Response(body, status=status_code, content_type=content_type)
        response.headers["X-DeepSearch-Cache"] = "MISS"
        return response(environ, start_response)


flask_app.wsgi_app = DiscoveryGuard(flask_app.wsgi_app)
app = flask_app
