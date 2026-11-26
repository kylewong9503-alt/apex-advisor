"""第 4 步：会前与会后闭环（第 10 节 C1–C6、C5-b…g）。"""
import json
import shutil
import subprocess

import pytest

from people_reading import scenarios as S
from people_reading.assistant import Assistant
from people_reading.engine import BehaviorDef, PremiseNotAsked
from people_reading.graph import build_matter_graph, render_svg
from people_reading.intake import Intake
from people_reading.judgment import ROWS, event_timeline, judgment_card, judgment_timeline
from people_reading.llm import ScriptedModel
from people_reading.premeeting import PREP, PreMeeting, validate_prep
from people_reading.profile import build_profile
from people_reading.settle_flow import SettlementSession, evidence_wording_ok
from people_reading.skills import SkillRegistry
from people_reading.store import Store
from people_reading.workbench import render_matter_page, render_settlement_page, workbench_view
from people_reading.world import SELF

from .test_step2 import PASS, extraction

MATTER = "争取集团专家支持"
C1_TEXT = "明天要和 F 谈，想争取集团专家支持，希望他引荐一次。"
C2_TEXT = "我觉得他会先问职责归属。"
C3_TEXT = "记得他先问的是谁来管"
REPORT = "他先问专家具体要解决哪个问题，后来才问谁牵头。他说可以帮我问问集团那边。"
Q_PREMISE = S.PREMISE_F1.text

PREP_OUT = {
    "fork_question": {"question": "上次你提到专家支持时，他的第一反应是什么？",
                      "branch_a": {"condition": "先问职责", "prep": "准备分工说明"},
                      "branch_b": {"condition": "先问具体困难", "prep": "准备一个需要专家介入的案例"}},
    "items": [{"text": "准备职责分工说明", "prediction_text": "F 会先问职责归属", "settlement_criterion": "F 的第一个实质性问题是否涉及谁负责"},
              {"text": "准备专家介入案例", "prediction_text": None, "settlement_criterion": None}],
    "request_line": {"text": "能不能请你帮我引荐一位集团里负责这类问题的同事？", "reason": "把请求缩小到一个具体的人，便于他答应"},
    "model_hypothesis": {"statement": "先确认专家要解决的问题"},
    "category_draft": {"behavior_key": "first_q_topic", "description": "F 的第一个实质性问题涉及的主题",
                       "categories": ["职责", "专家需求", "其他"],
                       "criteria": {"职责": "第一个实质性问题涉及谁负责、归谁管",
                                    "专家需求": "第一个实质性问题涉及专家要解决什么问题、需要什么专家",
                                    "其他": "以上两者之外（如预算、时间表）"},
                       "no_event_rule": "整场未提出实质性问题 → 归入「其他」",
                       "multi_topic_rule": "第一个实质性问题同时涉及多个类别 → 按句中先出现的主题归类"},
    "premise_warning": "分工说明若在他发问前展示，可能改变他的第一个问题，需要把展示时点写进前提",
}
REVISED = {"items": [{"text": "主备分工说明，备选带一个案例", "prediction_text": None, "settlement_criterion": None}],
           "request_line": PREP_OUT["request_line"], "premise_warning": PREP_OUT["premise_warning"]}
DIST_H = {"H1": {"职责": 0.70, "专家需求": 0.15, "其他": 0.15}, "H2": {"职责": 0.20, "专家需求": 0.60, "其他": 0.20}}
DIST_P = {"职责": 0.60, "专家需求": 0.25, "其他": 0.15}


def relation(**kw):
    base = {"scope": "matter", "matter": MATTER, "from": "F", "to": None, "type": None, "role": "referrer",
            "dimension": None, "source_quote": "希望他引荐一次"}
    base.update(kw)
    return base


C1_OUT = extraction(matter={"title": MATTER, "purpose": "这次会谈希望拿到一次具体引荐"}, planned_meeting={"with": "F", "when": "明天"},
                    relations=[relation()])
C2_OUT = extraction(hypotheses=[{"subject": "F", "statement": "先问职责", "source_quote": "他会先问职责归属"}])
C3_OUT = extraction(drafts=[{"kind": "behavior", "reporter": "我", "subject": "F", "content": "上次他先问谁来管",
                             "is_interpretation": False, "source_quote": "他先问的是谁来管", "behavior_key": None, "occurred": None}])


def report_out(drafts=()):
    return extraction(drafts=list(drafts))


REPORT_DRAFTS = [
    {"kind": "behavior", "reporter": "我", "subject": "F", "content": "F 先问专家具体要解决哪个问题", "is_interpretation": False,
     "source_quote": "他先问专家具体要解决哪个问题", "behavior_key": None, "occurred": None},
    {"kind": "quote", "reporter": "F", "subject": "F", "content": "表示可以帮忙问问集团那边", "is_interpretation": False,
     "source_quote": "可以帮我问问集团那边", "behavior_key": None, "occurred": None}]


def proposal(outcome="专家需求", quote="他先问专家具体要解决哪个问题", observable=True, anchor=True):
    return {"observable": observable, "outcome": outcome if observable else None, "anchor_occurred": anchor,
            "basis_quote": quote, "criterion_used": "第一个实质性问题涉及专家要解决什么问题、需要什么专家"}


REVIEW = {"intervention_outcome": "partial",
          "role_evidence": {"role": "referrer", "statement": "表示愿意打听", "source_quote": "可以帮我问问集团那边"},
          "alt_role": {"role": "other", "statement": "F 可能需要通过他人安排专家"},
          "watch": {"text": "两周内 F 是否给出具体人选"}, "follow_prediction": {"statement": "两周内 F 给出具体人选"}}


def env(tmp_path, outputs, name="s4.db"):
    w = S.build_world_b()
    st = Store(str(tmp_path / name), PASS)
    st.snapshot_world(w)
    it = Intake(st, w, ScriptedModel(list(outputs)))
    it.adopt_world()
    st.new_conversation("c1", "F 会谈")
    return w, st, it


def shortcut(tmp_path, outputs, *, with_b=False, name="s4.db"):
    """跳到 C4 结束时的状态（不走对话），用于 C5 系列变体。"""
    w, st, it = env(tmp_path, outputs, name)
    w.add_matter("MT1", MATTER, "这次会谈希望拿到一次具体引荐")
    eng = w.engine
    eng.define_behavior(S.TOPIC)
    w.add_interaction("MTG-F-1", "与 F 的会谈（明天）", "planned")
    for statement, hid in (("先问职责", "H1"), ("先确认专家要解决的问题", "H2")):
        h = w.add_hypothesis("F", statement, matter_id="MT1", proposed_by="user" if hid == "H1" else "model")
        assert h.id == hid
        w.formalize(hid, "first_q_topic", dist=DIST_H[hid])
    eng.add_opportunity("MTG-F-1", "first_q_topic", {"with": "F"}, "MTG-F-1")
    eng.lock_prediction(id="P-F1", person_id="F", matter_id="MT1", behavior_key="first_q_topic", bind_mode="specific_item",
                        opportunity_id="MTG-F-1", rules=[eng.rules["H1"], eng.rules["H2"]], premises=(S.PREMISE_F1,),
                        prob_dist=DIST_P, baseline_note="无历史，默认均匀分布")
    if with_b:
        eng.lock_prediction(id="P-F1b", person_id="F", matter_id="MT1", behavior_key="first_q_topic",
                            bind_mode="condition_match", conditions={"with": "F"}, rules=[eng.rules["H1"], eng.rules["H2"]],
                            prob_dist={"职责": 0.50, "专家需求": 0.30, "其他": 0.20})
    from people_reading.world import Intervention
    w.interventions["I-1"] = Intervention("I-1", "MTG-F-1", "MT1", "会谈结束时 F 答应引荐一位具体的集团专家",
                                          prepared_items=["分工说明", "专家介入案例"])
    sess = SettlementSession(it, "MT1", "MTG-F-1", it.model, "第二天 · 与 F 的会谈")
    return w, st, it, sess


def settle(sess, report=REPORT, choice="都没有", supplement="", outcome_arg=None):
    sess.submit_report("c1", report)
    sess.answer_premise(Q_PREMISE, choice, supplement)
    return sess.confirm_outcome(outcome_arg)


def row(w, a="H1", b="H2"):
    return next(r for k, r in w.engine.rows.items() if k[2:] == (a, b))


# ───────── C1：会前 · 提出目的 ─────────
def test_c1_goal_creates_matter_planned_meeting_and_role_draft(tmp_path):
    w, st, it = env(tmp_path, [C1_OUT])
    r = it.handle("c1", C1_TEXT)
    assert r.text == "已记下 · 3 条待确认"
    kinds = sorted(st.drafts()[i]["proposed"]["target_table"] for i in range(3))
    assert kinds == ["interaction", "matter", "matter_role"] and not w.matters  # 草稿，确认前不入库
    it.confirm_all()
    m = next(iter(w.matters.values()))
    assert (m.title, m.purpose) == (MATTER, "这次会谈希望拿到一次具体引荐")
    iid, meta = next((i, x) for i, x in w.interactions.items() if x.get("status") == "planned")
    assert meta["with"] == "F"
    role = next(iter(w.roles.values()))
    assert (role.person_id, role.role, role.dimension, role.proposed_by) == ("F", "referrer", None, "user")
    assert not w.edges.get("E1") or w.edges["E1"].kind == "structural"  # 没有生成全局 edge（只有验收 B 的 direct_report）
    v = workbench_view(w, m.id, st)
    assert v["mode"] == "matter" and v["title"] == MATTER and v["purpose"].startswith("这次会谈")


# ───────── C2：两种看法与结果类别 ─────────
def c2(tmp_path, extra=()):
    w, st, it = env(tmp_path, [C1_OUT, C2_OUT, PREP_OUT, *extra])
    it.handle("c1", C1_TEXT); it.confirm_all()
    it.handle("c1", C2_TEXT); it.confirm_all()
    mid = next(iter(w.matters))
    iid = next(i for i, x in w.interactions.items() if x.get("status") == "planned")
    pm = PreMeeting(it, mid, "F", iid, it.model)
    return w, st, it, pm, mid, iid


def test_c2_two_views_and_categorical_suggestion(tmp_path):
    w, st, it, pm, mid, iid = c2(tmp_path)
    h1 = w.hypotheses["H1"]
    assert (h1.statement, h1.formal, h1.proposed_by, h1.formed_at > 0) == ("先问职责", False, "user", True)
    out = pm.prepare()
    h2 = w.hypotheses[out.model_hypothesis_id]
    assert (h2.statement, h2.proposed_by, h2.formal) == ("先确认专家要解决的问题", "model", False)
    assert out.suggest_categorical and out.category_draft.categories == ("职责", "专家需求", "其他")
    beh = pm.confirm_categories()
    assert (beh.key, beh.outcome_type) == ("first_q_topic", "categorical") and "其他" in beh.categories
    pm.formalize(DIST_H)
    assert h1.formal and h2.formal and not w.engine.rows
    for hid in ("H1", "H2"):
        c = judgment_card(w, hid, st)
        assert c["header"]["note"] == "尚无互动支持" and [r["label"] for r in c["rows"]] == list(ROWS)
    assert judgment_card(w, "H1")["header"]["by"] == "你的判断" and judgment_card(w, "H2")["header"]["by"] == "模型提出"
    assert out.buttons(0) == {"record_action": True, "lock_prediction": True}
    assert out.buttons(1) == {"record_action": True, "lock_prediction": False}  # 没有可结算的预测，两个按钮各自独立


def test_prep_validation_rules():
    ok = dict(PREP_OUT)
    assert validate_prep(ok) == []
    bad_cat = {**ok, "category_draft": {**ok["category_draft"], "categories": ["职责", "专家需求"]}}
    assert any("其他" in e for e in validate_prep(bad_cat))
    no_crit = {**ok, "category_draft": {**ok["category_draft"], "criteria": {"职责": "x"}}}
    assert validate_prep(no_crit)
    no_rule = {**ok, "category_draft": {k: v for k, v in ok["category_draft"].items() if k != "multi_topic_rule"}}
    assert validate_prep(no_rule)
    no_sc = {**ok, "items": [{"text": "x", "prediction_text": "F 会先问职责", "settlement_criterion": None}]}
    assert any("结算标准" in e for e in validate_prep(no_sc))
    assert validate_prep({**ok, "fork_question": {"question": "q"}})


def test_prep_does_not_invent_when_model_unavailable(tmp_path):
    w, st, it, pm, mid, iid = c2(tmp_path)
    pm.model = None
    assert pm.prepare() is None
    pm.model = ScriptedModel(["垃圾", "还是垃圾"])
    assert pm.prepare() is None


# ───────── C3：分叉追问 = 回忆材料 ─────────
def test_c3_recalled_material_not_counted(tmp_path):
    w, st, it, pm, mid, iid = c2(tmp_path, [C3_OUT, {"branch": "A"}, REVISED])
    pm.prepare(); pm.confirm_categories(); pm.formalize(DIST_H)
    res = pm.answer_fork("c1", C3_TEXT)
    o = next(x for x in w.observations.values() if x.id == res["observations"][0])
    assert (o.source_mode, o.prompted_by_model, o.interaction_id) == ("recalled", True, "PREV-F")
    assert res["linked_hypothesis"] == "H1" and w.recall_links == [(o.id, "H1")]
    c = judgment_card(w, "H1", st)
    assert c["rows"][2]["text"] == "今天你回忆：上次他先问谁来管。它支持“先问职责”，未计入比较。"
    assert c["badge"] == "关联 1 次互动 · 其中 1 次为事后回忆" and c["rows"][1]["text"] == "尚无事前预测" or c["rows"][1]["text"] == "已形成规则，等待结果"
    assert not w.engine.rows  # 不产生 pair_evidence
    rv = pm.revise_after_fork(res["branch"], C3_TEXT)
    assert "分工" in rv["items"][0]["text"] and "时点" in pm.prep.premise_warning
    assert judgment_card(w, "H2", st)["header"]["note"] == "尚无互动支持"  # 回忆只支持 H1


def test_c3_unsure_answer_links_nothing(tmp_path):
    w, st, it, pm, mid, iid = c2(tmp_path, [C3_OUT, {"branch": "unsure"}])
    pm.prepare(); pm.confirm_categories(); pm.formalize(DIST_H)
    assert pm.answer_fork("c1", C3_TEXT)["linked_hypothesis"] is None and w.recall_links == []


# ───────── C4：行动与预测分开锁定 ─────────
def test_c4_action_and_prediction_locked_separately(tmp_path):
    w, st, it, pm, mid, iid = c2(tmp_path)
    pm.prepare(); pm.confirm_categories(); pm.formalize(DIST_H)
    itv = pm.record_action("会谈结束时 F 答应引荐一位具体的集团专家", ["分工说明", "专家介入案例"])
    assert itv.prepared_items == ["分工说明", "专家介入案例"] and itv.execution_log == [] and not w.engine.preds
    p = pm.lock_prediction(DIST_P, S.PREMISE_F1)
    assert (p.pre.bind_mode, p.pre.bound_opportunity_id, p.pre.outcome_type) == ("specific_item", iid, "categorical")
    assert dict(p.pre.baseline_dist) == pytest.approx({"职责": 1 / 3, "专家需求": 1 / 3, "其他": 1 / 3})
    assert p.pre.baseline_note == "无历史，默认均匀分布" and "截止" in p.pre.premises[0].cutoff
    assert set(p.rule_snaps) == {"H1", "H2"} and [c[0] for c in p.pre.settlement_criterion] == ["职责", "专家需求", "其他"]
    assert any(f.kind == "settle_prediction" and f.trigger == "after_meeting" for f in w.followups)
    assert itv.execution_log == []  # “准备了”不影响任何预测
    with pytest.raises(Exception):
        w.engine.lock_prediction(id="dup", person_id="F", matter_id=mid, behavior_key="first_q_topic",
                                 bind_mode="specific_item", opportunity_id=iid, rules=[], prob_dist=DIST_P)


# ───────── C5：会后主动结算（主用例） ─────────
def test_c5_main_flow(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [report_out(REPORT_DRAFTS), proposal(), REVIEW])
    # 首屏：只有四项，判断卡/预测/分叉建议都不可见
    scr = sess.first_screen()
    assert scr == {"matter": MATTER, "meeting_time": "第二天 · 与 F 的会谈", "open_question": "他最先问了什么？", "input_box": True}
    v = workbench_view(w, "MT1", st, session=sess)
    assert v["mode"] == "settlement" and set(v) == {"mode", "screen"}
    sess.submit_report("c1", REPORT)
    assert w.interactions["MTG-F-1"]["status"] == "occurred"
    qs = sess.premise_questions()
    assert len(qs) == 1 and qs[0]["question"] == "在 F 提出第一个实质性问题之前，你有没有展示材料，或口头讲过分工？"
    assert qs[0]["options"] == ["都没有", "展示了材料", "口头讲过", "记不清"]  # 中性选项，不提示正确答案
    sess.answer_premise(Q_PREMISE, "都没有", "分工说明是他问谁牵头时才拿出来的")
    itv = w.interventions["I-1"]
    timing = {e["item"]: e["timing"] for e in itv.execution_log}
    assert timing == {"分工说明": "after_marker", "专家介入案例": "not_before_marker"}  # 只按用户说过的写
    prop = sess.propose_outcome()
    assert (prop["outcome"], prop["basis"], prop["ask_user"]) == ("专家需求", "他先问专家具体要解决哪个问题", True)
    assert "其他" in it.model.prompts[1] and "按句中先出现的主题" in it.model.prompts[1]  # 对照了完整标准
    assert sess.confirm_outcome() == {"P-F1": "settled"}
    p = w.engine.preds["P-F1"]
    assert p.st.premise_check["no_show"]["status"] == "holds"
    assert (p.st.brier, round(p.st.brier_baseline, 3)) == (0.945, 0.667)
    r = row(w)
    assert (r.status, r.lr, r.p1, r.p2) == ("counted", 4.0, 0.15, 0.6)
    assert [x.subject for x in w.engine.revisions if x.kind == "counterexample"] == ["H1@MTG-F-1"]
    rev = sess.review(confirm_intervention="partial")
    # 观察：当时记录 + 经中性结算提醒提交（prompted）
    modes = {(o.source_mode, o.prompted_by_model) for o in w.observations.values() if o.interaction_id == "MTG-F-1"}
    assert modes == {("contemporaneous", True)}
    h1 = next(c for c in rev["cards"] if c["id"] == "H1")
    assert [x["label"] for x in h1["rows"]] == list(ROWS)
    assert "专家需求" in h1["rows"][0]["text"] and h1["rows"][0]["quotes"] == ["他先问专家具体要解决哪个问题"]
    assert h1["rows"][1]["text"] == ("这次结果（专家需求）在“先确认专家要解决的问题”下的事前概率，是在“先问职责”下的 4 倍；"
                                     "累计 4 倍，来自 1 次有区分力的互动。")
    assert w.interventions["I-1"].immediate_outcome == "partial" and rev["intervention"]["confirmed"]
    # 议题角色：will 记 1 条证据，措辞限于原话；can 保持未知；模型另提草稿
    wr = next(x for x in w.roles.values() if x.dimension == "will")
    assert (wr.person_id, wr.role, wr.statement, wr.status, len(wr.evidence_refs)) == ("F", "referrer", "表示愿意打听", "proposed", 1)
    assert not [x for x in w.roles.values() if x.dimension == "can"]
    alt = next(x for x in w.roles.values() if x.proposed_by == "model")
    assert (alt.statement, alt.status) == ("F 可能需要通过他人安排专家", "proposed")
    # 不创建占位人物，不生成任何 matter_edge / 升级
    assert not [p_ for p_ in w.persons.values() if p_.is_placeholder] and not w.matter_edges and not w.upgrade_suggestions()
    assert rev["watch"] == {"text": "两周内 F 是否给出具体人选", "kind": "观察信号", "cost": 0}
    assert rev["suggested_prediction"]["decided_by"] == "user"
    assert [x.kind for x in w.revisions if x.kind == "role_update"]


def test_c5_wording_guard_blocks_overclaims():
    assert evidence_wording_ok("表示愿意打听", "可以帮我问问集团那边")
    for bad in ("主动提出引荐", "已有具体联系人", "能完成引荐"):
        assert not evidence_wording_ok(bad, "可以帮我问问集团那边")
    assert evidence_wording_ok("主动提出引荐", "我主动提出引荐")  # 原话本身这样说时才可写


def test_c5_overclaiming_role_evidence_is_dropped(tmp_path):
    bad = {**REVIEW, "role_evidence": {**REVIEW["role_evidence"], "statement": "主动提出并已有具体联系人"}}
    w, st, it, sess = shortcut(tmp_path, [report_out(REPORT_DRAFTS), proposal(), bad])
    settle(sess)
    rev = sess.review()
    assert rev["role_update"] is None and not [x for x in w.roles.values() if x.dimension == "will"]


# ───────── C5-b：F 先问预算 ─────────
def test_c5b_budget_first(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [report_out(), proposal("其他", "他先问这件事要花多少钱", anchor=True)])
    settle(sess, "他先问这件事要花多少钱")
    p = w.engine.preds["P-F1"]
    assert (p.st.brier, round(p.st.brier_baseline, 3)) == (1.145, 0.667)
    assert row(w).status == "below_threshold"
    assert {x.subject for x in w.engine.revisions if x.kind == "counterexample"} == {"H1@MTG-F-1", "H2@MTG-F-1"}
    assert w.engine.cumulative("H1", "H2")["n"] == 0
    t = judgment_card(w, "H1", st)["rows"][1]["text"]
    assert t == "实际首问归入“其他”。两条规则事前分别给这一类 15% 和 20%，相差 5 个百分点，未达到 30 个百分点的比较门槛。"
    assert not any(x in t for x in ("获胜", "猜中", "都错了", "都没预料到", "证实"))


# ───────── C5-d：前提核对矩阵 ─────────
@pytest.mark.parametrize("choice,supp,status,timing", [
    ("都没有", "", "settled", "not_before_marker"),
    ("都没有", "后来才拿出", "settled", "after_marker"),
    ("展示了材料", "", "void_premise_changed", "before_marker"),
    ("口头讲过", "材料一直在包里，但开场口头解释了职责", "void_premise_changed", "before_marker"),
    ("记不清", "", "unobservable", "unknown"),
])
def test_c5d_premise_matrix(tmp_path, choice, supp, status, timing):
    w, st, it, sess = shortcut(tmp_path, [report_out(), proposal()])
    settle(sess, choice=choice, supplement=supp)
    assert w.engine.preds["P-F1"].st.status == status
    assert {e["timing"] for e in w.interventions["I-1"].execution_log} == {timing}
    if status == "settled":
        assert row(w).lr == 4.0
    else:
        assert not w.engine.rows and w.engine.preds["P-F1"].st.brier is None


def test_c5d_prepared_items_alone_never_assume_premise(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [report_out(), proposal()])
    sess.submit_report("c1", REPORT)
    assert w.interventions["I-1"].prepared_items and w.interventions["I-1"].execution_log == []
    assert sess.premise_questions()  # 必须先问
    with pytest.raises(PremiseNotAsked):
        sess.confirm_outcome()


# ───────── C5-e：归类边界与截止点 ─────────
def test_c5e_no_substantive_question_uses_cutoff_question(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [report_out(), proposal("其他", "F 没有提出实质性问题", anchor=False)])
    sess.submit_report("c1", "F 没有提出实质性问题")
    q = sess.premise_questions()[0]["question"]
    assert q == "到会议结束前，你有没有展示材料，或口头讲过分工？"  # 标志事件没发生 → 按截止点问
    sess.answer_premise(Q_PREMISE, "都没有")
    sess.confirm_outcome()
    assert w.engine.preds["P-F1"].st.status == "settled" and w.engine.outcomes[("MTG-F-1", "first_q_topic", 1)].outcome == "其他"


def test_c5e_no_question_but_explained_midway_is_void(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [report_out(), proposal("其他", "F 没有提出实质性问题", anchor=False)])
    settle(sess, "F 没有提出实质性问题，我中途讲了分工", "口头讲过")
    assert w.engine.preds["P-F1"].st.status == "void_premise_changed"
    assert w.engine.outcomes[("MTG-F-1", "first_q_topic", 1)].outcome == "其他"


def test_c5e_multi_topic_and_unobservable(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [report_out(), proposal("职责", "这件事谁牵头，专家具体解决什么？")])
    settle(sess, "首问：这件事谁牵头，专家具体解决什么？")
    assert w.engine.outcomes[("MTG-F-1", "first_q_topic", 1)].outcome == "职责" and w.engine.preds["P-F1"].st.status == "settled"
    w2, st2, it2, sess2 = shortcut(tmp_path, [report_out(), proposal(observable=False)], name="b.db")
    sess2.submit_report("c1", "我中途离席了，没听到首问")
    sess2.answer_premise(Q_PREMISE, "都没有")
    sess2.confirm_outcome()
    assert w2.engine.outcomes[("MTG-F-1", "first_q_topic", 1)].status == "unobservable"
    assert w2.engine.preds["P-F1"].st.status == "unobservable" and not w2.engine.rows


def test_proposal_without_original_words_is_not_shown(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [report_out(), proposal(quote="编造的依据")])
    sess.submit_report("c1", REPORT)
    p = sess.propose_outcome()
    assert p["manual"] and p["outcome"] is None and p["choices"] == ["职责", "专家需求", "其他"]  # 退回用户手动选择
    sess.answer_premise(Q_PREMISE, "都没有")
    sess.confirm_outcome("专家需求")
    assert w.engine.preds["P-F1"].st.status == "settled"


# ───────── C5-f / C5-g：两份预测共享一个结果 ─────────
def test_c5f_two_predictions_one_outcome(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [report_out(), proposal()], with_b=True)
    settle(sess)
    assert len(w.engine.outcomes) == 1 and w.engine.outcome_questions_asked == 1
    assert w.engine.preds["P-F1"].st.status == "settled" and w.engine.preds["P-F1b"].st.status == "settled"
    assert w.engine.preds["P-F1b"].st.brier == 0.78
    assert row(w).source_prediction_id == "P-F1" and w.engine.cumulative("H1", "H2")["multiple"] == 4.0
    w.engine.lock_prediction(id="P-late", person_id="F", matter_id="MT1", behavior_key="first_q_topic",
                             bind_mode="condition_match", conditions={"with": "F"}, rules=[w.engine.rules["H1"], w.engine.rules["H2"]],
                             prob_dist=DIST_P)
    again = SettlementSession(it, "MT1", "MTG-F-1", it.model)  # 之后又锁了一份：直接复用结果，不再询问
    assert again.propose_outcome() == {"reused": True, "outcome": "专家需求", "ask_user": False}


def test_c5g_shared_outcome_different_premises(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [report_out(), proposal()], with_b=True)
    sess.submit_report("c1", REPORT)
    qs = sess.premise_questions()
    assert len(qs) == 1 and qs[0]["prediction_ids"] == ["P-F1"]  # P-F1b 没有前提，不被询问
    sess.answer_premise(Q_PREMISE, "口头讲过", "开场就口头讲了分工")
    sess.confirm_outcome()
    assert len(w.engine.outcomes) == 1
    assert w.engine.preds["P-F1"].st.status == "void_premise_changed" and w.engine.preds["P-F1"].st.brier is None
    b = w.engine.preds["P-F1b"].st
    assert (b.status, b.brier, round(b.brier_baseline, 3)) == ("settled", 0.78, 0.667)
    r = row(w)
    assert (r.status, r.lr, r.source_prediction_id) == ("counted", 4.0, "P-F1b")
    void = next(x for x in w.engine.revisions if x.kind == "void")
    assert void.prediction_id == "P-F1" and "开场就口头讲了分工" in void.reason


# ───────── C6：档案、关系图、时间线、校准 ─────────
def full_c(tmp_path):
    outs = [C1_OUT, C2_OUT, PREP_OUT, C3_OUT, {"branch": "A"}, REVISED, report_out(REPORT_DRAFTS), proposal(), REVIEW]
    w, st, it, pm, mid, iid = c2(tmp_path)
    it.model.outputs = [PREP_OUT, C3_OUT, {"branch": "A"}, REVISED, report_out(REPORT_DRAFTS), proposal(), REVIEW]
    pm.prepare(); pm.confirm_categories(); pm.formalize(DIST_H)
    pm.answer_fork("c1", C3_TEXT); pm.revise_after_fork("A", C3_TEXT)
    pm.record_action("会谈结束时 F 答应引荐一位具体的集团专家", ["分工说明", "专家介入案例"])
    pm.lock_prediction(DIST_P, S.PREMISE_F1)
    sess = SettlementSession(it, mid, iid, it.model, "第二天")
    sess.submit_report("c1", REPORT)
    sess.answer_premise(Q_PREMISE, "都没有", "分工说明是他问谁牵头时才拿出来的")
    sess.confirm_outcome()
    sess.review(confirm_intervention="partial")
    return w, st, it, mid, iid


def test_c6_profile_graph_timelines_calibration(tmp_path):
    w, st, it, mid, iid = full_c(tmp_path)
    pf = build_profile(w, "F", st)
    assert pf["relation"]["org_path"]["label"] == "团队在其线下"
    roles = pf["matter_roles"][0]["roles"]["引荐人"]
    assert roles["will"].startswith("表示愿意打听") and roles["can"] == "未知"
    # 判断形成时间线 / 事件时间线
    jt = judgment_timeline(w, "H1")
    assert [e["kind"] for e in jt["events"]] == ["formed", "material", "outcome"]
    assert "当时没有相关材料" in jt["events"][0]["text"] and "4 倍" in jt["events"][2]["text"]
    assert "不对记忆可靠性下结论" in jt["note"]
    et = event_timeline(w, "F")
    assert [e["mode"] for e in et] == ["事后回忆", "当时记录"] and et[0]["label"] == "上次会谈"
    assert et[0]["recorded_at"] < et[1]["recorded_at"]
    # 关系小图
    g = build_matter_graph(w, mid, st)
    f = next(n for n in g["nodes"] if n["id"] == "F")
    assert f["label"] == "F · 表示愿意打听 · 渠道未知"
    assert [e for e in g["edges"] if e["from"] == "F"] == []  # 没有 F 指向他人的连线
    me_f = next(e for e in g["edges"] if e["from"] == SELF and e["to"] == "F")
    assert (me_f["label"], me_f["style"], me_f["proposer"]) == ("需其引荐", "dashed", "你")
    assert f["expand"]["basis"] == ["可以帮我问问集团那边"]
    assert f["expand"]["alternatives"][0]["text"] == "F 可能需要通过他人安排专家" and f["expand"]["alternatives"][0]["proposed_by"] == "模型提出"
    assert f["expand"]["signals"] == [{"text": "两周内 F 是否给出具体人选", "kind": "观察信号", "cost": 0}]
    assert "stroke-dasharray" in render_svg(g) and "两周内 F 是否给出具体人选" in render_svg(g)
    # 校准：多类别组单独统计
    cal = w.engine.calibration(matter_id=mid)
    assert set(cal) == {"categorical"} and (cal["categorical"]["locked"], cal["categorical"]["settled"]) == (1, 1)
    assert (cal["categorical"]["mean_brier"], round(cal["categorical"]["mean_baseline"], 3)) == (0.945, 0.667)


def test_c6_reopen_keeps_everything_and_surfaces_followups(tmp_path):
    w, st, it, mid, iid = full_c(tmp_path)
    before = judgment_card(w, "H1", st)
    st.close()
    st2 = Store(str(tmp_path / "s4.db"), PASS)
    w2, _ = st2.load_world()
    assert w2.engine.preds["P-1"].st.status == "settled" and w2.engine.preds["P-1"].st.brier == 0.945
    assert w2.engine.cumulative("H1", "H2")["multiple"] == 4.0 and w2.hypotheses["H1"].formal
    assert w2.interventions["I-1"].immediate_outcome == "partial"
    assert {e["timing"] for e in w2.interventions["I-1"].execution_log} == {"after_marker", "not_before_marker"}
    assert judgment_card(w2, "H1", st2) == before  # 三行判断卡完全一致
    assert w2.recall_links == w.recall_links
    # 再次打开议题：接上 watch 项与后续预测建议
    v = workbench_view(w2, mid, st2)
    assert any("建议锁定预测：两周内 F 给出具体人选" in t for t in v["followups"])
    assert "两周内 F 是否给出具体人选" in v["followups"]  # watch 项也随议题浮出
    assert w2.surface_followups(person_id="F") == [] and w2.surface_followups(matter_id=mid) == []  # 浮出一次后不再重复
    st2.close()
    w3, _ = Store(str(tmp_path / "s4.db"), PASS).load_world()  # 再次重新打开：浮出状态已落库，不重复
    assert w3.surface_followups(person_id="F") == [] and {f.status for f in w3.followups if f.kind in ("watch", "confirm_draft")} == {"surfaced"}


# ───────── 工作台：布局与结算首屏（桌面 + 手机） ─────────
def test_matter_layout_order_and_single_question(tmp_path):
    w, st, it, pm, mid, iid = c2(tmp_path)
    prep = pm.prepare(); pm.confirm_categories(); pm.formalize(DIST_H)
    v = workbench_view(w, mid, st, prep=prep)
    assert v["fork"]["question"] == "上次你提到专家支持时，他的第一反应是什么？" and v["fork"]["unsure_option"] == "记不清"
    assert len(v["fork"]["branches"]) == 2 and v["placeholder"] == "继续说，或随手记一件事……"
    html = render_matter_page(v, build_matter_graph(w, mid, st))
    order = [html.index(x) for x in ("当前目的", "我的判断", "模型提出的另一种可能", "值得问你的一件事", "行动建议", "继续说")]
    assert order == sorted(order) and html.count("值得问你的一件事") == 1
    assert "记为行动" in html and "锁定相关预测" in html and "会上可用的一句话" in html


CANDIDATES = ("职责", "专家需求", "先问职责", "其他", "P-F1", "H1", "H2", "判断", "预测", "分工", "引荐")


@pytest.mark.skipif(not shutil.which("node"), reason="需要 node + playwright 做真实页面检查")
def test_settlement_first_screen_has_no_candidate_answers_desktop_and_mobile(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [])
    page = tmp_path / "settle.html"
    page.write_text(render_settlement_page(sess.first_screen()), encoding="utf-8")
    js = tmp_path / "shot.js"
    js.write_text("""const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();const out={};
for (const [n,w,h] of [['desktop',1280,800],['mobile',390,844]]){const p=await b.newPage({viewport:{width:w,height:h}});
await p.goto('file://'+process.argv[2]);out[n]={text:await p.evaluate(()=>document.body.innerText),
nav:await p.evaluate(()=>document.querySelectorAll('nav,aside,.card').length),
scrollW:await p.evaluate(()=>document.documentElement.scrollWidth)};}
console.log(JSON.stringify(out));await b.close();})();""")
    r = subprocess.run(["node", str(js), str(page)], capture_output=True, text=True, timeout=120)
    if r.returncode != 0 and "Cannot find module" in r.stderr:
        pytest.skip("没有 playwright")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    for name, d in out.items():
        assert MATTER in d["text"] and "他最先问了什么？" in d["text"] and "第二天" in d["text"]
        assert not any(c in d["text"].replace(MATTER, "") for c in CANDIDATES), (name, d["text"])
        assert d["nav"] == 0
    assert out["mobile"]["scrollW"] <= 390  # 手机端不横向滚动
    assert MATTER in page.read_text(encoding="utf-8")


def test_after_first_report_layout_switches_to_matter(tmp_path):
    w, st, it, sess = shortcut(tmp_path, [report_out(), proposal()])
    assert workbench_view(w, "MT1", st, session=sess)["mode"] == "settlement"
    sess.submit_report("c1", REPORT)
    v = workbench_view(w, "MT1", st, session=sess)
    assert v["mode"] == "matter" and v["my_judgments"] and v["model_alternatives"]
