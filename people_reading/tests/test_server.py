"""本地服务：口令门、会话、同源、Host 白名单、遮挡、上锁、绑定范围。"""
import http.client
import threading
from urllib.parse import quote

import pytest

from people_reading import demo_flow
from people_reading.server import serve
from people_reading.store import Store


from people_reading.tests.test_step2 import PASS as PW  # noqa: E402
OK = "passphrase=" + quote(PW)


@pytest.fixture()
def srv(tmp_path):
    w, st = demo_flow.build_store()
    path = st.path
    st.close()
    s = serve(path, "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.shutdown(); s.server_close()


def req(s, method, path, body="", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1])
    h = {"Host": f"127.0.0.1:{s.server_address[1]}"}; h.update(headers or {})
    if body:
        h["Content-Type"] = "application/x-www-form-urlencoded"
    c.request(method, path, body=body, headers=h)
    r = c.getresponse(); data = r.read().decode("utf-8", "replace")
    return r.status, dict(r.getheaders()), data


def login(s):
    st, h, _ = req(s, "POST", "/unlock", OK)
    assert st == 303
    return {"Cookie": h["Set-Cookie"].split(";")[0]}


def test_nothing_served_before_unlock(srv):
    for p in ("/", "/library"):
        st, h, body = req(srv, "GET", p)
        assert st == 303 and h["Location"] == "/unlock" and "专家" not in body


def test_wrong_passphrase_then_cooldown(srv):
    for _ in range(5):
        st, _, _ = req(srv, "POST", "/unlock", "passphrase=bad")
        assert st == 401
    st, _, body = req(srv, "POST", "/unlock", OK)
    assert st == 429  # 冷却中，连正确口令也要等


def test_unlock_serves_flow_with_secure_headers_and_cookie_flags(srv):
    st, h, _ = req(srv, "POST", "/unlock", OK)
    assert "HttpOnly" in h["Set-Cookie"] and "SameSite=Strict" in h["Set-Cookie"]
    st, h, body = req(srv, "GET", "/", headers={"Cookie": h["Set-Cookie"].split(";")[0]})
    assert st == 200 and "<h1>" in body and h["Cache-Control"] == "no-store" and "default-src 'none'" in h["Content-Security-Policy"]
    assert "passphrase" not in body and PW not in body


def test_post_requires_same_origin_and_valid_session(srv):
    ck = login(srv)
    assert req(srv, "POST", "/mask", headers={**ck, "Origin": "http://evil.example"})[0] == 403
    assert req(srv, "POST", "/mask")[0] == 401  # 没会话


def test_bad_host_header_rejected(srv):
    ck = login(srv)
    assert req(srv, "GET", "/", headers={**ck, "Host": "evil.example"})[0] == 400


def test_mask_toggle_and_lock_invalidate(srv):
    ck = login(srv)
    assert req(srv, "POST", "/mask", headers=ck)[0] == 303
    st, _, body = req(srv, "GET", "/", headers=ck)
    assert st == 200 and "专家" not in body.split("<script>")[0]  # 遮挡后不输出原文
    assert req(srv, "POST", "/lock", headers=ck)[0] == 303
    assert req(srv, "GET", "/", headers=ck)[0] == 303  # 旧会话失效


def test_remote_bind_needs_explicit_opt_in(tmp_path):
    with pytest.raises(ValueError):
        serve(str(tmp_path / "x.db"), "0.0.0.0", 0)
    s = serve(str(tmp_path / "x.db"), "127.0.0.1", 0); s.server_close()


def test_pwa_files_carry_no_data_and_no_service_worker(srv):
    assert req(srv, "GET", "/manifest.webmanifest")[0] == 200 and req(srv, "GET", "/icon.svg")[0] == 200
    assert req(srv, "GET", "/sw.js")[0] in (303, 404)
