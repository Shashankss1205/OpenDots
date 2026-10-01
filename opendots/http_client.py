"""Bounded JSON HTTP calls without redirects or remote error-body disclosure."""
import json
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_url(url):
    if not isinstance(url, str):
        raise ValueError("Endpoint URL must be a string")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise ValueError("Invalid endpoint URL") from None
    if not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError("Endpoint needs a host and must not contain user credentials or a fragment")
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("Endpoints require HTTPS, except HTTP on loopback for local services")
    return url


def post_json(url, payload, headers=None, timeout=15, *, parse_response=True):
    validate_url(url)
    body = json.dumps(payload, allow_nan=False).encode()
    if len(body) > 2_000_000:
        raise ValueError("HTTP request exceeds 2 MB")
    request = Request(url, data=body, headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Remote endpoint returned HTTP {response.status}")
            if not parse_response:
                return None
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("HTTP response exceeds 1 MB")
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise RuntimeError(f"Remote endpoint returned HTTP {code}; inspect credentials, limits and configuration locally") from None
    except (URLError, TimeoutError, OSError, HTTPException, ValueError):
        raise RuntimeError("Remote endpoint connection failed or timed out") from None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError("Remote endpoint did not return valid JSON") from None
