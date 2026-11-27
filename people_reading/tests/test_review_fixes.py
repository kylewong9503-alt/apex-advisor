"""Codex 审查（PR #1）发现的问题：每条一个复现测试，断言的是修复后应有的行为。"""
import inspect

import pytest

from people_reading import scenarios as S
from people_reading.engine import BehaviorDef, Engine, EngineError, LockRejected, Premise, make_rule
from people_reading.intake import Intake
from people_reading.llm import ExternalModelRefused, MissingApiKey, RemoteModel, ScriptedModel
from people_reading.privacy import Redactor
from people_reading.ranking import discriminating_signals
from people_reading.settle_flow import SettlementSession
from people_reading.shell import render_app
from people_reading.store import Store
from people_reading.world import SELF, Person, World, WorldError

from . import test_step4 as T
from .test_step2 import D_BEH, D_HEAR, OUT1, PASS, TEXT1, extraction

ASK_BIN = BehaviorDef("b1", 1, "binary", "行为一", event_label="行为一")


# ───────── 1 锁定的规则不能被事后改动 ─────────
def locked_engine():
    e = Engine()
    e.define_behavior(S.ASK)
    e.add_opportunity("O", "ask_owner", {"cross_dept": True, "submitter": "S"})
    ra = make_rule("RA", "总是追问", "ask_owner", {"kind": "const_binary", "p": 0.8})
    rb = make_rule("RB", "只对跨部门", "ask_owner", {"kind": "fact_binary", "key": "cross_dept", "test": "is_true", "p_if": 0.2, "p_else": 0.2})
    e.lock_prediction(id="P", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="O", rules=[ra, rb], prob=0.7, settlement_criterion="c")
    return e, ra, rb


def test_f1_rule_redefinition_after_lock_is_rejected():
    e, ra, rb = locked_engine()
    with pytest.raises(EngineError):
        e.add_rule(make_rule("RA", "总是追问", "ask_owner", {"kind": "const_binary", "p": 0.2}))
    w = World()
    w.engine.define_behavior(S.ASK)
    w.add_hypothesis("X", "s"); w.formalize("H1", "ask_owner", p_hit=0.8)
    w.engine.add_opportunity("O", "ask_owner", {})
    w.engine.lock_prediction(id="P", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                             opportunity_id="O", rules=[w.engine.rules["H1"]], prob=0.7, settlement_criterion="c")
    with pytest.raises(EngineError):
        w.formalize("H1", "ask_owner", p_hit=0.2)  # 已被预测冻结的规则不能再改


def test_f1_mutating_the_spec_dict_after_lock_does_not_change_settlement():
    e, ra, rb = locked_engine()
    ra.spec["p"] = 0.2  # 调用方手里还握着原来的规格字典
    e.confirm_outcome("O", "ask_owner", 1, False)
    e.settle_prediction("P", {})
    row = e.rows[("O", "ask_owner", "RA", "RB")]
    # 结果是“未追问”：锁定时 RA 给“追问”0.8 → 给这个结果 0.2；若误用后来改的 0.2，则会变成 0.8
    assert row.p1 == 0.2 and row.p2 == 0.8 and row.status == "counted" and row.lr == 4.0
    assert e.verify_hash("P")


def test_f1_hash_covers_rule_spec_not_just_the_label():
    e, ra, rb = locked_engine()
    h = e.preds["P"].pre.content_hash
    e2 = Engine(); e2.define_behavior(S.ASK)
    e2.add_opportunity("O", "ask_owner", {"cross_dept": True, "submitter": "S"})
    ra2 = make_rule("RA", "总是追问", "ask_owner", {"kind": "const_binary", "p": 0.3}, definition=ra.definition)  # 标签相同，规格不同
    e2.lock_prediction(id="P", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                       opportunity_id="O", rules=[ra2, rb], prob=0.7, settlement_criterion="c")
    assert e2.preds["P"].pre.content_hash != h


# ───────── 2 模型不能覆盖已知事实 ─────────
def test_f2_model_cannot_overwrite_known_facts_but_can_fill_unknown(tmp_path):
    first = extraction(drafts=[D_BEH], clarifying_question="是同一份提案吗？",
                       interaction={"cross_dept": True, "submitter": "我", "responsible_named": None})
    second = extraction(interaction={"cross_dept": False, "submitter": "甲", "responsible_named": False})
    w, st, it = T.env(tmp_path, [first, second])
    it.handle("c1", TEXT1); it.confirm_all()
    opp = next(iter(w.stat_opps))
    assert w.stat_opps[opp].cross_dept is True and w.followups and w.followups[0].status == "open"
    r = it.handle("c1", "顺便说一下")
    s = w.stat_opps[opp]
    assert (s.cross_dept, s.target_id) == (True, SELF)  # 已知事实没被改
    assert s.responsible_named is False and w.followups[0].status == "done"  # 未知的被补全
    assert w.engine.opps[opp].facts["cross_dept"] is True
    assert r.question and "要更正吗" in r.question  # 冲突交给用户（走更正流程），不静默覆盖


# ───────── 3 事后回忆不能因为草稿没确认就被当成当时记录 ─────────
def test_f3_late_report_is_recalled_even_when_drafts_unconfirmed(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal(quote="编造的依据")])
    sess.submit_report("c1", T.REPORT, hours_since_event=72)  # 事件 72 小时后才报告
    sess.answer_premise(T.Q_PREMISE, "都没有")
    sess.confirm_outcome("专家需求")  # 手动结算
    rec = w.engine.outcomes[("MTG-F-1", "first_q_topic", 1)]
    assert rec.recalled_only is True
    assert next(r for k, r in w.engine.rows.items() if k[2:] == ("H1", "H2")).status == "recall_only"
    assert w.engine.cumulative("H1", "H2")["n"] == 0


def test_f3_earlier_contemporaneous_record_keeps_outcome_countable(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    w.add_observation("MTG-F-1", ("当天记的",), "behavior", SELF, "F", "当天记的", source_mode="contemporaneous")
    sess.submit_report("c1", T.REPORT, hours_since_event=72)
    sess.answer_premise(T.Q_PREMISE, "都没有")
    sess.confirm_outcome()
    assert w.engine.outcomes[("MTG-F-1", "first_q_topic", 1)].recalled_only is False


# ───────── 4 相同措辞、不同截止点的前提不能共用答案 ─────────
def two_cutoff_premises():
    pa = Premise("p", "我没有展示材料", anchor_event="F 的首问", cutoff="开场后五分钟", choices=(("成立", "holds"), ("不成立", "violated")))
    pb = Premise("p", "我没有展示材料", anchor_event="F 的首问", cutoff="会谈结束", choices=(("成立", "holds"), ("不成立", "violated")))
    return pa, pb


def test_f4_same_text_different_cutoff_are_asked_and_answered_separately(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    pa, pb = two_cutoff_premises()
    w.engine.preds.pop("P-F1")  # 换成两份只差截止点的预测
    e = w.engine
    e.lock_prediction(id="PA", person_id="F", matter_id="MT1", behavior_key="first_q_topic", bind_mode="specific_item",
                      opportunity_id="MTG-F-1", rules=[e.rules["H1"], e.rules["H2"]], premises=(pa,), prob_dist=T.DIST_P)
    e.lock_prediction(id="PB", person_id="F", matter_id="MT1", behavior_key="first_q_topic", bind_mode="condition_match",
                      conditions={"with": "F"}, rules=[e.rules["H1"], e.rules["H2"]], premises=(pb,), prob_dist=T.DIST_P)
    sess.submit_report("c1", T.REPORT)
    qs = sess.premise_questions()
    assert len(qs) == 2 and {q["cutoff"] for q in qs} == {"开场后五分钟", "会谈结束"}
    with pytest.raises(EngineError):
        sess.answer_premise(pa.text, "成立")  # 文字相同、截止点不同：必须指明是哪一个
    sess.answer_premise(pa.text, "成立", cutoff="开场后五分钟")
    sess.answer_premise(pb.text, "不成立", cutoff="会谈结束")
    sess.confirm_outcome()
    assert (e.preds["PA"].st.status, e.preds["PB"].st.status) == ("settled", "void_premise_changed")


# ───────── 5 遮挡页面里不能出现议题 ID / 标题 / 预测 ID ─────────
def test_f5_masked_output_has_no_matter_or_prediction_identifiers():
    w = S.build_world_b()
    w.add_matter("保密议题", "保密议题 周报", "目的")
    w.add_matter_role("保密议题", "F", "referrer", dimension="will", statement="愿意打听")
    e = w.engine
    e.define_behavior(S.ASK)
    e.add_opportunity("O", "ask_owner", {"cross_dept": True, "submitter": "S"})
    ra = make_rule("RA", "总是追问", "ask_owner", {"kind": "const_binary", "p": 0.8})
    rb = make_rule("RB", "只对跨部门", "ask_owner", {"kind": "const_binary", "p": 0.2})
    e.lock_prediction(id="P-保密", person_id="F", matter_id="保密议题", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="O", rules=[ra, rb], prob=0.7, settlement_criterion="c")
    e.confirm_outcome("O", "ask_owner", 1, False)
    e.settle_prediction("P-保密", {})
    html = render_app(w, None, mode="masked", title="保密议题 · 周报", toggle_href="x.html")
    for leak in ("保密议题", "P-保密", "周报", "愿意打听"):
        assert leak not in html, leak
    assert "data-tab='" in html and "data-tab='保密" not in html and "data-sub='保密" not in html
    code_html = render_app(w, None, mode="code", title="保密议题 · 周报")
    assert "保密议题" in code_html and "P-保密" in code_html  # 对照：不遮挡时确实会出现


# ───────── 6 外部模型：放宽隐私策略的入口不能是公开参数 ─────────
def test_f6_remote_model_policy_is_not_caller_configurable(monkeypatch):
    params = set(inspect.signature(RemoteModel.__init__).parameters)
    assert not params & {"api_key", "allowed_hosts", "require_https", "env"}, params
    monkeypatch.delenv("PR_ALLOW_EXTERNAL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-abcdef123456")
    with pytest.raises(ExternalModelRefused):
        RemoteModel()
    monkeypatch.setenv("PR_ALLOW_EXTERNAL", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(MissingApiKey):
        RemoteModel()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-abcdef123456")
    with pytest.raises(ExternalModelRefused):
        RemoteModel(base_url="http://127.0.0.1:9/v1")  # 不是官方 https 入口
    assert RemoteModel().base_url == "https://api.deepseek.com"


# ───────── 7 观察内容必须是原话，不是模型另写的 ─────────
def test_f7_observation_content_is_the_original_quote_model_text_kept_separately(tmp_path):
    bad = {**D_BEH, "content": "X 当众承诺赔偿"}  # 引用了真实片段，但另写了原话没表达的事实
    w, st, it = T.env(tmp_path, [extraction(drafts=[bad])])
    it.handle("c1", TEXT1)
    it.confirm_all()
    o = next(iter(w.observations.values()))
    assert o.content == "X 连问三次谁负责" and "赔偿" not in o.content
    assert o.summary == "X 当众承诺赔偿"  # 模型概括单独存放，标明不是证据


def test_f7_auto_confirm_paths_also_store_the_quote(tmp_path):
    w, st, it, pm, mid, iid = T.c2(tmp_path, [{**T.C3_OUT, "drafts": [{**T.C3_OUT["drafts"][0], "content": "他其实承诺了赔偿"}]}, {"branch": "A"}])
    pm.prepare(); pm.confirm_categories(); pm.formalize(T.DIST_H)
    res = pm.answer_fork("c1", T.C3_TEXT)
    o = next(x for x in w.observations.values() if x.id == res["observations"][0])
    assert o.content == "他先问的是谁来管"


# ───────── 8 同一机会有多种行为时，不能结算一半 ─────────
def test_f8_multiple_behaviors_on_one_opportunity_must_be_settled_per_behavior(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    e = w.engine
    e.define_behavior(ASK_BIN)
    e.add_rule(make_rule("RX", "x", "b1", {"kind": "const_binary", "p": 0.7}))
    e.add_rule(make_rule("RY", "y", "b1", {"kind": "const_binary", "p": 0.2}))
    e.opps["MTG-F-1"].facts["with"] = "F"
    e.lock_prediction(id="P-B", person_id="F", matter_id="MT1", behavior_key="b1", bind_mode="condition_match",
                      conditions={"with": "F"}, rules=[e.rules["RX"], e.rules["RY"]], prob=0.6, settlement_criterion="c")
    n_msgs = len(st.messages("c1"))
    with pytest.raises(EngineError) as ex:
        sess.submit_report("c1", T.REPORT)  # 没指明按哪种行为结算：在这里报错，什么都不保存
    assert "behavior_key" in str(ex.value) and len(st.messages("c1")) == n_msgs
    sess2 = SettlementSession(it, "MT1", "MTG-F-1", it.model, behavior_key="first_q_topic")
    sess2.submit_report("c1", T.REPORT)
    sess2.answer_premise(T.Q_PREMISE, "都没有")
    sess2.confirm_outcome()
    assert e.preds["P-F1"].st.status == "settled" and e.preds["P-B"].st.status == "locked"  # 另一种行为没被动


# ───────── 9 “更正前”只能指事实更正之前 ─────────
def test_f9_source_change_is_not_reported_as_before_correction():
    e = Engine(); e.define_behavior(S.ASK)
    e.add_opportunity("E2", "ask_owner", {"cross_dept": True, "submitter": "S", "responsible_named": False})
    e.lock_prediction(id="P-E2", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="E2", rules=S.ABC, prob=0.7, settlement_criterion="c")
    e.lock_prediction(id="P-c", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="condition_match",
                      conditions={"cross_dept": True}, rules=S.ABC, prob=0.6, settlement_criterion="c")
    e.confirm_outcome("E2", "ask_owner", 1, False)
    e.settle_prediction("P-c", {}, opp_id="E2")
    e.settle_prediction("P-E2", {})
    ch = [r for r in e.revisions if r.kind == "comparison_changed"]
    assert ch and all(r.detail["before"]["source"] == "P-c" and r.detail["after"]["source"] == "P-E2" for r in ch)
    for r in ch:  # 只有来源变了，数值不变
        assert {k: v for k, v in r.detail["before"].items() if k != "source"} == {k: v for k, v in r.detail["after"].items() if k != "source"}
    d = e.comparison_detail("E2", "H_B", "H_C")
    assert d["before_correction"] is None and d["corrected"] is False


# ───────── 10 事实更正必须先预览 ─────────
def test_f10_apply_correction_requires_a_matching_single_use_preview():
    w = S.build_world_a()
    e = w.engine
    with pytest.raises(EngineError):
        e.apply_correction("E4", "submitter", "user", confirmed_by_user=True)  # 没预览
    pv = e.correction_preview("E4", "submitter", "user")
    with pytest.raises(EngineError):
        e.apply_correction("E4", "submitter", "丁", confirmed_by_user=True, preview_token=pv["token"])  # 预览的是另一笔变更
    e.apply_correction("E4", "submitter", "user", confirmed_by_user=True, preview_token=pv["token"])
    with pytest.raises(EngineError):
        e.apply_correction("E4", "submitter", "user", confirmed_by_user=True, preview_token=pv["token"])  # 一次性
    pv2 = e.correction_preview("E2", "submitter", "user")
    e.opps["E2"].facts["cross_dept"] = False  # 预览之后状态变了
    with pytest.raises(EngineError):
        e.apply_correction("E2", "submitter", "user", confirmed_by_user=True, preview_token=pv2["token"])


# ───────── 11 人物合并与别名在重新打开后必须一致 ─────────
def test_f11_merge_and_aliases_survive_reopen(tmp_path):
    w, st, it = T.env(tmp_path, [extraction()])
    it.register_person(Person("G", "高峰"), aliases=("小高",))
    ph = it._person_for("老张", None)
    o = w.add_observation("I1", ("原话",), "behavior", SELF, ph, "老张说了话", involved=(ph,))
    st.save_observation(o, [{"text": "原话"}], None)
    w.merge_person(ph, "G")
    st.sync_world(w)
    st.close()
    st2 = Store(str(tmp_path / "s4.db"), PASS)
    w2, _ = st2.load_world()
    assert next(iter(w2.observations.values())).subject_person_id == "G"  # 合并结果被保存
    it2 = Intake(st2, w2, ScriptedModel([]))
    it2.adopt_world()
    assert it2.resolve("小高") == ("one", "G")  # 登记过的别名
    assert it2.resolve("老张") == ("one", "G")  # 占位人物的名字指向合并后的真实人物


# ───────── 12 批量确认只能确认当前对话的草稿 ─────────
def test_f12_confirm_all_is_scoped_to_the_conversation(tmp_path):
    w, st, it = T.env(tmp_path, [extraction(drafts=[D_BEH]), extraction(drafts=[D_HEAR])])
    st.new_conversation("c2", "另一个")
    it.handle("c1", TEXT1)
    it.handle("c2", TEXT1)
    it.confirm_all("c1")
    assert [d["status"] for d in st.drafts()] == ["confirmed", "pending"] and len(w.observations) == 1


# ───────── 13 同一人有不同类型的规则时，排序不能崩 ─────────
def test_f13_ranking_with_mixed_behaviors_does_not_crash():
    w = S.build_world_a()
    e = w.engine
    e.define_behavior(S.TOPIC)
    e.add_opportunity("MTG", "first_q_topic", {"with": "X"}, "MTG")
    e.lock_prediction(id="P-T", person_id="X", matter_id="M", behavior_key="first_q_topic", bind_mode="specific_item",
                      opportunity_id="MTG", rules=[S.H1, S.H2], prob_dist={"职责": 0.6, "专家需求": 0.25, "其他": 0.15})
    sig = discriminating_signals(w, "X")  # 二元的 H_A/H_B/H_C 与多类别的 H1/H2 同属一个人
    names = {tuple(sorted(e["separates"])) for e in sig}
    assert any(set(n) <= {r.name for r in S.ABC} for n in names) and any(set(n) <= {S.H1.name, S.H2.name} for n in names)


# ───────── 14 极小但非零的概率不能把似然比舍入成 0 ─────────
def test_f14_tiny_but_positive_probability_keeps_a_positive_ratio():
    e = Engine(); e.define_behavior(S.ASK)
    e.add_opportunity("O", "ask_owner", {})
    hi = make_rule("RH", "高", "ask_owner", {"kind": "const_binary", "p": 0.9})
    lo = make_rule("RL", "低", "ask_owner", {"kind": "const_binary", "p": 1e-10})
    e.lock_prediction(id="P", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="O", rules=[hi, lo], prob=0.5, settlement_criterion="c")
    e.confirm_outcome("O", "ask_owner", 1, True)
    e.settle_prediction("P", {})
    row = e.rows[("O", "ask_owner", "RH", "RL")]
    assert row.status == "counted" and 0 < row.lr < 1e-9
    assert e.cumulative("RL", "RH")["multiple"] > 1e9  # 反向累计不再除零
    assert e.cumulative("RH", "RL")["multiple"] > 0


# ───────── 15 概率必须在 [0,1] ─────────
def test_f15_out_of_range_category_probabilities_are_rejected():
    e = Engine(); e.define_behavior(S.TOPIC)
    e.add_opportunity("O", "first_q_topic", {})
    kw = dict(person_id="X", matter_id="M", behavior_key="first_q_topic", bind_mode="specific_item", opportunity_id="O", rules=[])
    with pytest.raises(LockRejected):
        e.lock_prediction(id="a", prob_dist={"职责": 1.2, "专家需求": -0.2, "其他": 0.0}, **kw)
    with pytest.raises(LockRejected):
        e.lock_prediction(id="b", prob_dist={"职责": 0.6, "专家需求": 0.25, "其他": 0.15},
                          baseline_dist={"职责": 1.2, "专家需求": -0.2, "其他": 0.0}, **kw)
    with pytest.raises(LockRejected):
        e.lock_prediction(id="c", prob=0.5, baseline_prob=1.5, **{**kw, "behavior_key": "first_q_topic"})
    for bad in ({"kind": "const_binary", "p": 1.5}, {"kind": "const_dist", "dist": {"a": 1.2, "b": -0.2}},
                {"kind": "fact_binary", "key": "k", "test": "is_true", "p_if": 2, "p_else": 0.1}):
        with pytest.raises(EngineError):
            make_rule("R", "r", "k", bad)


# ───────── 16 条件匹配不能因为 JSON 往返而改变 ─────────
def test_f16_condition_matching_is_stable_across_json_roundtrip():
    e = Engine(); e.define_behavior(S.ASK)
    e.add_opportunity("O", "ask_owner", {"tags": ("a", "b")})
    e.lock_prediction(id="P", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="condition_match",
                      conditions={"tags": ["a", "b"]}, rules=[], prob=0.5, settlement_criterion="c")
    e.confirm_outcome("O", "ask_owner", 1, False)
    e2 = Engine.from_dict(__import__("json").loads(__import__("json").dumps(e.to_dict())))
    for eng in (e, e2):  # 保存前后行为一致：值相同的元组与列表视为同一个条件
        eng.settle_prediction("P", {}, opp_id="O")
        assert eng.preds["P"].st.status == "settled"


# ───────── 17 依赖声明 ─────────
def test_f17_requirements_declare_cryptography():
    import pathlib
    text = (pathlib.Path(__file__).parents[2] / "requirements.txt").read_text()
    assert "cryptography" in text
