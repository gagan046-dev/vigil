"""Polite, safe page fetching: SSRF guard, robots.txt, per-host gap, httpx or Playwright."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import queue
from .metrics import FETCH_SECONDS

log = logging.getLogger(__name__)

USER_AGENT = "VigilBot/0.1 (+personal web watcher; respects robots.txt)"
MAX_BYTES = 3 * 1024 * 1024
ROBOTS_TTL_S = 3600
_ANY_URL = re.compile(r".*")


class FetchError(Exception):
    """A check could not fetch the page (message is shown to the user)."""


class UnsafeURLError(FetchError):
    pass


class RobotsBlockedError(FetchError):
    pass


@dataclass
class FetchResult:
    html: str
    status_code: int
    final_url: str
    rendered: bool
    duration_ms: int = 0


# ---------------------------------------------------------------- SSRF guard


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not (
        addr.is_multicast or addr.is_reserved or addr.is_loopback or addr.is_link_local or addr.is_private
    )


def check_url_safety(url: str, allowed_hosts: Iterable[str] = (), resolver: Callable = socket.getaddrinfo) -> None:
    """Raise UnsafeURLError unless url is http(s) and every resolved IP is public.

    Hosts in allowed_hosts (e.g. the in-cluster demo shop) skip the IP check.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:  # e.g. a port outside 0-65535
        raise UnsafeURLError("the URL is malformed") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError("only http and https URLs can be watched")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no host")
    if parts.username or parts.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    if host in {h.lower() for h in allowed_hosts}:
        return
    try:
        infos = resolver(host, port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise FetchError(f"could not resolve host {host}") from exc
    ips = {info[4][0] for info in infos}
    if not ips:
        raise FetchError(f"could not resolve host {host}")
    for ip in ips:
        if not _ip_is_public(ip):
            raise UnsafeURLError(f"blocked: {host} resolves to a non-public address")


# ---------------------------------------------------------------- robots.txt


class RobotsCache:
    def __init__(
        self,
        http_get: Callable[[str], httpx.Response] | None = None,
        ttl_s: int = ROBOTS_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._get = http_get
        self._ttl = ttl_s
        self._clock = clock
        self._cache: dict[str, tuple[float, robotparser.RobotFileParser]] = {}

    def _load(self, base: str) -> robotparser.RobotFileParser:
        rp = robotparser.RobotFileParser()
        try:
            resp = self._get(base + "/robots.txt") if self._get else None
        except Exception:
            resp = None
        if resp is None:
            rp.allow_all = True
        elif resp.status_code in (401, 403):
            rp.disallow_all = True
        elif resp.status_code >= 400:
            rp.allow_all = True
        else:
            rp.parse(resp.text.splitlines())
        return rp

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
        now = self._clock()
        hit = self._cache.get(base)
        if not hit or now - hit[0] > self._ttl:
            hit = (now, self._load(base))
            self._cache[base] = hit
        return hit[1].can_fetch(USER_AGENT, url)


# ---------------------------------------------------------------- fetcher


class Fetcher:
    """fetch(url, render) = safety + robots + politeness + download."""

    def __init__(
        self,
        allowed_hosts: Iterable[str] = (),
        r=None,
        host_gap_ms: int = 5000,
        resolver: Callable = socket.getaddrinfo,
        http: httpx.Client | None = None,
    ):
        self.allowed_hosts = tuple(allowed_hosts)
        self.r = r
        self.host_gap_ms = host_gap_ms
        self.resolver = resolver
        self.http = http or httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            max_redirects=5,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
            event_hooks={"request": [self._guard_request]},
        )
        self.robots = RobotsCache(self._robots_get)
        self._pw = None
        self._browser = None

    # every request (including each redirect hop) passes the SSRF guard
    def _guard_request(self, request: httpx.Request) -> None:
        check_url_safety(str(request.url), self.allowed_hosts, self.resolver)

    def _robots_get(self, url: str) -> httpx.Response:
        return self.http.get(url, timeout=10.0)

    def prepare(self, url: str) -> None:
        check_url_safety(url, self.allowed_hosts, self.resolver)
        if not self.robots.allowed(url):
            raise RobotsBlockedError("blocked by robots.txt")
        if self.r is not None:
            host = (urlsplit(url).hostname or "").lower()
            try:
                queue.wait_host_slot(self.r, host, self.host_gap_ms)
            except Exception:
                log.warning("politeness check failed; continuing")

    def fetch(self, url: str, render: bool = False) -> FetchResult:
        self.prepare(url)
        start = time.monotonic()
        mode = "browser" if render else "http"
        try:
            result = self._fetch_browser(url) if render else self._fetch_http(url)
        finally:
            FETCH_SECONDS.labels(mode=mode).observe(time.monotonic() - start)
        result.duration_ms = int((time.monotonic() - start) * 1000)
        return result

    def _fetch_http(self, url: str) -> FetchResult:
        try:
            with self.http.stream("GET", url) as resp:
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code} from {urlsplit(url).hostname}")
                chunks: list[bytes] = []
                size = 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FetchError("page is larger than 3 MB")
                    chunks.append(chunk)
                raw = b"".join(chunks)
                encoding = resp.charset_encoding or "utf-8"
                try:
                    html = raw.decode(encoding, errors="replace")
                except LookupError:
                    html = raw.decode("utf-8", errors="replace")
                return FetchResult(html, resp.status_code, str(resp.url), rendered=False)
        except FetchError:
            raise
        except httpx.TimeoutException as exc:
            raise FetchError("timed out fetching the page") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"could not fetch the page ({type(exc).__name__})") from exc

    # -- Playwright: one browser per worker process, a fresh context per fetch
    def _browser_instance(self):
        if self._browser is not None and not self._browser.is_connected():
            log.warning("browser disconnected; relaunching")  # e.g. Chromium crashed / OOM-killed
            self._browser = None
            try:
                if self._pw is not None:
                    self._pw.stop()
            except Exception:
                log.debug("playwright stop failed", exc_info=True)
            self._pw = None
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(args=["--disable-dev-shm-usage"])
        return self._browser

    def _fetch_browser(self, url: str) -> FetchResult:
        try:
            browser = self._browser_instance()
        except Exception as exc:
            raise FetchError(f"browser unavailable ({type(exc).__name__})") from exc
        safe_cache: dict[str, bool] = {}

        def guard(route):
            req_url = route.request.url
            host = urlsplit(req_url).hostname or ""
            if host not in safe_cache:
                try:
                    if req_url.startswith(("http://", "https://")):
                        check_url_safety(req_url, self.allowed_hosts, self.resolver)
                        safe_cache[host] = True
                    else:
                        safe_cache[host] = req_url.startswith(("data:", "blob:"))
                except FetchError:
                    safe_cache[host] = False
            if safe_cache[host]:
                route.continue_()
            else:
                route.abort()

        def guard_ws(ws):
            # page.route() does not see WebSockets: guard them separately
            try:
                check_url_safety(
                    ws.url.replace("ws://", "http://", 1).replace("wss://", "https://", 1),
                    self.allowed_hosts,
                    self.resolver,
                )
            except FetchError:
                ws.close()
                return
            ws.connect_to_server()

        # service workers could bypass page.route(), so they are blocked
        context = browser.new_context(user_agent=USER_AGENT, java_script_enabled=True, service_workers="block")
        try:
            page = context.new_page()
            page.route("**/*", guard)
            page.route_web_socket(_ANY_URL, guard_ws)
            try:
                resp = page.goto(url, wait_until="networkidle", timeout=30_000)
            except Exception as exc:
                raise FetchError(f"browser could not load the page ({type(exc).__name__})") from exc
            status = resp.status if resp else 200
            # defence in depth: never hand back content whose URL (or any redirect hop) is unsafe
            hops = [page.url]
            req = resp.request if resp else None
            while req is not None:
                hops.append(req.url)
                req = req.redirected_from
            for hop in hops:
                if hop.startswith(("http://", "https://")):
                    check_url_safety(hop, self.allowed_hosts, self.resolver)
            if status >= 400:
                raise FetchError(f"HTTP {status} from {urlsplit(url).hostname}")
            html = page.content()
            if len(html) > MAX_BYTES:
                html = html[:MAX_BYTES]
            return FetchResult(html, status, page.url, rendered=True)
        finally:
            context.close()

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            log.debug("browser shutdown failed", exc_info=True)
        self.http.close()
