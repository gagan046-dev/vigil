import socket

import httpx
import pytest
import respx

from vigil.fetch import (
    USER_AGENT,
    Fetcher,
    FetchError,
    RobotsBlockedError,
    RobotsCache,
    UnsafeURLError,
    check_url_safety,
)


def resolver_for(mapping):
    def resolve(host, port, proto=0, **_):
        if host not in mapping:
            raise socket.gaierror("nope")
        return [
            (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in mapping[host]
        ]

    return resolve


PUBLIC = resolver_for({"example.com": ["93.184.215.14"], "shop.example.com": ["93.184.215.14"]})


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fd00::1",
        "100.64.0.1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        "240.0.0.1",
    ],
)
def test_blocks_non_public_ips(ip):
    with pytest.raises(UnsafeURLError):
        check_url_safety("http://internal.test/", (), resolver_for({"internal.test": [ip]}))


def test_blocks_if_any_resolved_ip_private():
    res = resolver_for({"mixed.test": ["93.184.215.14", "10.1.2.3"]})
    with pytest.raises(UnsafeURLError):
        check_url_safety("https://mixed.test/", (), res)


def test_allows_public():
    check_url_safety("https://example.com/x", (), PUBLIC)
    check_url_safety("https://[2606:4700::1111]/", (), resolver_for({"2606:4700::1111": ["2606:4700::1111"]}))


def test_scheme_credentials_and_resolution_errors():
    with pytest.raises(UnsafeURLError):
        check_url_safety("file:///etc/passwd", (), PUBLIC)
    with pytest.raises(UnsafeURLError):
        check_url_safety("ftp://example.com", (), PUBLIC)
    with pytest.raises(UnsafeURLError):
        check_url_safety("http://user:pw@example.com/", (), PUBLIC)
    with pytest.raises(FetchError):
        check_url_safety("http://does-not-exist.test/", (), PUBLIC)


def test_allowlist_bypasses_ip_check():
    res = resolver_for({"vigil-demo-shop": ["10.43.0.12"]})
    check_url_safety("http://vigil-demo-shop:8000/static", ("vigil-demo-shop",), res)
    check_url_safety("http://VIGIL-DEMO-SHOP/", ("vigil-demo-shop",), res)
    with pytest.raises(UnsafeURLError):
        check_url_safety("http://vigil-demo-shop/", (), res)


def _resp(status, text=""):
    return httpx.Response(status, text=text)


def test_robots_rules_and_cache():
    calls = []

    def get(url):
        calls.append(url)
        return _resp(200, "User-agent: *\nDisallow: /private\n")

    rc = RobotsCache(get)
    assert rc.allowed("https://example.com/public")
    assert not rc.allowed("https://example.com/private/page")
    assert calls == ["https://example.com/robots.txt"]  # cached


def test_robots_status_codes_and_ttl():
    assert RobotsCache(lambda u: _resp(404)).allowed("https://a.test/x")
    assert not RobotsCache(lambda u: _resp(403)).allowed("https://a.test/x")

    def boom(u):
        raise httpx.ConnectError("down")

    assert RobotsCache(boom).allowed("https://a.test/x")
    now = [0.0]
    calls = []
    rc = RobotsCache(lambda u: calls.append(u) or _resp(404), ttl_s=3600, clock=lambda: now[0])
    rc.allowed("https://a.test/")
    now[0] = 4000
    rc.allowed("https://a.test/")
    assert len(calls) == 2


@respx.mock
def test_static_fetch_uses_user_agent_and_robots():
    respx.get("https://example.com/robots.txt").mock(return_value=_resp(200, "User-agent: *\nAllow: /\n"))
    route = respx.get("https://example.com/p").mock(return_value=_resp(200, "<p>hi</p>"))
    f = Fetcher(resolver=PUBLIC)
    res = f.fetch("https://example.com/p")
    assert res.html == "<p>hi</p>" and res.rendered is False and res.status_code == 200
    assert route.calls[0].request.headers["user-agent"] == USER_AGENT


@respx.mock
def test_static_fetch_blocked_by_robots():
    respx.get("https://example.com/robots.txt").mock(return_value=_resp(200, "User-agent: *\nDisallow: /\n"))
    with pytest.raises(RobotsBlockedError, match="robots.txt"):
        Fetcher(resolver=PUBLIC).fetch("https://example.com/p")


@respx.mock
def test_redirect_to_private_ip_is_blocked():
    res = resolver_for({"example.com": ["93.184.215.14"], "evil.test": ["127.0.0.1"]})
    respx.get("https://example.com/robots.txt").mock(return_value=_resp(404))
    respx.get("https://example.com/r").mock(
        return_value=httpx.Response(302, headers={"Location": "http://evil.test/admin"})
    )
    respx.get("http://evil.test/admin").mock(return_value=_resp(200, "secret"))
    with pytest.raises(UnsafeURLError):
        Fetcher(resolver=res).fetch("https://example.com/r")


@respx.mock
def test_http_error_and_size_limit(monkeypatch):
    respx.get("https://example.com/robots.txt").mock(return_value=_resp(404))
    respx.get("https://example.com/missing").mock(return_value=_resp(404))
    respx.get("https://example.com/big").mock(return_value=httpx.Response(200, content=b"x" * 2048))
    f = Fetcher(resolver=PUBLIC)
    with pytest.raises(FetchError, match="HTTP 404"):
        f.fetch("https://example.com/missing")
    monkeypatch.setattr("vigil.fetch.MAX_BYTES", 1024)
    with pytest.raises(FetchError, match="3 MB"):
        f.fetch("https://example.com/big")


def test_malformed_port_is_rejected_cleanly():
    with pytest.raises(UnsafeURLError):
        check_url_safety("http://example.com:99999/", resolver=PUBLIC)
