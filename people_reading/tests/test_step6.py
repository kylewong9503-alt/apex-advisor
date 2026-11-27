"""第 6 步：观察排序完善、象限自动计算与迁移、commitment 账本。"""
import datetime

import pytest

from people_reading import scenarios as S
from people_reading.classify import closeness, depth, refresh_quadrants, suggest_quadrant
from people_reading.intake import Intake
from people_reading.llm import ScriptedModel
from people_reading.panels import calibration_panel
from people_reading.privacy import Redactor
from people_reading.profile import build_profile, fmt_day, render_html
from people_reading.quadrants import NOTE, NOTE_AUTO, build_quadrants, render_quadrants
from people_reading.ranking import (Scene, discriminating_signals, generate_scenes, pair_power, person_rules,
                                    rank_scenes)
from people_reading.skills import run_competing_explanation
from people_reading.store import Store
from people_reading.world import SELF, Person, WorldError

from . import test_step4 as T
from .test_step2 import PASS, extraction

DAY = datetime.date(2026, 11, 27).toordinal()


# ───────── 观察排序 ─────────
def test_generate_scenes_from_declarative_rules():
    names = sorted(s.name for s in generate_scenes(S.ABC))
    assert names == sorted(["我提交的跨部门提案", "我提交的部门内提案", "他人提交的跨部门提案", "他人提交的部门内提案"])
    cat = generate_scenes([S.H1, S.H2])
    assert [s.name for s in cat] == ["他的下一次相关互动"] and cat[0].facts == {}


def test_pair_power_binary_and_categorical():
    p, d = pair_power(S.H_A, S.H_C, {"submitter": "丁", "cross_dept": True})
    assert (p, d) == (0.6, {"pa": 0.8, "pb": 0.2})
    p, d = pair_power(S.H1, S.H2, {})
    assert p == 0.5 and d["category"] == "职责" and (d["pa"], d["pb"]) == (0.7, 0.2)  # 差得最大的类别
    assert pair_power(S.H_A, S.H_C, {"submitter": None}) is None  # 未知 → 不可比


def test_rank_follows_3_9_and_filters_below_threshold():
    rules, scenes = S.ABC, generate_scenes(S.ABC)
    byname = {s.name: s for s in scenes}
    byname["他人提交的跨部门提案"].kind = "observe"
    byname["我提交的部门内提案"].kind = "observe"
    byname["他人提交的部门内提案"].kind, byname["他人提交的部门内提案"].cost = "ask", 3  # 0.6/3 = 0.2
    out = rank_scenes(scenes, rules)
    assert "我提交的跨部门提案" not in [e["name"] for e in out]  # 区分力 0 → 筛掉
    assert [e["name"] for e in out] == ["他人提交的跨部门提案", "我提交的部门内提案", "他人提交的部门内提案"]
    assert [(e["kind"], e["cost"]) for e in out] == [("观察信号", 0), ("观察信号", 0), ("当面询问", 3)]
    assert [e["power"] for e in out[:2]] == [0.6, 0.6] and out[2]["score"] == 0.2


def test_positive_cost_ordered_by_power_over_cost_after_all_zero_cost():
    a = Scene("甲场景", {"submitter": "user", "cross_dept": False}, "ask", 1)  # 0.6/1
    b = Scene("乙场景", {"submitter": "someone_else", "cross_dept": True}, "ask", 4)  # 0.6/4
    c = Scene("丙场景", {"submitter": "someone_else", "cross_dept": False}, "observe")  # 0.6, 代价 0
    d = Scene("丁场景", {"submitter": "someone_else", "cross_dept": True})  # 没评估
    out = rank_scenes([b, d, a, c], S.ABC)
    assert [e["name"] for e in out] == ["丙场景", "甲场景", "乙场景", "丁场景"]
    assert [e["status"] for e in out] == ["ranked", "ranked", "ranked", "unrated"]  # 未评估的单列在最后，不参与排序


def test_each_entry_says_what_to_see_and_which_two_explanations():
    out = rank_scenes([Scene("某场景", {"submitter": "someone_else", "cross_dept": True}, "observe")], S.ABC)
    t = out[0]["text"]
    assert t.startswith("看到：某场景") and "能分清“" in t and "相差 60%" in t
    assert set(out[0]["separates"]) <= {r.name for r in S.ABC}
    cat = rank_scenes([Scene("下一次会谈", {}, "observe")], [S.H1, S.H2])[0]["text"]
    assert "是否落在“职责”" in cat and "给这一类 70%" in cat and "给 20%" in cat


def test_rate_scene_rules_and_signals_use_ratings():
    w = S.build_world_a()
    with pytest.raises(WorldError):
        w.rate_scene("X", "他人提交的部门内提案", "ask", 0)  # 当面询问必须由你评一个正代价
    with pytest.raises(WorldError):
        w.rate_scene("X", "x", "maybe", 1)
    assert all(e["status"] == "unrated" for e in discriminating_signals(w, "X"))
    w.rate_scene("X", "他人提交的跨部门提案", "observe", 99)  # observe 的代价恒为 0
    w.rate_scene("X", "他人提交的部门内提案", "ask", 2)
    sig = discriminating_signals(w, "X")
    assert [(e["name"], e["kind"], e["cost"]) for e in sig if e["status"] == "ranked"] == [
        ("他人提交的跨部门提案", "观察信号", 0), ("他人提交的部门内提案", "当面询问", 2)]
    res = run_competing_explanation(w, "X", "q")
    assert res["signals"][0]["kind"] == "观察信号" and res["signals"][0]["text"].startswith("看到：他人提交的跨部门提案")
    assert any(s["kind"] == "待评估代价" for s in res["signals"]) and "待你评估代价" in res["text"]


def test_ratings_survive_reopen(tmp_path):
    w = S.build_world_a()
    w.rate_scene("X", "他人提交的跨部门提案", "observe")
    st = Store(str(tmp_path / "r.db"), PASS)
    st.snapshot_world(w)
    st.close()
    w2, _ = Store(str(tmp_path / "r.db"), PASS).load_world()
    assert w2.scene_ratings == {("X", "他人提交的跨部门提案"): ("observe", 0)}
    assert [e["name"] for e in discriminating_signals(w2, "X") if e["status"] == "ranked"] == ["他人提交的跨部门提案"]


def test_ranking_for_cfo_categorical_rules(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [])
    assert {r.id for r in person_rules(w, "F")} == {"H1", "H2"}
    sig = discriminating_signals(w, "F")
    assert len(sig) == 1 and sig[0]["power"] == 0.5 and sig[0]["status"] == "unrated"


# ───────── 象限自动计算 ─────────
def edge(w, to, degree=None, status="confirmed", type_="depends_on"):
    attrs = {"degree": degree} if degree else {}
    return w.add_edge(SELF, to, "interactional", type_, status=status, **attrs)


def test_closeness_uses_only_global_interactional_edges_and_frequency():
    w = S.build_world_a()
    base = closeness(w, "X")
    assert base["n_edges"] == 0 and base["edge_score"] == 0 and base["freq_score"] == 1.0 and base["score"] == 0.5
    # 议题里的临时角色 / 议题边不影响
    w.add_matter("M1", "m"); w.add_matter_role("M1", "X", "approver", status="confirmed")
    w.add_matter_edge("M1", "X", "丁", "influences", status="confirmed")
    assert closeness(w, "X") == base
    # 结构关系、未确认的、已否定的都不算
    w.add_edge(SELF, "X", "structural", "direct_report")
    edge(w, "X", status="proposed"); edge(w, "X", status="refuted")
    assert closeness(w, "X") == base
    edge(w, "X", degree=5)  # 全局互动关系边（依赖程度 5）
    c = closeness(w, "X")
    assert c["n_edges"] == 1 and c["edge_score"] == 0.5 and c["score"] == 0.75


def test_closeness_window_and_undated_interactions():
    w = S.build_world_a()
    assert closeness(w, "X", today=200)["n_recent"] == 0 and closeness(w, "X", today=200)["freq_score"] == 0
    assert closeness(w, "X", today=30)["n_recent"] == 19  # 第 1–30 天内全部 19 次互动


def test_depth_components():
    w = S.build_world_a()
    d = depth(w, "X")
    assert d["effective_evidence"] == 7.0  # 3 次有观察的互动 + 4 条有区分力的比较
    assert d["performance_score"] == 0.0 and d["n_settled"] == 2 and d["score"] == 0.525
    # 事后回忆的观察只算半份
    for o in w.observations.values():
        if o.interaction_id == "E4":
            o.source_mode = "recalled"
    assert depth(w, "X")["effective_evidence"] == 6.5  # E4 那次互动只算半份（1+1+0.5+4 条比较；此处没重算比较行）
    w2 = S.build_world_a()
    w2.correct_fact("E4", "submitter", "user", confirmed_by_user=True)
    assert depth(w2, "X")["n_settled"] == 1  # 因更正而重算的预测不计入表现


def test_depth_performance_rewards_beating_baseline_with_shrinkage(tmp_path):
    w = two_settled_good_predictions()
    d = depth(w, "X")
    assert d["n_settled"] == 2 and 0 < d["performance_score"] < 0.5  # 比基线好，但样本少 → 向 0 收缩
    assert d["performance_score"] == pytest.approx((1 - 0.1225 / 0.25) * 2 / 4, abs=1e-6)


def two_settled_good_predictions():
    w = S.build_world_a(through="E1")
    e = w.engine
    for oid, prob in (("G1", 0.35), ("G2", 0.35)):
        w.add_opportunity(oid, actor="X", target="丙", cross_dept=False, responsible_named=False, day=40)
        e.lock_prediction(id="P-" + oid, person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                          opportunity_id=oid, rules=S.ABC, prob=prob, settlement_criterion="c")
        e.confirm_outcome(oid, "ask_owner", 1, False)
        e.settle_prediction("P-" + oid, {})
    return w


def test_suggest_and_refresh_respect_manual_priority():
    w = S.build_world_a()
    sug = suggest_quadrant(w, "X")
    assert sug.quadrant == "近-深" and "有效证据 7.0" in sug.explain() and "门槛 0.4" in sug.explain()
    w.set_quadrant("X", "远-浅")
    ch = refresh_quadrants(w)
    assert w.persons["X"].quadrant_auto == "近-深" and w.persons["X"].quadrant == "远-浅"  # 手动优先
    assert not [c for c in ch if c["person"] == "X"]
    q = build_quadrants(w, auto=True)
    x = next(c for c in q["cards"]["远-浅"] if c["id"] == "X")
    assert x["source"] == "manual" and x["suggestion"] == "近-深" and q["note"] == NOTE_AUTO
    assert "系统建议：近-深" in render_quadrants(q)
    assert build_quadrants(w)["note"] == NOTE  # 默认仍是 MVP 口径
    others = [c for qq in q["cards"].values() for c in qq if c["id"] != "X"]
    assert others and all(c["source"] == "auto" for c in others) and "（自动）" in render_quadrants(q)


def test_auto_cards_are_tagged_and_unclassified_get_a_place():
    w = S.build_world_a()
    refresh_quadrants(w)
    q = build_quadrants(w, auto=True)
    assert not q["cards"]["未分类"] or all(c["id"] not in ("X",) for c in q["cards"]["未分类"])
    x = next(c for qq in q["cards"].values() for c in qq if c["id"] == "X")
    assert x["source"] == "auto" and "（自动）" in render_quadrants(q)


def test_quadrant_auto_persisted(tmp_path):
    w = S.build_world_a()
    refresh_quadrants(w)
    st = Store(str(tmp_path / "q.db"), PASS)
    st.snapshot_world(w)
    st.close()
    w2, _ = Store(str(tmp_path / "q.db"), PASS).load_world()
    assert w2.persons["X"].quadrant_auto == "近-深" and w2.persons["X"].quadrant_source == "auto"


# ───────── 迁移规则 ─────────
def hyps(w):
    a = w.add_hypothesis("F", "他对谁都先问职责", scope="person")
    b = w.add_hypothesis("F", "他对我格外关照", scope="relation")
    c = w.add_hypothesis("F", "已退役的判断", scope="relation"); c.status = "retired"
    return a, b, c


def test_far_to_near_keeps_person_rules_and_flags_relation_rules():
    w = S.build_world_b()
    a, b, c = hyps(w)
    w.set_quadrant("F", "远-浅")
    assert (a.status, b.status) == ("active", "active")
    w.set_quadrant("F", "近-浅")
    assert (a.status, b.status, c.status) == ("active", "needs_review", "retired")  # person 类保留，关系类需复核
    rev = [r for r in w.revisions if r.kind == "quadrant_migration"][-1]
    assert rev.detail["kept_person_rules"] == [a.id] and rev.detail["needs_review"] == [b.id]
    fu = [f for f in w.followups if f.kind == "verify_role"][-1]
    assert fu.text == "关系由远变近：复核 1 条关系类判断" and "F" in fu.target_ref.split(":")


def test_other_directions_only_log():
    w = S.build_world_b()
    a, b, c = hyps(w)
    w.set_quadrant("F", "近-深")
    w.set_quadrant("F", "远-深")  # 近→远：规格只定义了远→近，这里只记修订，不动规则
    w.set_quadrant("F", "远-浅")  # 深浅变化：不迁移
    assert (a.status, b.status) == ("active", "active")
    assert [r.detail["needs_review"] for r in w.revisions if r.kind == "quadrant_migration"] == [[], []]


def test_auto_refresh_triggers_migration_and_card_shows_needs_review(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [])
    w.hypotheses["H1"].scope, w.hypotheses["H2"].scope = "relation", "person"
    w.persons["F"].quadrant_auto = "远-浅"
    w.add_matter("MX", "x")
    for i in range(7):  # 近 30 天里与 F 有 7 次互动 → 关系变近
        w.add_interaction(f"IX{i}", "与 F 的会谈", "occurred", day=100 + i)
        w.add_observation(f"IX{i}", ("原话",), "behavior", SELF, "F", f"第 {i} 次")
    ch = refresh_quadrants(w, today=110)
    assert ch and ch[0]["person"] == "F" and ch[0]["before"] == "远-浅" and ch[0]["after"].startswith("近")
    assert w.hypotheses["H1"].status == "needs_review" and w.hypotheses["H2"].status == "active"
    from people_reading.judgment import judgment_card
    assert judgment_card(w, "H1")["header"]["note"] == "关系已变，需复核"


def test_quadrant_at_lock_is_traceable(tmp_path):
    w, st, it, pm, mid, iid = T.c2(tmp_path)
    pm.prepare(); pm.confirm_categories(); pm.formalize(T.DIST_H)
    w.set_quadrant("F", "近-浅")
    p = pm.lock_prediction(T.DIST_P, S.PREMISE_F1)
    w.set_quadrant("F", "近-深")
    assert p.pre.quadrant_at_lock == "近-浅" and w.persons["F"].quadrant == "近-深"
    assert w.engine.verify_hash(p.id)


# ───────── 承诺账本 ─────────
def obs_for(w, iid="I1", subject="F", text="F 答应下周五前把名单发我"):
    return w.add_observation(iid, (text,), "behavior", SELF, subject, text).id


def test_commitment_requires_original_words_and_valid_parties():
    w = S.build_world_b()
    with pytest.raises(WorldError):
        w.add_commitment("F", SELF, "发名单", evidence_obs_id="O99")  # 没有原话
    o = obs_for(w)
    with pytest.raises(WorldError):
        w.add_commitment("F", "F", "x", evidence_obs_id=o)
    with pytest.raises(WorldError):
        w.add_commitment("Z", SELF, "x", evidence_obs_id=o)
    c = w.add_commitment("F", SELF, "把名单发我", evidence_obs_id=o, due_at=DAY + 5, due_text="下周五")
    assert (c.status, c.from_person_id, c.to_person_id, c.evidence_obs_id) == ("open", "F", SELF, o)


def test_commitment_states_overdue_and_closing_rules():
    w = S.build_world_b()
    w.today_fn = lambda: DAY
    o = obs_for(w)
    c = w.add_commitment("F", SELF, "把名单发我", evidence_obs_id=o, due_at=DAY + 5, due_text="下周五")
    assert w.commitment_state(c) == "open"
    w.today_fn = lambda: DAY + 6
    assert w.commitment_state(c) == "overdue"  # 逾期由到期日与今天推出，不存储
    with pytest.raises(WorldError):
        w.close_commitment(c.id, "fulfilled")  # 没有原话
    with pytest.raises(WorldError):
        w.close_commitment(c.id, "cancelled")  # 取消要写原因
    with pytest.raises(WorldError):
        w.close_commitment(c.id, "done", closing_obs_id=o)
    o2 = obs_for(w, "I2", text="F 把名单发过来了")
    w.close_commitment(c.id, "fulfilled", closing_obs_id=o2)
    assert w.commitment_state(c) == "fulfilled" and c.closing_obs_id == o2
    with pytest.raises(WorldError):
        w.close_commitment(c.id, "broken", closing_obs_id=o2)  # 不能重复关闭
    c2 = w.add_commitment("F", SELF, "再发一份", evidence_obs_id=o)
    w.close_commitment(c2.id, "cancelled", reason="事情取消了")
    assert w.commitment_state(c2) == "cancelled"


def test_commitment_followup_surfaces_on_person_open_and_closes():
    w = S.build_world_b()
    o = obs_for(w)
    c = w.add_commitment("F", SELF, "把名单发我", evidence_obs_id=o, due_text="下周五")
    fu = w.surface_followups(person_id="F")
    assert [f.text for f in fu] == ["承诺到期：把名单发我（下周五）"]
    c2 = w.add_commitment(SELF, "F", "给他一份材料", evidence_obs_id=o, due_text="本周")
    assert [f.text for f in w.surface_followups(person_id="F")] == ["承诺到期：给他一份材料（本周）"]
    w.close_commitment(c.id, "cancelled", reason="取消")
    assert next(f for f in w.followups if f.target_ref.startswith(f"commitment:{c.id}:")).status == "done"


def test_ledger_directions_and_no_scoring():
    w = S.build_world_b()
    w.add_person(Person("G", "G"))
    o = obs_for(w)
    w.add_commitment("F", SELF, "把名单发我", evidence_obs_id=o)
    w.add_commitment(SELF, "F", "给他材料", evidence_obs_id=o)
    w.add_commitment("F", "G", "对接预算", evidence_obs_id=o)
    led = w.commitment_ledger("F")
    assert [r["content"] for r in led["owes_me"]] == ["把名单发我"] and [r["content"] for r in led["i_owe"]] == ["给他材料"]
    assert [r["content"] for r in led["others"]] == ["对接预算"]
    flat = str(led) + str(build_profile(w, "F")["commitments"])
    assert not any(k in flat for k in ("score", "reliab", "守信", "可靠", "信用"))  # 只记录，不打分


def test_profile_shows_commitments_with_clickable_quotes(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [])
    w.today_fn = lambda: DAY + 9
    o = obs_for(w, text="F 答应下周五前把名单发我")
    w.add_commitment("F", SELF, "把名单发我", evidence_obs_id=o, due_at=DAY + 5, due_text="下周五")
    pf = build_profile(w, "F", st)
    r = pf["commitments"]["owes_me"][0]
    assert (r["state"], r["quotes"]) == ("overdue", ["F 答应下周五前把名单发我"])
    page = render_html(pf)
    assert "已逾期" in page and "他欠我" in page and "只记录，不打分" in page and "data-evidence" in page
    masked = render_html(pf, Redactor(w, "masked"))
    assert "把名单发我" not in masked and "下周五" not in masked


C_TEXT = "F 答应下周五前把名单发我，我也答应他周三给材料。"
C_OUT = extraction(commitments=[
    {"from": "F", "to": "我", "content": "把名单发我", "due_text": "下周五前", "source_quote": "F 答应下周五前把名单发我"},
    {"from": "我", "to": "F", "content": "周三给他材料", "due_text": "周三", "source_quote": "我也答应他周三给材料"}])


def test_intake_commitments_are_drafts_then_ledger_with_quotes(tmp_path):
    w, st, it = T.env(tmp_path, [C_OUT])
    r = it.handle("c1", C_TEXT)
    assert r.text == "已记下 · 2 条待确认" and not w.commitments  # 先是草稿
    ids = it.confirm_all()
    assert len(w.commitments) == 2
    c1 = w.commitments[ids[0]]
    assert (c1.from_person_id, c1.to_person_id, c1.content, c1.due_text) == ("F", SELF, "把名单发我", "下周五前")
    ob = next(o for o in w.observations.values() if o.id == c1.evidence_obs_id)
    assert ob.content == "承诺：把名单发我" and st.quote(ob.source_spans[0]) == "F 答应下周五前把名单发我"  # 点回原话
    assert len([f for f in w.followups if f.kind == "watch"]) == 2


def test_intake_edit_due_day_then_overdue(tmp_path):
    w, st, it = T.env(tmp_path, [C_OUT])
    w.today_fn = lambda: DAY + 10
    r = it.handle("c1", C_TEXT)
    cid = it.confirm(r.draft_ids[0], edits={"due_at": DAY + 5})
    assert w.commitment_state(w.commitments[cid]) == "overdue"


def test_intake_update_closes_only_listed_open_commitments(tmp_path):
    upd = extraction(commitment_updates=[{"id": "C1", "status": "fulfilled", "source_quote": "F 把名单发过来了"},
                                         {"id": "C99", "status": "broken", "source_quote": "F 把名单发过来了"}])
    w, st, it = T.env(tmp_path, [C_OUT, upd])
    it.handle("c1", C_TEXT); it.confirm_all()
    r = it.handle("c1", "F 把名单发过来了")
    assert "C1=把名单发我" in it.model.prompts[1] and r.dropped == 1  # 提示里列出未关闭的承诺；不存在的编号被丢弃
    assert w.commitments["C1"].status == "open"  # 草稿确认前不变
    it.confirm_all()
    c = w.commitments["C1"]
    assert c.status == "fulfilled"
    closing = next(o for o in w.observations.values() if o.id == c.closing_obs_id)
    assert closing.content == "兑现：把名单发我" and st.quote(closing.source_spans[0]) == "F 把名单发过来了"
    again = extraction(commitment_updates=[{"id": "C1", "status": "broken", "source_quote": "F 把名单发过来了"}])
    it.model.outputs.append(again)
    assert it.handle("c1", "F 把名单发过来了").dropped == 1  # 已关闭的不能再改


def test_commitments_survive_reopen(tmp_path):
    w, st, it = T.env(tmp_path, [C_OUT])
    it.handle("c1", C_TEXT); it.confirm_all()
    st.close()
    w2, _ = Store(str(tmp_path / "s4.db"), PASS).load_world()
    assert [(c.content, c.status) for c in w2.commitments.values()] == [("把名单发我", "open"), ("周三给他材料", "open")]
    assert w2.commitment_ledger("F")["owes_me"][0]["due"] == "下周五前"


def test_schema_commitments():
    from people_reading.schema import validate_extraction as v
    assert v(extraction(commitments=[{"from": "F", "to": "我", "content": "x", "source_quote": "y"}])) == []
    assert v(extraction(commitments=[{"from": "F", "to": "我", "content": "", "source_quote": "y"}]))
    assert v(extraction(commitment_updates=[{"id": "C1", "status": "cancelled", "source_quote": "y"}]))


# ───────── 真实日期显示 ─────────
def test_interactions_created_from_observations_get_real_dates(tmp_path):
    w, st, it = T.env(tmp_path, [C_OUT])
    w.today_fn = lambda: DAY
    it.handle("c1", C_TEXT); it.confirm_all()
    day = next(iter(w.interactions.values()))["day"]
    assert day == DAY and fmt_day(day) == "2026-11-27" and fmt_day(0) == "日期未知" and fmt_day(12) == "第 12 天"
    assert any("2026-11-27" in t["day_label"] for t in build_profile(w, "F", st)["timeline"])
