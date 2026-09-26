import threading
import time
from collections import defaultdict, deque

from werkzeug.wrappers import Response

from backend.server import app as flask_app


WINDOW_SECONDS = 60
MAX_DISCOVERY_REQUESTS = 5
_lock = threading.Lock()
_requests = defaultdict(deque)


def _client_key(environ):
    forwarded = environ.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return environ.get("REMOTE_ADDR", "unknown")


class DiscoveryGuard:
    """Small deployment guard for credit-consuming discovery endpoints."""

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

        if path == "/api/discover" and method == "POST":
            now = time.monotonic()
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

        return self.app(environ, start_response)


flask_app.wsgi_app = DiscoveryGuard(flask_app.wsgi_app)
app = flask_app
