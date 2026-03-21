from web.oauth import google_auth_url


def test_google_auth_url_con_rifa_id():
    url = google_auth_url(nonce="abc123", rifa_id=42)
    assert "state=rifa%3A42%3Aabc123" in url or "state=rifa:42:abc123" in url


def test_google_auth_url_con_next_url():
    url = google_auth_url(nonce="abc123", next_url="/admin")
    assert "next" in url
    assert "admin" in url
    assert "abc123" in url


def test_google_auth_url_state_formato_rifa():
    url = google_auth_url(nonce="mynonce", rifa_id=7)
    from urllib.parse import urlparse, parse_qs, unquote
    parsed = urlparse(url)
    state = unquote(parse_qs(parsed.query)["state"][0])
    assert state == "rifa:7:mynonce"


def test_google_auth_url_state_formato_next():
    url = google_auth_url(nonce="mynonce", next_url="/admin")
    from urllib.parse import urlparse, parse_qs, unquote
    parsed = urlparse(url)
    state = unquote(parse_qs(parsed.query)["state"][0])
    assert state == "next:/admin:mynonce"
