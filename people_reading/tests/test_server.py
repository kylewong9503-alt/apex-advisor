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


def test_settlement_end_to_end_over_http(tmp_path):
    from people_reading.llm import ScriptedModel
    from people_reading.tests import test_step4 as T4
    w, st, it, pm, mid, iid = T4.c2(tmp_path)
    it.model.outputs = [T4.PREP_OUT, T4.C3_OUT, {"branch": "A"}, T4.REVISED]
    pm.prepare(); pm.confirm_categories(); pm.formalize(T4.DIST_H)
    pm.answer_fork("c1", T4.C3_TEXT); pm.revise_after_fork("A", T4.C3_TEXT)
    pm.record_action("目标", ["分工说明"]); pm.lock_prediction(T4.DIST_P, T4.S.PREMISE_F1)
    path = st.path; st.close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel([T4.report_out(T4.REPORT_DRAFTS), T4.proposal(), T4.REVIEW]))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        ck = login(s)
        # 没有开始结算时，乱发结算请求一律 409，不改任何状态
        for p, b in (("/settle/report", "text=x"), ("/settle/premise", "pid=a&choice=0"), ("/settle/outcome", "choice=0")):
            assert req(s, "POST", p, b, headers=ck)[0] == 409
        assert req(s, "POST", "/settle/start", f"matter={mid}", headers=ck)[0] == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "什么时候的事" in body
        assert req(s, "POST", "/settle/report", "text=x", headers=ck)[0] == 409  # 没说时间就不能提交
        assert req(s, "POST", "/settle/when", "choice=0", headers=ck)[0] == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "<textarea" in body and "role='radio'" not in body.split("<script>")[0]  # 首屏开放题，没有候选答案
        assert "先问职责" not in body and "专家需求" not in body
        assert req(s, "POST", "/settle/report", "text=" + quote(T4.REPORT), headers=ck)[0] == 303
        q = s.app.sess.premise_questions()[0]
        body = req(s, "GET", "/", headers=ck)[2]
        assert "先核对前提" in body
        assert req(s, "POST", "/settle/premise", f"pid={q['premise_id']}&choice=99", headers=ck)[0] == 409
        assert req(s, "POST", "/settle/premise", f"pid={q['premise_id']}&choice={q['options'].index('都没有')}", headers=ck)[0] == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "这次结果算哪一种" in body
        cats = list(s.app.sess._behavior_for()[0].categories)
        assert req(s, "POST", "/settle/outcome", f"choice={cats.index('专家需求')}", headers=ck)[0] == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "4×" in body and "结果" in body
        assert s.app.w.engine.preds["P-1"].st.status == "settled"
        assert req(s, "POST", "/settle/outcome", "choice=0", headers=ck)[0] == 409  # 已结算，不能再确认（幂等）
        assert req(s, "POST", "/settle/done", headers=ck)[0] == 303 and s.app.sess is None
        assert s.app.w.engine.preds["P-1"].st.status == "settled"  # 结束只关闭这次会话，不动结果
    finally:
        s.shutdown(); s.server_close()


def test_settlement_late_report_is_recall_and_not_counted(tmp_path):
    from people_reading.llm import ScriptedModel
    from people_reading.tests import test_step4 as T4
    w, st, it, pm, mid, iid = T4.c2(tmp_path)
    it.model.outputs = [T4.PREP_OUT, T4.C3_OUT, {"branch": "A"}, T4.REVISED]
    pm.prepare(); pm.confirm_categories(); pm.formalize(T4.DIST_H)
    pm.answer_fork("c1", T4.C3_TEXT); pm.revise_after_fork("A", T4.C3_TEXT)
    pm.record_action("目标", ["分工说明"]); pm.lock_prediction(T4.DIST_P, T4.S.PREMISE_F1)
    path = st.path; st.close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel([T4.report_out(T4.REPORT_DRAFTS), T4.proposal(), T4.REVIEW]))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        ck = login(s)
        # 没有开始结算时，乱发结算请求一律 409，不改任何状态
        for p, b in (("/settle/report", "text=x"), ("/settle/premise", "pid=a&choice=0"), ("/settle/outcome", "choice=0")):
            assert req(s, "POST", p, b, headers=ck)[0] == 409
        assert req(s, "POST", "/settle/start", f"matter={mid}", headers=ck)[0] == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "什么时候的事" in body
        assert req(s, "POST", "/settle/report", "text=x", headers=ck)[0] == 409  # 没说时间就不能提交
        assert req(s, "POST", "/settle/when", "choice=1", headers=ck)[0] == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "<textarea" in body and "role='radio'" not in body.split("<script>")[0]  # 首屏开放题，没有候选答案
        assert "先问职责" not in body and "专家需求" not in body
        assert req(s, "POST", "/settle/report", "text=" + quote(T4.REPORT), headers=ck)[0] == 303
        q = s.app.sess.premise_questions()[0]
        body = req(s, "GET", "/", headers=ck)[2]
        assert "先核对前提" in body
        assert req(s, "POST", "/settle/premise", f"pid={q['premise_id']}&choice=99", headers=ck)[0] == 409
        assert req(s, "POST", "/settle/premise", f"pid={q['premise_id']}&choice={q['options'].index('都没有')}", headers=ck)[0] == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "这次结果算哪一种" in body
        cats = list(s.app.sess._behavior_for()[0].categories)
        assert req(s, "POST", "/settle/outcome", f"choice={cats.index('专家需求')}", headers=ck)[0] == 303
        rows = list(s.app.w.engine.rows.values()) if isinstance(s.app.w.engine.rows, dict) else list(s.app.w.engine.rows)
        assert rows and all(getattr(r, "status", None) != "counted" for r in rows)  # R10：事后回忆不进入比较
    finally:
        s.shutdown(); s.server_close()


def _prep_server(tmp_path, prep_out):
    from people_reading.llm import ScriptedModel
    from people_reading.tests import test_step4 as T4
    w, st, it, pm, mid, iid = T4.c2(tmp_path)
    path = st.path; st.close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel([prep_out]))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, mid


def test_lock_prediction_by_choices_only(tmp_path):
    from people_reading.tests import test_step4 as T4
    prep = {**T4.PREP_OUT, "premise_options": [{"action": "主动展示或讲解分工说明", "anchor_event": "F 提出第一个实质性问题"}]}
    s, mid = _prep_server(tmp_path, prep)
    try:
        ck = login(s)
        body = req(s, "GET", "/", headers=ck)[2]
        assert "/lock/start" in body  # 会前答案页上有“锁定预测”
        assert req(s, "POST", "/lock/commit", headers=ck)[0] == 409  # 没开始就不能锁
        assert req(s, "POST", "/lock/start", f"matter={mid}", headers=ck)[0] == 303
        nxt = lambda k, c, extra="": req(s, "POST", "/lock/next", f"k={k}&choice={c}{extra}", headers=ck)[0]  # noqa: E731
        assert "会后按这几类结算" in req(s, "GET", "/", headers=ck)[2]
        assert nxt("lean", 0) == 409  # 不能跳步
        assert nxt("cats", 0) == 303
        for hid, c in (("H1", 0), ("H2", 1)):  # 先问各解释指向哪一类（形式化）
            assert "他会先问哪一类" in req(s, "GET", "/", headers=ck)[2]
            assert nxt("hyp", c, f"&hid={hid}") == 303
        assert s.app.w.hypotheses["H1"].formal and s.app.w.hypotheses["H2"].formal
        assert nxt("lean", 0) == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "职责 60% · 专家需求 20% · 其他 20%" in body  # 把握的换算由程序给出
        assert nxt("conf", 9) == 409 and nxt("conf", 1) == 303
        assert nxt("premise", 0) == 303 and nxt("until", 0) == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "锁定前看一眼" in body and "若F 提出第一个实质性问题始终没有发生，以会议结束为截止" in body
        assert not s.app.w.engine.preds  # 看一眼时还没有锁定
        assert req(s, "POST", "/lock/commit", headers=ck)[0] == 303
        (p,) = s.app.w.engine.preds.values()
        assert p.st.status == "locked" and dict(p.pre.prob_dist) == {"职责": 0.6, "专家需求": 0.2, "其他": 0.2}
        assert p.pre.premises[0].cutoff.endswith("以会议结束为截止") and p.pre.premises[0].anchor_event
        assert req(s, "POST", "/lock/commit", headers=ck)[0] == 409  # 不会锁第二次
    finally:
        s.shutdown(); s.server_close()


def test_lock_without_premise_options_and_cancel(tmp_path):
    from people_reading.tests import test_step4 as T4
    s, mid = _prep_server(tmp_path, T4.PREP_OUT)
    try:
        ck = login(s)
        req(s, "GET", "/", headers=ck)
        req(s, "POST", "/lock/start", f"matter={mid}", headers=ck)
        assert req(s, "POST", "/lock/next", "k=cats&choice=1", headers=ck)[0] == 303  # 先不锁定
        assert s.app.lk is None and not s.app.w.engine.preds
        req(s, "POST", "/lock/start", f"matter={mid}", headers=ck)
        for k, c, x in (("cats", 0, ""), ("hyp", 0, "&hid=H1"), ("hyp", 1, "&hid=H2"), ("lean", 1, ""), ("conf", 3, "")):
            assert req(s, "POST", "/lock/next", f"k={k}&choice={c}{x}", headers=ck)[0] == 303
        assert "没有前提" in req(s, "GET", "/", headers=ck)[2]  # 模型没给前提候选：直接到“看一眼”
        assert req(s, "POST", "/lock/commit", headers=ck)[0] == 303
        (p,) = s.app.w.engine.preds.values()
        assert p.pre.premises == () and abs(sum(dict(p.pre.prob_dist).values()) - 1) < 1e-9
    finally:
        s.shutdown(); s.server_close()


def test_profile_sums_to_one_and_matches_design():
    from people_reading.premeeting import profile
    cats = ("职责", "专家需求", "其他")
    assert profile(cats, "职责", 0.6) == {"职责": 0.6, "专家需求": 0.2, "其他": 0.2}
    for p in (0.8, 0.6, 0.45, None):
        assert abs(sum(profile(cats, "职责", p).values()) - 1) < 1e-9
