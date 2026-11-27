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


# ───────── 写操作：说一句 → 草稿 → 确认；外部模型提示 ─────────
def _fresh(tmp_path, outputs, model_cls=None):
    from people_reading.llm import ScriptedModel
    path = str(tmp_path / "n.db")
    Store(path, PW).close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel(outputs))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def test_say_confirm_flow_writes_only_after_user_confirms(tmp_path):
    from people_reading.tests import test_step4 as T4
    s = _fresh(tmp_path, [T4.C1_OUT])
    try:
        ck = login(s)
        st, _, body = req(s, "GET", "/", headers=ck)
        assert "今天想记一件什么事" in body and "<textarea" in body
        assert req(s, "POST", "/say", "text=" + quote(T4.C1_TEXT), headers=ck)[0] == 303
        st, _, body = req(s, "GET", "/", headers=ck)
        assert "记下这一条吗" in body and "原话：" in body  # 先出确认，不直接写入
        assert not s.app.w.observations  # 没确认前世界里什么都没有
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        assert req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)[0] == 303
        assert any(d["status"] == "confirmed" for d in s.app.lock.store.drafts())
        assert req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)[0] == 409  # 已处理，不重复
        assert req(s, "POST", "/confirm", "id=D999&choice=0", headers=ck)[0] == 409
    finally:
        s.shutdown(); s.server_close()


def test_reject_draft_leaves_world_unchanged(tmp_path):
    from people_reading.tests import test_step4 as T4
    s = _fresh(tmp_path, [T4.C1_OUT])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote(T4.C1_TEXT), headers=ck)
        n = len(s.app.w.persons)
        for d in s.app.lock.store.drafts(status="pending"):  # 一条一条都选“不记”
            assert req(s, "POST", "/confirm", f"id={d['id']}&choice=1", headers=ck)[0] == 303
        assert s.app.lock.store.drafts(status="pending") == [] and len(s.app.w.persons) == n
        assert not s.app.w.matters and not s.app.w.observations  # 全部拒绝：世界里什么都没有
    finally:
        s.shutdown(); s.server_close()


def test_model_failure_keeps_text_and_asks_for_manual(tmp_path):
    s = _fresh(tmp_path, ["不是 JSON", "还是不是", "仍然不是"])
    try:
        ck = login(s)
        assert req(s, "POST", "/say", "text=" + quote("随手记一句"), headers=ck)[0] == 303
        st, _, body = req(s, "GET", "/", headers=ck)
        assert st == 200 and "今天想记一件什么事" in body  # 没有编造的草稿
    finally:
        s.shutdown(); s.server_close()


def test_banner_only_when_external_model_is_on(tmp_path, monkeypatch):
    s = _fresh(tmp_path, [])
    try:
        assert "外部模型已开启" not in req(s, "GET", "/", headers=login(s))[2]
        monkeypatch.setenv("PR_ALLOW_EXTERNAL", "1"); monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-key-not-real")
        from people_reading.llm import RemoteModel
        s.app.model = RemoteModel()  # 只构造，不发请求
        body = req(s, "GET", "/", headers=login(s))[2]
        assert "外部模型已开启" in body and "dummy-key-not-real" not in body
    finally:
        s.shutdown(); s.server_close()
