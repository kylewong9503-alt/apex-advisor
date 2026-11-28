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


def act_fields(body: str) -> dict:
    """页面上当前这一步“继续”按钮要提交的字段（服务端用它把提交绑定到所显示的题目）。"""
    import html as _h, json as _j, re as _re
    m = _re.findall(r"data-fields='([^']*)'", body)
    return _j.loads(_h.unescape(m[0])) if m else {}


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
        assert "都记下吗" in body and "记下所选" in body and "role='checkbox'" in body  # 先出确认，不直接写入
        assert not s.app.w.observations  # 没确认前世界里什么都没有
        pend = s.app.lock.store.drafts(status="pending")
        did, msg = pend[0]["id"], pend[0]["message_id"]
        assert req(s, "POST", "/confirm_many", f"msg={msg}&ids=D999", headers=ck)[0] == 409  # 不属于这一组的 id
        keep = ",".join(d["id"] for d in pend[:-1])  # 最后一条取消勾选 = 不记
        assert req(s, "POST", "/confirm_many", f"msg={msg}&ids={keep}", headers=ck)[0] == 303
        st_ = {d["id"]: d["status"] for d in s.app.lock.store.drafts()}
        assert st_[pend[-1]["id"]] == "rejected" and all(st_[i] == "confirmed" for i in keep.split(","))
        assert req(s, "POST", "/confirm_many", f"msg={msg}&ids={keep}", headers=ck)[0] == 409  # 已处理，不重复
        assert req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)[0] == 409
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
        qt = act_fields(body)["q"]
        assert q["premise_id"] not in qt  # 标识是带盐哈希，不含原文
        assert req(s, "POST", "/settle/premise", f"q={qt}&choice=99", headers=ck)[0] == 409
        assert req(s, "POST", "/settle/premise", f"choice={q['options'].index('都没有')}", headers=ck)[0] == 409  # 没带题目标识
        assert req(s, "POST", "/settle/premise", f"q={qt}&choice={q['options'].index('都没有')}", headers=ck)[0] == 303
        assert req(s, "POST", "/settle/premise", f"q={qt}&choice={q['options'].index('都没有')}", headers=ck)[0] == 409  # 重复提交不会答到别的题
        body = req(s, "GET", "/", headers=ck)[2]
        assert "这次结果算哪一种" in body
        cats = list(s.app.sess._behavior_for()[0].categories)
        assert req(s, "POST", "/settle/outcome", f"choice={cats.index('专家需求')}", headers=ck)[0] == 303
        from people_reading.skillset import labels
        n_labels = len(labels(s.app.lock.store))
        body = req(s, "GET", "/", headers=ck)[2]
        assert "记下" in body and "data-act='/confirm" in body  # 报告里的草稿先由你确认：打开页面不会替你确认
        rep = s.app.sess.report_drafts
        assert rep and all(d["status"] == "pending" for d in s.app.lock.store.drafts() if d["id"] in rep)
        assert len(labels(s.app.lock.store)) == n_labels  # 没确认就没有回测标签
        msg = next(d["message_id"] for d in s.app.lock.store.drafts() if d["id"] in rep)
        assert req(s, "POST", "/confirm_many", f"msg={msg}&ids={','.join(rep)}", headers=ck)[0] == 303
        assert len(labels(s.app.lock.store)) == n_labels + len(rep)
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
        qt = act_fields(body)["q"]
        assert q["premise_id"] not in qt  # 标识是带盐哈希，不含原文
        assert req(s, "POST", "/settle/premise", f"q={qt}&choice=99", headers=ck)[0] == 409
        assert req(s, "POST", "/settle/premise", f"choice={q['options'].index('都没有')}", headers=ck)[0] == 409  # 没带题目标识
        assert req(s, "POST", "/settle/premise", f"q={qt}&choice={q['options'].index('都没有')}", headers=ck)[0] == 303
        assert req(s, "POST", "/settle/premise", f"q={qt}&choice={q['options'].index('都没有')}", headers=ck)[0] == 409  # 重复提交不会答到别的题
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


def test_multiple_behaviors_on_one_meeting_ask_which_to_settle_first(tmp_path):
    from people_reading import scenarios as S
    from people_reading.engine import make_rule
    from people_reading.llm import ScriptedModel
    from people_reading.tests import test_step4 as T4
    w, st, it, pm, mid, iid = T4.c2(tmp_path)
    it.model.outputs = [T4.PREP_OUT]
    pm.prepare(); pm.confirm_categories(); pm.formalize(T4.DIST_H)
    pm.lock_prediction(T4.DIST_P, None)
    e = w.engine
    e.define_behavior(S.ASK)
    r1 = make_rule("RA", "总是追问", "ask_owner", {"kind": "const_binary", "p": 0.8})
    r2 = make_rule("RB", "很少追问", "ask_owner", {"kind": "const_binary", "p": 0.2})
    e.lock_prediction(id="P-9", person_id="F", matter_id=mid, behavior_key="ask_owner", bind_mode="condition_match",
                      conditions={"with": "F"}, rules=[r1, r2], prob=0.6, settlement_criterion="是否追问负责人")
    st.sync_world(w); path = st.path; st.close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel([]))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        ck = login(s)
        assert req(s, "POST", "/settle/start", headers=ck)[0] == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "这次先结算哪一项" in body and s.app.sess is None  # 不替你选
        qt = act_fields(body)["q"]
        assert req(s, "POST", "/settle/pick", "q=bad&choice=0", headers=ck)[0] == 409
        assert req(s, "POST", "/settle/pick", f"q={qt}&choice=0", headers=ck)[0] == 303
        assert s.app.sess is not None and s.app.sess.behavior_key == s.app._groups()[0][0]
        s.app.sess._behavior_for()  # 指明了行为与版本：不再报“需要 behavior_key”
    finally:
        s.shutdown(); s.server_close()


def test_serif_font_served_locally_without_login_and_csp_allows_only_self(srv):
    import re
    from people_reading.theme import FONTS_CSS_FILE, FONTS_DIR
    st, h, css = req(srv, "GET", "/fonts.css")
    assert st == 200 and h["Content-Type"].startswith("text/css") and "max-age" in h["Cache-Control"]
    names = set(re.findall(r"url\(fonts/([^)]*)\)", css))
    assert names and all((FONTS_DIR / n).is_file() for n in names)  # 样式表里引用的每个字体文件都真的存在
    for n in ("noto-serif-sc-latin-wght-normal.woff2", sorted(names)[0]):
        st, h, _ = req(srv, "GET", "/fonts/" + n)
        assert st == 200 and h["Content-Type"] == "font/woff2"
    assert (FONTS_DIR / sorted(names)[0]).read_bytes()[:4] == b"wOF2"
    assert req(srv, "GET", "/fonts/../server.py")[0] == 404 and req(srv, "GET", "/fonts/anything.woff2")[0] == 404  # 不能读到别的文件
    ck = login(srv)
    st, h, page = req(srv, "GET", "/", headers=ck)
    assert "font-src 'self'" in h["Content-Security-Policy"] and "href='fonts.css'" in page
    assert "fonts.googleapis" not in page and "https://" not in page
    assert "Noto Serif SC" in page and "Inter" not in page and "Noto Sans SC" not in page  # 全部是思源宋体


def test_codespaces_host_only_inside_codespaces():
    from people_reading.server import codespaces_host
    assert codespaces_host({}) == "" and codespaces_host({"CODESPACE_NAME": "x"}) == ""
    assert codespaces_host({"CODESPACE_NAME": "fuzzy-fiesta-1", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev"}) == "fuzzy-fiesta-1-8765.app.github.dev"


def test_model_failure_shows_a_notice_once_and_logs_only_the_reason(tmp_path, capfd):
    s = _fresh(tmp_path, ["不是 JSON", "还是不是", "仍然不是"])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote("很私密的原话内容"), headers=ck)
        body = req(s, "GET", "/", headers=ck)[2]
        assert "role='alert'" in body and "模型没能解析" in body
        assert "role='alert'" not in req(s, "GET", "/", headers=ck)[2]  # 只出现一次
        assert "很私密的原话内容" not in capfd.readouterr().err  # 日志里只有原因，没有原话
    finally:
        s.shutdown(); s.server_close()


def test_submit_button_shows_busy_state():
    from people_reading.flow import FLOW_JS
    assert "处理中…" in FLOW_JS and "aria-busy" in FLOW_JS


def test_origin_check_behind_a_rewriting_proxy(tmp_path):
    from people_reading.llm import ScriptedModel
    path = str(tmp_path / "p.db"); Store(path, PW).close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel([]), allowed_hosts=["kagami.example.net"])
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        ck = login(s)
        # 代理把 Host 改成 localhost，但 Origin 是白名单里的真实域名：放行
        ok = req(s, "POST", "/mask", headers={**ck, "Origin": "https://kagami.example.net"})
        assert ok[0] == 303
        # Origin 不在白名单、也不等于 Host：仍然拒绝
        assert req(s, "POST", "/mask", headers={**ck, "Origin": "https://evil.example"})[0] == 403
        # 白名单是精确匹配，不是后缀匹配
        assert req(s, "POST", "/mask", headers={**ck, "Origin": "https://kagami.example.net.evil.example"})[0] == 403
    finally:
        s.shutdown(); s.server_close()


# ───────── 真实浏览器：从页面上点按钮走一遍（之前所有服务端测试都是直接发请求，漏掉了页面本身的两个问题）─────────
import shutil  # noqa: E402
import subprocess  # noqa: E402

BROWSER_JS = r"""
const {chromium}=require('playwright');(async()=>{const [port,pw]=process.argv.slice(2);
const b=await chromium.launch();const p=await b.newPage({viewport:{width:1280,height:800}});
const out={posts:[],errors:[]};p.on('pageerror',e=>out.errors.push(e.message));p.on('console',m=>{if(m.type()==='error')out.errors.push(m.text())});
p.on('response',r=>{if(r.request().method()==='POST')out.posts.push(new URL(r.url()).pathname+' '+r.status())});
await p.goto('http://127.0.0.1:'+port+'/unlock');await p.fill('input[name=passphrase]',pw);await p.click('button[type=submit]');await p.waitForURL('**/');
await p.fill('textarea','明天要和 F 谈，想争取集团专家支持，希望他引荐一次。');
await p.click('.step.on button.btn.p');
await p.waitForFunction(()=>/都记下吗/.test(document.querySelector('.step.on h1').textContent),null,{timeout:8000});
out.h1=await p.evaluate(()=>document.querySelector('.step.on h1').textContent);
out.checked=await p.evaluate(()=>document.querySelectorAll('.step.on .opt[aria-checked=true]').length);
await p.click('.step.on .opt >> nth=2');   // 取消勾选第 3 条
await p.click('.step.on button.btn.p');
await p.waitForFunction(()=>!/都记下吗/.test(document.querySelector('.step.on h1').textContent),null,{timeout:8000});
out.after=await p.evaluate(()=>document.querySelector('.step.on h1').textContent);
console.log(JSON.stringify(out));await b.close()})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="需要 node + playwright 做真实页面检查")
def test_real_browser_submit_then_confirm_with_one_unchecked(tmp_path):
    from people_reading.llm import ScriptedModel
    from people_reading.tests import test_step4 as T4
    path = str(tmp_path / "b.db"); Store(path, PW).close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel([T4.C1_OUT]))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    js = tmp_path / "b.js"; js.write_text(BROWSER_JS)
    try:
        r = subprocess.run(["node", str(js), str(s.server_address[1]), PW], capture_output=True, text=True, timeout=90)
        if "Cannot find module" in r.stderr:
            pytest.skip("没有 playwright")
        assert r.returncode == 0, r.stderr[-800:]
        import json as _j
        out = _j.loads(r.stdout.strip().splitlines()[-1])
        assert out["errors"] == [], out["errors"]  # 没有脚本错误，也没有被 CSP 拦掉的请求
        assert "/say 303" in out["posts"] and "/confirm_many 303" in out["posts"], out["posts"]
        assert "都记下吗" in out["h1"] and out["checked"] == 3 and "都记下吗" not in out["after"]
        # 第 3 条被取消勾选 = 不记
        st = {d["id"]: d["status"] for d in s.app.lock.store.drafts()}
        assert sorted(st.values()).count("rejected") == 1 and sorted(st.values()).count("confirmed") == 2
    finally:
        s.shutdown(); s.server_close()


def test_every_button_with_an_action_renders_its_attributes():
    """页面上每个带动作的按钮都必须真的带 data-act（之前有个提交按钮的属性没被渲染，点了没反应）。"""
    from people_reading.flow import ask, check, open_ask, render_flow
    steps = [open_ask("q", act={"url": "/say", "fields": {}}), ask("q", [("a", "", "")], act={"url": "/x", "fields": {}}),
             check("q", [("D1", "t", "")], act={"url": "/y", "fields": {}}),
             {"kind": "answer", "cue": "", "line": "l", "reason": "r", "buttons": [{"label": "b", "act": {"url": "/z", "fields": {}}}]},
             {"kind": "summary", "cue": "", "title": "t", "rows": [], "note": "n", "act": {"url": "/c", "fields": {}}, "back": {"url": "/r", "fields": {}}},
             {"kind": "result", "cue": "", "title": "t", "big": "", "big_note": "", "note": "", "act": {"url": "/d", "fields": {}}}]
    page = render_flow(steps)
    for u in ("/say", "/x", "/y", "/z", "/c", "/r", "/d"):
        assert f"data-act='{u}'" in page, u
    assert "{actattr" not in page  # 没有未展开的模板片段


# ───────── 记下之后：回执 + 解读 → 选择 → 留意（不再停在“记下这一条”）─────────
READ_TEXT = "我的老板 F 突然跟我要 A 和 B 的合作"
READ_DRAFT = {"kind": "behavior", "reporter": "我", "subject": "F", "content": "F 要合作", "is_interpretation": False,
              "source_quote": "F 突然跟我要 A 和 B 的合作", "behavior_key": None, "occurred": None}
READ_OUT = {"explanations": [
    {"statement": "她在为某个汇报找素材", "evidence": "突然跟我要", "signal": "接下来一周里她是否在会上引用这两家的情况"},
    {"statement": "她想试探我手上有没有这两家的资源", "evidence": "要 A 和 B 的合作", "signal": "她之后是否追问我和这两家的关系"},
    {"statement": "她编造的依据", "evidence": "原话里根本没有这句", "signal": "无"}]}


def _reading_server(tmp_path, outs):
    from people_reading.llm import ScriptedModel
    from people_reading.tests import test_step4 as T4
    from people_reading.tests.test_step2 import extraction
    w, st, it = T4.env(tmp_path, [])
    path = st.path; st.close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel([extraction(drafts=[READ_DRAFT])] + outs))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def test_after_confirming_a_record_the_flow_continues_with_a_reading(tmp_path):
    s = _reading_server(tmp_path, [READ_OUT])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote(READ_TEXT), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        assert req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)[0] == 303
        rc = req(s, "GET", "/", headers=ck)[2].split("<script>")[0]
        assert "已记下" in rc and "打开档案" in rc and "/person/" in rc and "看解读" in rc  # 先给回执
        assert "几种说得通的解释" not in rc and not s.app.model.prompts[1:]  # 解读要点了才算（模型没被调用）
        assert req(s, "POST", "/reading/start", headers=ck)[0] == 303
        body = req(s, "GET", "/", headers=ck)[2]
        assert "几种说得通的解释" in body and "她在为某个汇报找素材" in body and "她想试探我手上" in body
        assert "她编造的依据" not in body and "原话里根本没有这句" not in body  # 依据在原话里找不到的解释直接剔除，不拿整句顶替
        assert req(s, "POST", "/reading/pick", "choice=0", headers=ck)[0] == 409  # 不能跳步
        assert req(s, "POST", "/reading/next", headers=ck)[0] == 303
        assert "你觉得哪种更像" in req(s, "GET", "/", headers=ck)[2]
        assert req(s, "POST", "/reading/pick", "choice=9", headers=ck)[0] == 409
        assert req(s, "POST", "/reading/signal", "choice=0", headers=ck)[0] == 409  # 也不能提前到后面的步骤
        assert req(s, "POST", "/reading/pick", "choice=1", headers=ck)[0] == 303
        hy = [(h.statement, h.proposed_by) for h in s.app.w.hypotheses.values() if h.person_id == "F"]
        assert ("她想试探我手上有没有这两家的资源", "user") in hy and ("她在为某个汇报找素材", "model") in hy
        assert not any(h.formal for h in s.app.w.hypotheses.values() if h.statement.startswith("她"))  # 解释不进入比较
        body = req(s, "GET", "/", headers=ck)[2]
        assert "想让我替你留意哪一个现象" in body and "不用问对方，零代价" in body
        assert req(s, "POST", "/reading/signal", "choice=0", headers=ck)[0] == 303
        assert any(f.kind == "watch" and "会上引用" in f.text for f in s.app.w.followups)
        body = req(s, "GET", "/", headers=ck)[2]
        assert "已记下" in body and "1 条你看到的" in body
        assert req(s, "POST", "/reading/done", headers=ck)[0] == 303 and s.app.reading is None
        after = req(s, "GET", "/", headers=ck)[2].split("<script>")[0]
        assert "已记下" not in after and "role='alert'" not in after  # 走完了，回到下一件事
    finally:
        s.shutdown(); s.server_close()


def test_without_usable_reading_you_still_get_a_receipt_not_a_dead_end(tmp_path):
    s = _reading_server(tmp_path, ["不是 JSON", "还是不是", "仍然不是"])  # 模型给不出解读
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote(READ_TEXT), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)
        body = req(s, "GET", "/", headers=ck)[2]
        assert "已记下" in body and "几种说得通的解释" not in body  # 没有编造解释，但有回执
        assert req(s, "POST", "/reading/done", headers=ck)[0] == 303
    finally:
        s.shutdown(); s.server_close()


def test_read_event_validation():
    from people_reading.llm import ScriptedModel
    from people_reading.skillset import read_event
    text = "老板 F 突然跟我要 A 和 B 的合作"
    ok = lambda s, e, g: {"statement": s, "evidence": e, "signal": g}  # noqa: E731
    two = [ok("她在找素材", "突然跟我要", "之后是否引用"), ok("她在试探资源", "A 和 B 的合作", "之后是否追问")]
    assert len(read_event(ScriptedModel([{"explanations": two}]), text)) == 2
    assert read_event(None, text) is None
    for bad in (ok("她天生就爱试探", "突然跟我要", "x"),            # 性格标签
                ok("她已经问了 17 次", "突然跟我要", "x"),            # 原话里没有的数字
                ok("她答应引荐", "突然跟我要", "x")):                  # 越界说法
        why: list = []
        assert read_event(ScriptedModel([{"explanations": [two[0], bad]}]), text, diag=why) is None  # 剩一条就不给（不足 2 条）
        assert why and "不足 2 条" in why[0]  # 并且说明原因
    paraphrased = [ok("她想要资源", "模型改写过的依据", "之后是否追问"), two[0]]  # 依据找不到：这条剔除，剩一条就不给
    why = []
    assert read_event(ScriptedModel([{"explanations": paraphrased}]), text, diag=why) is None and "依据在原话里找不到" in why[0]
    three = paraphrased + [two[1]]
    got = read_event(ScriptedModel([{"explanations": three}]), text)
    assert got and [x["statement"] for x in got] == ["她在找素材", "她在试探资源"] and all(x["evidence"] in text for x in got)
    assert read_event(ScriptedModel([{"explanations": [two[0], dict(two[0])]}]), text) is None  # 重复的解释只算一条


# ───────── 人物标签：点一下就知道说的是谁，能对应到他的 profile ─────────
def _who_server(tmp_path, outs):
    from people_reading.llm import ScriptedModel
    from people_reading.tests import test_step4 as T4
    w, st, it = T4.env(tmp_path, [])
    for _ in range(2):  # M 被记录得比 F 多
        w.add_observation("MTG-X", ({"message_id": 1, "start": 0, "end": 1},), "behavior", "user", "M", "m", source_mode="contemporaneous")
    st.sync_world(w)
    path = st.path; st.close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel(outs))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def _chip_toks(body):
    import re
    return re.findall(r"<button type='button' class='mi' role='option' aria-selected='false' data-tok='([^']*)'>([^<]*)</button>", body)


def test_people_chips_ordered_by_frequency_and_masked_in_private_view(tmp_path):
    s = _who_server(tmp_path, [])
    try:
        ck = login(s)
        chips = _chip_toks(req(s, "GET", "/", headers=ck)[2])
        labels = [l for _, l in chips]
        assert labels.index("M") < labels.index("F")  # 被记得多的在前
        assert "＋ 新的人" in req(s, "GET", "/", headers=ck)[2] and "@ 谁" in req(s, "GET", "/", headers=ck)[2]
        req(s, "POST", "/mask", headers=ck)
        body = req(s, "GET", "/", headers=ck)[2]
        assert [l for _, l in _chip_toks(body)] and all(l.startswith("●") for _, l in _chip_toks(body))  # 遮挡时标签也遮挡
        assert "data-tok='M'" not in body and "data-tok='F'" not in body  # 页面里没有人物 id
    finally:
        s.shutdown(); s.server_close()


def test_tag_binds_pronoun_subject_to_the_chosen_person(tmp_path):
    from people_reading.tests.test_step2 import extraction
    d = {"kind": "behavior", "reporter": "我", "subject": "他", "content": "他突然要合作", "is_interpretation": False,
         "source_quote": "他突然跟我要合作", "behavior_key": None, "occurred": None}
    s = _who_server(tmp_path, [extraction(drafts=[d])])
    try:
        ck = login(s)
        tok = dict((l, t) for t, l in _chip_toks(req(s, "GET", "/", headers=ck)[2]))["F"]
        assert req(s, "POST", "/say", f"text={quote('他突然跟我要合作')}&who={tok}", headers=ck)[0] == 303
        dr = s.app.lock.store.drafts(status="pending")[0]
        assert dr["proposed"]["subject_id"] == "F"  # “他”没法对应，但你点了 F
        ref = s.app.intake.confirm(dr["id"])
        assert next(o for o in s.app.w.observations.values() if o.id == ref).subject_person_id == "F"
    finally:
        s.shutdown(); s.server_close()


def test_new_person_is_created_once_and_appears_as_a_tag(tmp_path):
    from people_reading.tests.test_step2 import extraction
    d = {"kind": "behavior", "reporter": "我", "subject": "老板", "content": "突然要合作", "is_interpretation": False,
         "source_quote": "老板突然跟我要合作", "behavior_key": None, "occurred": None}
    s = _who_server(tmp_path, [extraction(drafts=[d]), extraction(drafts=[dict(d, source_quote="老板又来要合作")])])
    try:
        ck = login(s)
        n = len(s.app.w.persons)
        assert req(s, "POST", "/say", f"text={quote('老板突然跟我要合作')}&newname={quote('Judy')}", headers=ck)[0] == 303
        assert len(s.app.w.persons) == n + 1
        pid = next(p.id for p in s.app.w.persons.values() if p.display_name == "Judy")
        assert s.app.lock.store.drafts(status="pending")[0]["proposed"]["subject_id"] == pid
        # 再用同名：不重复建
        req(s, "POST", "/say", f"text={quote('老板又来要合作')}&newname={quote('Judy')}", headers=ck)
        assert len([p for p in s.app.w.persons.values() if p.display_name == "Judy"]) == 1
        assert "Judy" in [l for _, l in _chip_toks(req(s, "GET", "/say", headers=ck)[2])]
        # 重开后还在（落库了）
        assert any(p.display_name == "Judy" for p in s.app.lock.store.load_world()[0].persons.values())
    finally:
        s.shutdown(); s.server_close()


def test_bad_tag_token_is_rejected_and_creates_nothing(tmp_path):
    s = _who_server(tmp_path, [])
    try:
        ck = login(s)
        n = len(s.app.w.persons)
        assert req(s, "POST", "/say", f"text={quote('x')}&who=not-a-token", headers=ck)[0] == 409
        assert len(s.app.w.persons) == n and not s.app.lock.store.drafts()
        # 只点了标签没写话：什么也不做，也不建人
        assert req(s, "POST", "/say", f"text=&newname={quote('Zed')}", headers=ck)[0] == 303 and len(s.app.w.persons) == n
    finally:
        s.shutdown(); s.server_close()


BROWSER_TAG_JS = r"""
const {chromium}=require('playwright');(async()=>{const [port,pw]=process.argv.slice(2);
const b=await chromium.launch();const p=await b.newPage({viewport:{width:390,height:844}});const out={errors:[],posts:[]};
p.on('pageerror',e=>out.errors.push(e.message));p.on('console',m=>{if(m.type()==='error')out.errors.push(m.text())});
p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/say'))out.posts.push(r.postData())});
await p.goto('http://127.0.0.1:'+port+'/unlock');await p.fill('input[name=passphrase]',pw);await p.click('button[type=submit]');await p.waitForURL('**/');
await p.click('.step.on .tb[data-menu=who]');await p.click('.step.on .mi[data-new]');out.inputShown=await p.evaluate(()=>!document.querySelector('.step.on .newname').hidden);
await p.fill('.step.on .newname','Judy');await p.fill('textarea','老板突然跟我要合作');
await p.click('.step.on button.btn.p');await p.waitForTimeout(1500);console.log(JSON.stringify(out));await b.close()})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="需要 node + playwright")
def test_real_browser_new_person_tag_is_submitted(tmp_path):
    from people_reading.llm import ScriptedModel
    from people_reading.tests.test_step2 import extraction
    d = {"kind": "behavior", "reporter": "我", "subject": "老板", "content": "突然要合作", "is_interpretation": False,
         "source_quote": "老板突然跟我要合作", "behavior_key": None, "occurred": None}
    path = str(tmp_path / "bt.db"); Store(path, PW).close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel([extraction(drafts=[d])]))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    js = tmp_path / "t.js"; js.write_text(BROWSER_TAG_JS)
    try:
        r = subprocess.run(["node", str(js), str(s.server_address[1]), PW], capture_output=True, text=True, timeout=90)
        if "Cannot find module" in r.stderr:
            pytest.skip("没有 playwright")
        import json as _j
        out = _j.loads(r.stdout.strip().splitlines()[-1])
        assert out["errors"] == [] and out["inputShown"] is True
        assert any("newname=Judy" in (p or "") for p in out["posts"]), out["posts"]
        assert any(p.display_name == "Judy" for p in s.app.w.persons.values())
    finally:
        s.shutdown(); s.server_close()


# ───────── 场景标签：筛选和分类用，不影响分析 ─────────
def _scene_toks(body):
    import re
    return dict((l, t) for t, l in re.findall(r"role='option' aria-selected='false' data-stok='([^']*)'>([^<]*)</button>", body))


def test_scene_tags_are_attached_to_the_interaction_and_persist(tmp_path):
    from people_reading.tests.test_step2 import extraction
    d = {"kind": "behavior", "reporter": "我", "subject": "F", "content": "F 在会上追问", "is_interpretation": False,
         "source_quote": "F 在会上追问负责人", "behavior_key": None, "occurred": None}
    s = _who_server(tmp_path, [extraction(drafts=[d])])
    try:
        ck = login(s)
        toks = _scene_toks(req(s, "GET", "/", headers=ck)[2])
        assert set(toks) >= {"开会", "一对一", "汇报", "评审", "邮件/消息", "电话", "饭局/闲聊", "其他"}
        assert req(s, "POST", "/say", f"text={quote('F 在会上追问负责人')}&scenes={toks['开会']},{toks['汇报']}", headers=ck)[0] == 303
        dr = s.app.lock.store.drafts(status="pending")[0]
        assert dr["proposed"]["scenes"] == ["开会", "汇报"]
        ref = s.app.intake.confirm(dr["id"])
        o = next(o for o in s.app.w.observations.values() if o.id == ref)
        assert s.app.w.interactions[o.interaction_id]["scenes"] == ["开会", "汇报"]
        w2, _ = s.app.lock.store.load_world()  # 重开后还在
        assert w2.interactions[o.interaction_id]["scenes"] == ["开会", "汇报"]
    finally:
        s.shutdown(); s.server_close()


def test_custom_scene_created_once_capped_and_bad_tokens_rejected(tmp_path):
    s = _who_server(tmp_path, ["不是 JSON"] * 30)  # 模型给不出结果也无妨：这里只看标签本身
    try:
        ck = login(s)
        assert req(s, "POST", "/say", f"text={quote('x')}&newscene={quote('团建')}", headers=ck)[0] in (303,)
        assert "团建" in _scene_toks(req(s, "GET", "/say", headers=ck)[2])
        req(s, "POST", "/say", f"text={quote('y')}&newscene={quote('团建')}", headers=ck)
        from people_reading import scenes as SC
        assert SC.custom_scenes(s.app.lock.store) == ["团建"]  # 同名不重复
        assert SC.add_custom(s.app.lock.store, "一二三四五六七八九十十一十二十三十四")  == "一二三四五六七八九十十一"  # 过长截断
        for i in range(SC.MAX_CUSTOM):
            try:
                SC.add_custom(s.app.lock.store, f"场景{i}")
            except ValueError:
                break
        assert len(SC.custom_scenes(s.app.lock.store)) == SC.MAX_CUSTOM
        assert req(s, "POST", "/say", f"text={quote('z')}&newscene={quote('第十三个')}", headers=ck)[0] == 409  # 满了
        assert req(s, "POST", "/say", f"text={quote('z')}&scenes=bogus", headers=ck)[0] == 409
    finally:
        s.shutdown(); s.server_close()


def test_scene_labels_masked_and_profile_shows_and_filters_by_scene(tmp_path):
    from people_reading.tests.test_step2 import extraction
    d = {"kind": "behavior", "reporter": "我", "subject": "F", "content": "F 追问", "is_interpretation": False,
         "source_quote": "F 追问负责人", "behavior_key": None, "occurred": None}
    d2 = dict(d, content="F 发邮件", source_quote="F 发了邮件")
    s = _who_server(tmp_path, [extraction(drafts=[d]), extraction(drafts=[d2])])
    try:
        ck = login(s)
        toks = _scene_toks(req(s, "GET", "/", headers=ck)[2])
        for text, sc in (("F 追问负责人", "开会"), ("F 发了邮件", "邮件/消息")):
            req(s, "POST", "/say", f"text={quote(text)}&scenes={toks[sc]}", headers=ck)
            for dr in s.app.lock.store.drafts(status="pending"):
                s.app.intake.confirm(dr["id"])
        lib = req(s, "GET", "/library", headers=ck)[2].split("<script>")[0]
        assert "class='tag'>#开会<" in lib and "class='tag'>#邮件/消息<" in lib and "data-sf=" in lib
        import re
        assert re.search(r"<li data-scenes='\d'>", lib)  # 属性里只有序号
        req(s, "POST", "/mask", headers=ck)
        masked = req(s, "GET", "/library", headers=ck)[2].split("<script>")[0]
        assert "开会" not in masked and "邮件/消息" not in masked
        say = req(s, "GET", "/say", headers=ck)[2].split("<script>")[0]
        assert "开会" not in say and "data-stok=" in say  # 遮挡时场景名也不输出
    finally:
        s.shutdown(); s.server_close()


BROWSER_SCENE_JS = r"""
const {chromium}=require('playwright');(async()=>{const [port,pw]=process.argv.slice(2);
const b=await chromium.launch();const p=await b.newPage({viewport:{width:390,height:844}});const out={errors:[],posts:[]};
p.on('pageerror',e=>out.errors.push(e.message));p.on('console',m=>{if(m.type()==='error')out.errors.push(m.text())});
p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/say'))out.posts.push(r.postData())});
await p.goto('http://127.0.0.1:'+port+'/unlock');await p.fill('input[name=passphrase]',pw);await p.click('button[type=submit]');await p.waitForURL('**/');
await p.click('.step.on .tb[data-menu=where]');
out.heights=await p.evaluate(()=>[...document.querySelectorAll('.step.on .menu .mi')].filter(m=>m.offsetParent).map(m=>Math.round(m.getBoundingClientRect().height)));
await p.click('.step.on .mi[data-stok] >> nth=0');await p.click('.step.on .mi[data-stok] >> nth=2');
out.tokens=await p.evaluate(()=>[...document.querySelectorAll('.step.on .tk')].map(x=>x.textContent));
await p.click('.step.on .mi[data-newscn]');out.inputShown=await p.evaluate(()=>!document.querySelector('.step.on .newscene').hidden);
await p.fill('.step.on .newscene','团建');await p.fill('textarea','F 在团建上追问负责人');await p.click('.step.on button.btn.p');await p.waitForTimeout(1500);
console.log(JSON.stringify(out));await b.close()})();
"""


@pytest.mark.skipif(not shutil.which("node"), reason="需要 node + playwright")
def test_real_browser_scene_tags_are_small_and_submitted(tmp_path):
    from people_reading.llm import ScriptedModel
    from people_reading.tests.test_step2 import extraction
    d = {"kind": "behavior", "reporter": "我", "subject": "F", "content": "追问", "is_interpretation": False,
         "source_quote": "F 在团建上追问负责人", "behavior_key": None, "occurred": None}
    s = _who_server(tmp_path, [extraction(drafts=[d])])
    js = tmp_path / "sc.js"; js.write_text(BROWSER_SCENE_JS)
    try:
        r = subprocess.run(["node", str(js), str(s.server_address[1]), __import__("people_reading.tests.test_step4", fromlist=["x"]).PASS],
                           capture_output=True, text=True, timeout=90)
        if "Cannot find module" in r.stderr:
            pytest.skip("没有 playwright")
        import json as _j
        out = _j.loads(r.stdout.strip().splitlines()[-1])
        assert out["errors"] == [] and out["inputShown"] is True
        assert len(set(out["heights"])) == 1 and out["heights"][0] <= 44  # 所有选项等高，不再大小不一
        assert out["tokens"] == ["#开会 ×", "#汇报 ×"] or [x.replace(" ×", "") for x in out["tokens"]] == ["#开会", "#汇报"], out["tokens"]
        post = out["posts"][0]
        assert "newscene=" in post and post.count("scenes=") >= 1, post
        dr = s.app.lock.store.drafts(status="pending")[0]
        assert "团建" in dr["proposed"]["scenes"] and len(dr["proposed"]["scenes"]) == 3
    finally:
        s.shutdown(); s.server_close()


def test_open_step_is_a_composer_with_disabled_send_until_text():
    from people_reading.flow import open_ask, render_flow
    page = render_flow([open_ask("q", act={"url": "/say", "fields": {}}, people=[("t1", "F")], scenes=[("s1", "开会")])])
    assert "class='composer'" in page and "class='btn p send'" in page and " disabled>" in page
    assert page.index("class='menu who'") < page.index("class='composer'")  # 选择菜单在输入框上方，输入框里有 @ 谁 / # 场合
    assert "data-tok='t1'" in page and "data-stok='s1'" in page and "{actattr" not in page and "class='chip" not in page


def test_receipt_says_why_there_is_no_reading(tmp_path):
    s = _reading_server(tmp_path, ["不是 JSON", "还是不是", "仍然不是"])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote(READ_TEXT), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)
        req(s, "POST", "/reading/start", headers=ck)
        body = req(s, "GET", "/", headers=ck)[2].split("<script>")[0]
        assert "这次没有给出解读：" in body and "模型没给出合格的解读" in body  # 不再悄悄跳过
    finally:
        s.shutdown(); s.server_close()


def test_people_list_profile_and_preselected_say(tmp_path):
    s = _reading_server(tmp_path, [READ_OUT])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote(READ_TEXT), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)
        home = req(s, "GET", "/", headers=ck)[2]
        assert "href='/people'" in home  # 首页有“人物”入口
        pl = req(s, "GET", "/people", headers=ck)[2]
        assert "/person/" in pl and "1 条记录" in pl
        href = pl.split("/person/")[1].split("'")[0]
        pr = req(s, "GET", "/person/" + href, headers=ck)
        assert pr[0] == 200 and "/say?who=" + href in pr[2]
        assert req(s, "GET", "/person/bogus", headers=ck)[0] == 404
        sy = req(s, "GET", "/say?who=" + href, headers=ck)[2]
        assert f"data-pre='{href}'" in sy
        assert "data-pre=''" in req(s, "GET", "/say?who=bogus", headers=ck)[2]  # 令牌对不上：不预选
        s.app.masked = True
        mp = req(s, "GET", "/people", headers=ck)[2]
        assert "data-dk=''" in req(s, "GET", "/say", headers=ck)[2]  # 遮挡时不在浏览器里留草稿
        assert "F" not in mp.split("<main>")[1].replace("Fira", "")  # 人名不出现
    finally:
        s.shutdown(); s.server_close()


def test_missing_fact_is_asked_before_explanations_and_stored_on_the_interaction(tmp_path):
    out = dict(READ_OUT, missing_fact={"question": "她是当面说的还是发消息？", "options": ["当面", "消息"]})
    again = {"explanations": [{"statement": "她在书面留痕", "evidence": "突然跟我要", "signal": "之后是否再发消息"},
                               {"statement": "她想先试探", "evidence": "要 A 和 B 的合作", "signal": "之后是否追问"}]}
    s = _reading_server(tmp_path, [out, again])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote(READ_TEXT), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)
        req(s, "POST", "/reading/start", headers=ck)
        body = req(s, "GET", "/", headers=ck)[2]
        assert "她是当面说的还是发消息" in body and "记不清" in body and "几种说得通的解释" not in body
        assert req(s, "POST", "/reading/next", headers=ck)[0] == 409  # 不能跳过这一步直接看解读
        assert req(s, "POST", "/reading/fact", "choice=9", headers=ck)[0] == 409
        assert req(s, "POST", "/reading/fact", "choice=1", headers=ck)[0] == 303
        facts = [f for i in s.app.w.interactions.values() for f in i.get("facts", [])]
        assert facts == [{"q": "她是当面说的还是发消息？", "a": "消息", "by": "user"}]
        assert not any("消息" in o.content for o in s.app.w.observations.values())  # 补的事实不是观察
        body = req(s, "GET", "/", headers=ck)[2]
        assert "几种说得通的解释" in body and "她在书面留痕" in body and "她在为某个汇报找素材" not in body  # 带着答案重新生成
        assert "消息" in s.app.model.prompts[2] and "不是原话" in s.app.model.prompts[2]
        pr = req(s, "GET", "/person/" + req(s, "GET", "/people", headers=ck)[2].split("/person/")[1].split("'")[0], headers=ck)[2]
        assert "补充事实：她是当面说的还是发消息？ → 消息" in pr
    finally:
        s.shutdown(); s.server_close()


def test_bad_missing_fact_is_ignored(tmp_path):
    out = dict(READ_OUT, missing_fact={"question": "她花了 99 万吗？", "options": ["是", "否"]})  # 原话里没有的数字
    s = _reading_server(tmp_path, [out])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote(READ_TEXT), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)
        req(s, "POST", "/reading/start", headers=ck)
        assert "几种说得通的解释" in req(s, "GET", "/", headers=ck)[2]
    finally:
        s.shutdown(); s.server_close()


def test_profile_hints_open_watch_and_shows_other_people_in_the_same_event(tmp_path):
    s = _reading_server(tmp_path, [READ_OUT])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote(READ_TEXT), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)
        req(s, "POST", "/reading/start", headers=ck); req(s, "POST", "/reading/next", headers=ck)
        req(s, "POST", "/reading/pick", "choice=0", headers=ck); req(s, "POST", "/reading/signal", "choice=0", headers=ck)
        tok = req(s, "GET", "/people", headers=ck)[2].split("/person/")[1].split("'")[0]
        assert "有 1 条待核对的观察" in req(s, "GET", "/person/" + tok, headers=ck)[2]
        o = next(iter(s.app.w.observations.values()))
        # 同一件事还涉及另一个人：两边的档案都能看到这件事
        from people_reading.world import Person
        s.app.w.persons["G2"] = Person("G2", "第二位", code="G2")
        o.involved = ["G2"]
        pr = req(s, "GET", "/person/" + tok, headers=ck)[2]
        assert "同一件事还涉及：F、G2" in pr  # 页面按“代号”显示（遮挡时是●序号）
    finally:
        s.shutdown(); s.server_close()


def _two_people_server(tmp_path, outs):
    from people_reading.llm import ScriptedModel
    from people_reading.tests import test_step4 as T4
    from people_reading.tests.test_step2 import extraction
    dF = dict(READ_DRAFT)
    dM = dict(READ_DRAFT, subject="M", content="M 拒绝了预算", source_quote="M 拒绝了预算")
    w, st, it = T4.env(tmp_path, [])
    path = st.path; st.close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel([extraction(drafts=[dF, dM])] + outs))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def test_second_person_also_gets_the_missing_fact_question(tmp_path):
    ex = lambda a, b: [{"statement": a, "evidence": "突然跟我要", "signal": "之后是否再发"}, {"statement": b, "evidence": "突然跟我要", "signal": "之后是否追问"}]  # noqa: E731
    mf = {"question": "是当面吗？", "options": ["是", "否"]}
    rF = {"explanations": ex("F 在留痕", "F 在催促")}
    rM = {"explanations": [{"statement": "M 预算紧", "evidence": "M 拒绝了预算", "signal": "他是否批其他项目"},
                           {"statement": "M 在拖延", "evidence": "M 拒绝了预算", "signal": "他是否给出时间"}], "missing_fact": mf}
    s = _two_people_server(tmp_path, [rF, rM])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote("F 突然跟我要 A 和 B 的合作。M 拒绝了预算。"), headers=ck)
        pend = s.app.lock.store.drafts(status="pending")
        msg = pend[0]["message_id"]
        req(s, "POST", "/confirm_many", f"msg={msg}&ids={','.join(d['id'] for d in pend)}", headers=ck)
        req(s, "POST", "/reading/start", headers=ck); req(s, "POST", "/reading/who", "choice=0", headers=ck)
        req(s, "POST", "/reading/next", headers=ck); req(s, "POST", "/reading/pick", "choice=0", headers=ck)
        req(s, "POST", "/reading/signal", "choice=0", headers=ck)
        body = req(s, "GET", "/", headers=ck)[2]
        assert "是当面吗" in body and "几种说得通的解释" not in body  # 第二个人先补事实，不直接看到解释
        assert s.app.reading["stage"] == "fact"
    finally:
        s.shutdown(); s.server_close()


def test_profile_start_note_keeps_a_person_beyond_the_first_twelve(tmp_path):
    from people_reading.tests import test_step4 as T4
    from people_reading.world import Person
    w, st, it = T4.env(tmp_path, [])
    for i in range(14):
        w.persons[f"Z{i}"] = Person(f"Z{i}", f"人{i}", code=f"Z{i}")
    st.sync_world(w)
    path = st.path; st.close()
    s = serve(path, "127.0.0.1", 0)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        ck = login(s)
        rd = __import__("people_reading.privacy", fromlist=["Redactor"]).Redactor(s.app.w, "code")
        chips = {t for t, _ in s.app.people_chips(rd)}
        extra = next(p.id for p in s.app.w.persons.values() if p.id.startswith("Z") and s.app.tok("who", p.id) not in chips)
        tok = s.app.tok("who", extra)
        assert f"data-pre='{tok}'" in req(s, "GET", "/say?who=" + tok, headers=ck)[2]
    finally:
        s.shutdown(); s.server_close()


def test_open_watch_is_checked_when_the_same_person_is_recorded_again(tmp_path):
    s = _reading_server(tmp_path, [READ_OUT])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote(READ_TEXT), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)
        for u, c in (("/reading/start", ""), ("/reading/next", ""), ("/reading/pick", "choice=0"), ("/reading/signal", "choice=0")):
            req(s, "POST", u, c, headers=ck)
        req(s, "POST", "/reading/done", headers=ck)
        src = next(i for i in s.app.w.interactions.values() if i.get("watch"))
        assert src["watch"][0]["statement"] and src["watch"][0]["signal"] and src["watch"][0]["fid"]  # 来源、解释、现象都留着
        # 重新上锁再打开：留意项与来源仍在
        s.app.lock.lock(); s.app.on_lock()
        ck = login(s)
        assert any(i.get("watch") for i in s.app.w.interactions.values())
        from people_reading.tests.test_step2 import extraction
        s.app.model.outputs.append(extraction(drafts=[dict(READ_DRAFT, content="F 又发了一次", source_quote="F 又发了")]))
        req(s, "POST", "/say", "text=" + quote("F 又发了一次邀请"), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)
        body = req(s, "GET", "/", headers=ck)[2]
        assert "有一条待核对的观察" in body and "接下来一周里她是否在会上引用" in body
        assert req(s, "POST", "/reading/watch", "choice=7", headers=ck)[0] == 409
        c = s.app.reading["checks"][0]
        k = s.app.tok("watch", c["fid"], c["now"])
        assert req(s, "POST", "/reading/watch", "choice=1", headers=ck)[0] == 409  # 没带页面上的标识：拒绝
        assert req(s, "POST", "/reading/watch", f"choice=1&k={k}", headers=ck)[0] == 303
        fu = next(f for f in s.app.w.followups if f.kind == "watch")
        assert fu.status == "not_seen"
        assert next(i for i in s.app.w.interactions.values() if i.get("watch"))["watch"][0]["resolved"]["answer"] == "not_seen"
    finally:
        s.shutdown(); s.server_close()


def test_watch_answer_is_bound_to_the_item_shown_and_old_followups_are_checked(tmp_path):
    from people_reading.world import Followup
    s = _reading_server(tmp_path, [READ_OUT])
    try:
        ck = login(s)  # 两条升级前格式的开放留意项（没有来源元数据）
        for n in (1, 2):
            s.app.w.followups.append(Followup(f"F{n}", "watch", "F", "when_relevant", f"现象{n}"))
        req(s, "POST", "/say", "text=" + quote(READ_TEXT), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)
        r = s.app.reading
        page = req(s, "GET", "/", headers=ck)[2].split("<script>")[0]
        assert "关于F" in page and "现象1" in page and "新记录：“F 突然跟我要 A 和 B 的合作”" in page  # 说明在核对谁、哪条记录
        assert "与此无关 / 不确定" in page
        assert r["stage"] == "watch" and [c["fid"] for c in r["checks"]] == ["F1", "F2"]  # 旧格式也进入核对
        k1 = s.app.tok("watch", "F1", r["checks"][0]["now"])
        assert req(s, "POST", "/reading/watch", f"choice=0&k={k1}", headers=ck)[0] == 303
        assert req(s, "POST", "/reading/watch", f"choice=0&k={k1}", headers=ck)[0] == 409  # 重复提交同一题：不会写到第二条
        st = {f.id: f.status for f in s.app.w.followups if f.kind == "watch"}
        assert st == {"F1": "seen", "F2": "open"}
        now = r["checks"][0]["now"]
        assert s.app.w.interactions[now]["checks"] == [{"fid": "F1", "answer": "seen"}]  # 旧格式也留下是哪次记录核对的
        s.app.lock.store.sync_world(s.app.w)
        w2, _ = s.app.lock.store.load_world()
        assert w2.interactions[now]["checks"] == [{"fid": "F1", "answer": "seen"}]  # 重开后还在
    finally:
        s.shutdown(); s.server_close()


def test_followup_from_an_event_is_not_verified_by_a_note_on_the_same_event(tmp_path):
    from people_reading.world import Followup
    s = _reading_server(tmp_path, [READ_OUT])
    try:
        ck = login(s)
        req(s, "POST", "/say", "text=" + quote(READ_TEXT), headers=ck)
        did = s.app.lock.store.drafts(status="pending")[0]["id"]
        iid = "INT-main-%s" % s.app.lock.store.drafts(status="pending")[0]["message_id"]
        s.app.w.interactions[iid] = {"id": iid, "label": "x", "status": "occurred", "day": 1}
        s.app.w.interactions[iid]["watch"] = [{"fid": "F1", "pid": "F", "signal": "现象", "statement": "解释"}]
        s.app.w.followups.append(Followup("F1", "watch", "F", "when_relevant", "现象"))
        req(s, "POST", "/confirm", f"id={did}&choice=0", headers=ck)  # 观察仍归到同一个互动
        assert s.app.reading["stage"] == "receipt" and not s.app.reading["checks"]
        assert next(f for f in s.app.w.followups if f.id == "F1").status == "open"
    finally:
        s.shutdown(); s.server_close()


def test_invalid_citation_does_not_claim_the_dedupe_slot():
    from people_reading.llm import ScriptedModel
    from people_reading.skillset import read_event
    text = "老板 F 突然跟我要 A 和 B 的合作"
    ok = lambda s, e, g: {"statement": s, "evidence": e, "signal": g}  # noqa: E731
    items = [ok("她在找素材", "模型改写的依据", "之后是否引用"), ok("她在找素材", "突然跟我要", "之后是否引用"), ok("她在试探资源", "A 和 B 的合作", "之后是否追问")]
    got = read_event(ScriptedModel([{"explanations": items}]), text)
    assert got and [x["statement"] for x in got] == ["她在找素材", "她在试探资源"]
