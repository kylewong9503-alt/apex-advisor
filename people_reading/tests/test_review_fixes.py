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


def test_f3_unrelated_contemporaneous_record_does_not_make_a_late_report_countable(tmp_path):
    """会议当天只记了签到，72 小时后才回忆首问内容：结果引用的是晚到的报告，仍是回忆，不进比较。"""
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    w.add_observation("MTG-F-1", ("到场签到",), "behavior", SELF, "F", "到场签到", source_mode="contemporaneous")
    sess.submit_report("c1", T.REPORT, hours_since_event=72)
    sess.answer_premise(T.Q_PREMISE, "都没有")
    sess.confirm_outcome()
    assert w.engine.outcomes[("MTG-F-1", "first_q_topic", 1)].recalled_only is True
    assert next(r for k, r in w.engine.rows.items() if k[2:] == ("H1", "H2")).status == "recall_only"


def _as_confirmed_record(st, o, behavior_key):
    """模拟这条观察是经草稿确认而来的（应用里只有这一条路），并带着它所记录的行为。"""
    st.new_conversation(f"cx-{o.id}", "t") if f"cx-{o.id}" not in [c for c, _, _ in st.conversations()] else None
    mid = st.add_message(f"cx-{o.id}", "user", o.content)
    st.save_draft(f"D9{len(st.drafts()) + 100}", mid, [{"message_id": mid, "start": 0, "end": len(o.content)}],
                  {"behavior_key": behavior_key}, "confirmed", o.id)


def test_f3_explicitly_cited_contemporaneous_observation_makes_the_outcome_countable(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    sess.submit_report("c1", T.REPORT, hours_since_event=72)
    sess.answer_premise(T.Q_PREMISE, "都没有")
    # 第 5 轮：当时记录必须是关于这个行为的，且所选结果的依据原话就在这条当时记录里
    o = w.add_observation("MTG-F-1", (sess.proposal["span"],), "behavior", SELF, "F", "当天记：他先问专家要解决什么问题",
                          source_mode="contemporaneous")
    _as_confirmed_record(st, o, "first_q_topic")
    sess.confirm_outcome(supported_by=(o.id,))  # 你明确指出：这条当时的记录就是结果的依据
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
    html = render_app(w, None, mode="masked", title="保密议题 · 周报", toggle_href="保密议题-周报.html?m=P-保密")
    for leak in ("保密议题", "P-保密", "周报", "愿意打听"):
        assert leak not in html, leak
    assert "data-tab='" in html and "data-tab='保密" not in html and "data-sub='保密" not in html
    assert "href='?mask=off'" in html  # 遮挡下切换链接是常量，不输出调用方给的 URL
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


# ═════════ 第二轮审查（对 4dc1956）：9 条 ═════════
import dataclasses  # noqa: E402

from people_reading.assistant import Assistant  # noqa: E402
from people_reading.skills import SkillRegistry  # noqa: E402


# 1 旧版数据库必须能升级
def test_g1_old_database_without_new_columns_still_opens(tmp_path):
    st = Store(str(tmp_path / "old.db"), PASS)
    st.new_conversation("c", "t"); st.add_message("c", "user", "X 连问三次谁负责")
    w = World(); w.add_person(Person("X", "X"))
    o = w.add_observation("I1", ({"message_id": 1, "start": 0, "end": 3},), "behavior", SELF, "X", "X 连问", summary="概括")
    st.save_person(w.persons["X"]); st.save_observation(o, [{"message_id": 1, "start": 0, "end": 3}], None)
    st.db.execute("ALTER TABLE observation DROP COLUMN summary")  # 模拟上一版建的库：没有 summary 列
    st.db.execute("ALTER TABLE person DROP COLUMN quadrant_auto")
    st.db.commit(); st.close()
    st2 = Store(str(tmp_path / "old.db"), PASS)  # 用新版打开：自动补列
    w2, _ = st2.load_world()
    obs = next(iter(w2.observations.values()))
    assert obs.content == "X 连问" and obs.summary == ""
    o2 = w2.add_observation("I2", ({"message_id": 1, "start": 0, "end": 3},), "behavior", SELF, "X", "再记一条", summary="新概括")
    st2.save_observation(o2, [{"message_id": 1, "start": 0, "end": 3}], None)
    st2.sync_world(w2)
    st2.close()
    assert [o.summary for o in Store(str(tmp_path / "old.db"), PASS).load_world()[0].observations.values()] == ["", "新概括"]


# 2 已确认的更正不能被后续录入回滚
def test_g2_confirmed_correction_is_not_rolled_back_by_later_intake(tmp_path):
    first = extraction(drafts=[D_BEH], clarifying_question="是同一份提案吗？",
                       interaction={"cross_dept": None, "submitter": "我", "responsible_named": None})
    later = extraction(interaction={"cross_dept": True, "submitter": None, "responsible_named": None})
    w, st, it = T.env(tmp_path, [first, later])
    a = Assistant(it, SkillRegistry(st))
    it.handle("c1", TEXT1); it.confirm_all()
    opp = next(iter(w.stat_opps))
    pv = a.handle("c1", "那份提案是M提交的")
    assert pv.kind == "correction_preview"
    a.confirm_correction("c1", pv.preview_id)
    assert w.stat_opps[opp].target_id == "M" and w.engine.opps[opp].facts["submitter"] == "M"
    it.handle("c1", "它是跨部门的")  # 只补充了是否跨部门
    assert w.stat_opps[opp].cross_dept is True  # 补全了
    assert w.stat_opps[opp].target_id == "M" and w.engine.opps[opp].facts["submitter"] == "M"  # 更正没被回滚


# 3 哈希验证必须覆盖实际用于结算的冻结规则
def test_g3_swapped_frozen_rule_fails_hash_and_blocks_settlement():
    e, ra, rb = locked_engine()
    p = e.preds["P"]
    swapped = make_rule("RA", "总是追问", "ask_owner", {"kind": "const_binary", "p": 0.2}, definition=ra.definition)
    try:
        p.frozen_rules["RA"] = swapped  # 若冻结表本身不可变，这里会抛 TypeError，也算修好
    except TypeError:
        return
    assert not e.verify_hash("P")
    e.confirm_outcome("O", "ask_owner", 1, False)
    with pytest.raises(EngineError):
        e.settle_prediction("P", {})


def test_g3_replacing_the_whole_frozen_mapping_is_caught_too():
    e, ra, rb = locked_engine()
    p = e.preds["P"]
    swapped = make_rule("RA", "总是追问", "ask_owner", {"kind": "const_binary", "p": 0.2}, definition=ra.definition)
    p.frozen_rules = {"RA": swapped, "RB": p.frozen_rules["RB"]}
    assert not e.verify_hash("P")
    e.confirm_outcome("O", "ask_owner", 1, False)
    with pytest.raises(EngineError):
        e.settle_prediction("P", {})


# 4 同一行为的两个定义版本必须能分别结算
def test_g4_two_definition_versions_are_settled_separately(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    e = w.engine
    e.define_behavior(dataclasses.replace(S.TOPIC, version=2, no_event_rule="整场没问题 → 无法观察"))
    e.lock_prediction(id="P-v2", person_id="F", matter_id="MT1", behavior_key="first_q_topic", bind_mode="condition_match",
                      conditions={"with": "F"}, rules=[e.rules["H1"], e.rules["H2"]], prob_dist=T.DIST_P, def_version=2)
    both = SettlementSession(it, "MT1", "MTG-F-1", it.model, behavior_key="first_q_topic")
    with pytest.raises(EngineError) as ex:
        both.submit_report("c1", T.REPORT)
    assert "def_version" in str(ex.value)  # 提示里要求的参数必须是真的能传的
    v1 = SettlementSession(it, "MT1", "MTG-F-1", it.model, behavior_key="first_q_topic", def_version=1)
    v1.submit_report("c1", T.REPORT)
    v1.answer_premise(T.Q_PREMISE, "都没有")
    v1.confirm_outcome()
    assert e.preds["P-F1"].st.status == "settled" and e.preds["P-v2"].st.status == "locked"


# 5 标志事件不同、截止点相同的前提必须能分别回答
def test_g5_premises_differing_only_by_anchor_event_can_be_answered_separately(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    mk = lambda anchor: Premise("p", "未展示材料", anchor_event=anchor, cutoff="散会",  # noqa: E731
                                choices=(("成立", "holds"), ("不成立", "violated")))
    e = w.engine
    e.preds.pop("P-F1")
    e.lock_prediction(id="PA", person_id="F", matter_id="MT1", behavior_key="first_q_topic", bind_mode="specific_item",
                      opportunity_id="MTG-F-1", rules=[e.rules["H1"], e.rules["H2"]], premises=(mk("首次提问"),), prob_dist=T.DIST_P)
    e.lock_prediction(id="PB", person_id="F", matter_id="MT1", behavior_key="first_q_topic", bind_mode="condition_match",
                      conditions={"with": "F"}, rules=[e.rules["H1"], e.rules["H2"]], premises=(mk("首次表决"),), prob_dist=T.DIST_P)
    sess.submit_report("c1", T.REPORT)
    qs = sess.premise_questions()
    assert len(qs) == 2 and {q["anchor_event"] for q in qs} == {"首次提问", "首次表决"}
    with pytest.raises(EngineError):
        sess.answer_premise("未展示材料", "成立", cutoff="散会")  # 仍然有歧义
    sess.answer_premise("未展示材料", "成立", cutoff="散会", anchor_event="首次提问")
    sess.answer_premise("未展示材料", "不成立", cutoff="散会", anchor_event="首次表决")
    sess.confirm_outcome()
    assert (e.preds["PA"].st.status, e.preds["PB"].st.status) == ("settled", "void_premise_changed")


# 8 重新形式化失败后，假设与规则必须保持一致
def test_g8_failed_reformalize_leaves_hypothesis_unchanged():
    w = World()
    w.engine.define_behavior(S.ASK)
    w.add_hypothesis("X", "s"); w.formalize("H1", "ask_owner", p_hit=0.8)
    w.engine.add_opportunity("O", "ask_owner", {})
    w.engine.lock_prediction(id="P", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                             opportunity_id="O", rules=[w.engine.rules["H1"]], prob=0.7, settlement_criterion="c")
    with pytest.raises(EngineError):
        w.formalize("H1", "ask_owner", p_hit=0.2)
    h = w.hypotheses["H1"]
    assert h.p_hit == 0.8 and h.formal and w.engine.rules["H1"].value({}) == 0.8


# 9 失效的更正预览不能因为事实改回原值而复活
def test_g9_stale_preview_token_cannot_be_revived_by_reverting_the_value():
    e = Engine(); e.define_behavior(S.ASK)
    e.add_opportunity("O", "ask_owner", {"n": 0})
    stale = e.correction_preview("O", "n", 2)["token"]
    for v in (1, 0):  # 0 → 1 → 0：事实回到原值，但期间发生了两次更正
        tok = e.correction_preview("O", "n", v)["token"]
        e.apply_correction("O", "n", v, confirmed_by_user=True, preview_token=tok)
    assert e.opps["O"].facts["n"] == 0
    with pytest.raises(EngineError):
        e.apply_correction("O", "n", 2, confirmed_by_user=True, preview_token=stale)


# ═════════ 第三轮审查（对 03d6e00）：5 条 ═════════
import hashlib  # noqa: E402
import json  # noqa: E402
import sqlite3  # noqa: E402

from people_reading import crypto  # noqa: E402
from people_reading.engine import _canon  # noqa: E402


# A 更正重算不能接受哈希失配的冻结规则
def test_h1_correction_refuses_tampered_frozen_rules():
    e, ra, rb = locked_engine()
    e.confirm_outcome("O", "ask_owner", 1, False)
    e.settle_prediction("P", {})
    key = ("O", "ask_owner", "RA", "RB")
    assert e.rows[key].status == "counted"
    p = e.preds["P"]
    p.frozen_rules = {"RA": make_rule("RA", "总是追问", "ask_owner", {"kind": "const_binary", "p": 0.2}, definition=ra.definition),
                      "RB": p.frozen_rules["RB"]}
    assert not e.verify_hash("P")
    with pytest.raises(EngineError):
        e.correction_preview("O", "cross_dept", False)
    tok = None
    e._previews.add("x")  # 绕过预览令牌，直接验证重算本身也拒绝
    with pytest.raises(EngineError):
        e.recompute("O", "测试")
    assert e.rows[key].status == "counted"  # 比较行没被篡改后的规则改写


# B supported_by 只能指本场会议的观察，并且依据要进入结果的 source_spans
def test_h2_supported_by_must_belong_to_this_interaction_and_enters_source_spans(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    other = w.add_observation("MTG-OTHER", ("另一场会的签到",), "behavior", SELF, "F", "另一场会的签到", source_mode="contemporaneous")
    sess.submit_report("c1", T.REPORT, hours_since_event=72)
    sess.answer_premise(T.Q_PREMISE, "都没有")
    with pytest.raises(EngineError):
        sess.confirm_outcome(supported_by=(other.id,))  # 别的会议的观察不能充当本场结果的依据
    assert ("MTG-F-1", "first_q_topic", 1) not in w.engine.outcomes  # 失败时什么也没写
    mine = w.add_observation("MTG-F-1", (sess.proposal["span"],), "behavior", SELF, "F", "当天记：他先问专家要解决什么问题",
                             source_mode="contemporaneous")
    _as_confirmed_record(st, mine, "first_q_topic")
    sess.confirm_outcome(supported_by=(mine.id,))
    rec = w.engine.outcomes[("MTG-F-1", "first_q_topic", 1)]
    assert rec.recalled_only is False and sess.proposal["span"] in rec.source_spans  # 证据链里有它


# C 错误口令不能改动数据库
def test_h3_wrong_passphrase_does_not_migrate_or_write(tmp_path):
    p = str(tmp_path / "old.db")
    st = Store(p, PASS)
    st.new_conversation("c", "t")
    st.db.execute("ALTER TABLE observation DROP COLUMN summary")
    st.db.execute("DROP TABLE entity")
    st.db.commit(); st.close()
    with pytest.raises(crypto.WrongPassphrase):
        Store(p, "错的口令")
    db = sqlite3.connect(p)
    cols = {r[1] for r in db.execute("PRAGMA table_info(observation)")}
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    db.close()
    assert "summary" not in cols and "entity" not in tables  # 口令不对：一个字节都没改
    Store(p, PASS).close()  # 口令对了才升级
    db = sqlite3.connect(p)
    assert "summary" in {r[1] for r in db.execute("PRAGMA table_info(observation)")}


# D 旧版保存的预测（rule_defs 每项两个字段）升级后仍能结算
def test_h4_legacy_two_field_rule_defs_still_settle():
    e, ra, rb = locked_engine()
    d = e.to_dict()
    pd = d["preds"][0]
    legacy = [[r[0], r[1]] for r in pd["pre"]["rule_defs"]]  # 旧版：(id, definition)
    pd["pre"]["rule_defs"] = legacy
    pre = pd["pre"]
    content = dict(
        id=pre["id"], person_id=pre["person_id"], matter_id=pre["matter_id"], behavior_key=pre["behavior_key"],
        def_version=pre["def_version"], outcome_type=pre["outcome_type"], frozen_categories=pre["frozen_categories"],
        settlement_criterion=pre["settlement_criterion"], bind_mode=pre["bind_mode"],
        bound_opportunity_id=pre["bound_opportunity_id"], conditions=tuple(tuple(c) for c in pre["conditions"]),
        premises=tuple(), prob=pre["prob"], prob_dist=pre["prob_dist"], baseline_prob=pre["baseline_prob"],
        baseline_dist=pre["baseline_dist"], baseline_note=pre["baseline_note"], threshold=pre["threshold"],
        window=pre["window"], rule_defs=tuple(tuple(r) for r in legacy))
    pre["content_hash"] = hashlib.sha256(_canon(content).encode()).hexdigest()  # 旧版算出的哈希
    pd.pop("frozen")  # 旧版没有冻结表
    e2 = Engine.from_dict(json.loads(json.dumps(d)))
    assert e2.verify_hash("P")
    e2.confirm_outcome("O", "ask_owner", 1, False)
    e2.settle_prediction("P", {})
    assert e2.preds["P"].st.status == "settled" and e2.rows[("O", "ask_owner", "RA", "RB")].status == "counted"


# E 不同前提的标志事件，是否发生要分别判断
def test_h5_each_premise_gets_its_own_anchor_occurrence(tmp_path):
    prop = {"observable": True, "outcome": "其他", "basis_quote": "没有首问", "criterion_used": "其他",
            "anchors": {"首次提问": False, "首次表决": True}}
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), prop])
    mk = lambda anchor: Premise("p", "未展示材料", anchor_event=anchor, cutoff="散会",  # noqa: E731
                                question=f"在{anchor}之前，你展示过材料吗？", cutoff_question=f"到散会前，你展示过材料吗？",
                                choices=(("成立", "holds"), ("不成立", "violated")))
    e = w.engine
    e.preds.pop("P-F1")
    e.lock_prediction(id="PA", person_id="F", matter_id="MT1", behavior_key="first_q_topic", bind_mode="specific_item",
                      opportunity_id="MTG-F-1", rules=[e.rules["H1"], e.rules["H2"]], premises=(mk("首次提问"),), prob_dist=T.DIST_P)
    e.lock_prediction(id="PB", person_id="F", matter_id="MT1", behavior_key="first_q_topic", bind_mode="condition_match",
                      conditions={"with": "F"}, rules=[e.rules["H1"], e.rules["H2"]], premises=(mk("首次表决"),), prob_dist=T.DIST_P)
    sess.submit_report("c1", "没有首问，但发生了表决")
    qs = {q["anchor_event"]: q["question"] for q in sess.premise_questions()}
    assert qs["首次提问"] == "到散会前，你展示过材料吗？"  # 首问没发生 → 按截止点问
    assert qs["首次表决"] == "在首次表决之前，你展示过材料吗？"  # 表决发生了 → 照常问


# ───────── 第 4 轮复审（f0404d8）─────────
def test_r4_advice_checks_numbers_in_settlement_criterion(tmp_path):
    from people_reading import skillset as K
    from people_reading.llm import ScriptedModel
    from people_reading.tests.test_skillset import mk, TEXT
    from people_reading.tests.test_step2 import D_BEH, extraction
    st, it = mk(tmp_path, [extraction(drafts=[D_BEH])])
    oid = it.confirm(it.handle("c1", TEXT).draft_ids[0])
    h = it.world.add_hypothesis("X", "只对跨部门提案追问")
    item = {"do": "写明负责人", "reason": "他在会上追问负责人", "cites": [oid], "depends_on": h.id, "fails_if": "仍追问",
            "prediction": {"text": "不再追问", "settlement_criterion": "负责人被问了 731 次"}}
    assert K.advise(ScriptedModel([{"items": [item]}]), it.world, "X", st) is None


def test_r4_split_keeps_one_message_on_one_side():
    from people_reading.skillset import split
    lbls = [{"id": f"L{i}", "message_id": 1} for i in range(5)] + [{"id": f"M{i}", "message_id": m} for i, m in enumerate((2, 3, 4, 5))]
    train, test = split(lbls)
    assert not ({x["message_id"] for x in train} & {x["message_id"] for x in test})
    assert test and all(x["message_id"] == 5 for x in test)


def test_r4_seven_options_render():
    from people_reading.flow import ask, render_flow
    page = render_flow([ask("q", [(str(i), "", "") for i in range(7)])])
    assert page.count("role='radio'") == 7


def test_r4_prep_caps_judgment_options():
    from people_reading.flow import build_prep_steps
    card = lambda s: {"header": {"statement": s}}  # noqa: E731
    v = {"title": "t", "my_judgments": [card(f"判断{i}") for i in range(6)], "model_alternatives": [card("另一种")]}
    assert len(build_prep_steps(v)[0]["options"]) <= 6


def _cfo_server(tmp_path, outputs):
    import threading
    from people_reading.llm import ScriptedModel
    from people_reading.server import serve
    from people_reading.tests import test_step4 as T4
    w, st, it, pm, mid, iid = T4.c2(tmp_path)
    path = st.path; st.close()
    s = serve(path, "127.0.0.1", 0, model=ScriptedModel(outputs))
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, mid


def test_r4_no_settle_button_without_locked_prediction_and_masked_page_has_no_ids(tmp_path):
    from people_reading.tests import test_step4 as T4
    from people_reading.tests.test_server import login, req
    s, mid = _cfo_server(tmp_path, [T4.PREP_OUT])
    try:
        ck = login(s)
        body = req(s, "GET", "/", headers=ck)[2]
        assert "/settle/start" not in body  # 没有锁定的预测：不出现“会后”
        assert req(s, "POST", "/settle/start", headers=ck)[0] == 409
        req(s, "POST", "/mask", headers=ck)
        body = req(s, "GET", "/", headers=ck)[2]
        assert "matter" not in body and mid not in body.split("<script>")[0].replace("data-fields='{}'", "")
    finally:
        s.shutdown(); s.server_close()


def test_r4_idle_timeout_drops_wizard_state(tmp_path):
    from people_reading.server import App
    from people_reading.store import Store
    from people_reading.tests.test_step2 import PASS
    path = str(tmp_path / "t.db"); Store(path, PASS).close()
    now = [0.0]
    app = App(path, idle_seconds=10, clock=lambda: now[0])
    app.lock.unlock(PASS); app.on_unlock(); app.token = "t"
    app.lk = {"mid": "gone", "hyp": {}}
    now[0] = 100.0
    assert app.authed("t") is False and app.lk is None
    app.lock.unlock(PASS); app.on_unlock()
    assert "<h1>" in app.flow_html()  # 重新登录后能正常打开，不卡在旧向导


def test_r4_same_meeting_unrelated_record_cannot_make_recall_countable(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    sign = w.add_observation("MTG-F-1", ("签到",), "behavior", SELF, "F", "F 九点签到", source_mode="contemporaneous")
    _as_confirmed_record(st, sign, None)  # 同一场会、当时记录，但与被预测的行为无关
    sess.submit_report("c1", T.REPORT, hours_since_event=72)
    sess.answer_premise(T.Q_PREMISE, "都没有")
    with pytest.raises(EngineError):
        sess.confirm_outcome(supported_by=(sign.id,))
    assert ("MTG-F-1", "first_q_topic", 1) not in w.engine.outcomes  # 什么也没写


def test_r4_settlement_precheck_prevents_partial_write(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    sess.submit_report("c1", T.REPORT)
    # 不回答前提就确认结果：应在写入结果之前就失败
    with pytest.raises(EngineError):
        sess.confirm_outcome()
    assert ("MTG-F-1", "first_q_topic", 1) not in w.engine.outcomes
    assert all(p.st.status == "locked" for p in w.engine.preds.values())


def test_r4_correction_with_tampered_rules_writes_nothing():
    from types import MappingProxyType
    e, ra, rb = locked_engine()
    e.confirm_outcome("O", "ask_owner", 1, False)
    e.settle_prediction("P", {})
    pv = e.correction_preview("O", "cross_dept", False)
    p = e.preds["P"]
    fr = dict(p.frozen_rules)
    fr["RA"] = make_rule("RA", "RA", "ask_owner", {"kind": "const_binary", "p": 0.01})
    p.frozen_rules = MappingProxyType(fr)  # 预览之后被篡改
    facts, nrev = dict(e.opps["O"].facts), len(e.revisions)
    with pytest.raises(EngineError):
        e.apply_correction("O", "cross_dept", False, confirmed_by_user=True, preview_token=pv["token"])
    assert e.opps["O"].facts == facts and len(e.revisions) == nrev and not e.opps["O"].corrected


def test_r4_legacy_db_with_rule_redefined_after_lock_settles_with_locked_rule():
    e, ra, rb = locked_engine()
    d = e.to_dict()
    pd = d["preds"][0]
    pd.pop("frozen")  # 旧库没有冻结表
    legacy = [[r[0], r[1]] for r in pd["pre"]["rule_defs"]]
    pd["pre"]["rule_defs"] = legacy
    pre = pd["pre"]
    content = dict(
        id=pre["id"], person_id=pre["person_id"], matter_id=pre["matter_id"], behavior_key=pre["behavior_key"],
        def_version=pre["def_version"], outcome_type=pre["outcome_type"], frozen_categories=pre["frozen_categories"],
        settlement_criterion=pre["settlement_criterion"], bind_mode=pre["bind_mode"],
        bound_opportunity_id=pre["bound_opportunity_id"], conditions=tuple(tuple(c) for c in pre["conditions"]),
        premises=tuple(), prob=pre["prob"], prob_dist=pre["prob_dist"], baseline_prob=pre["baseline_prob"],
        baseline_dist=pre["baseline_dist"], baseline_note=pre["baseline_note"], threshold=pre["threshold"],
        window=pre["window"], rule_defs=tuple(tuple(r) for r in legacy))
    pre["content_hash"] = hashlib.sha256(_canon(content).encode()).hexdigest()
    # 旧版允许锁定后重定义规则：库里存的是改过的规则
    for r in d["rules"]:
        if r["id"] == "RA":
            r["spec"] = {"kind": "const_binary", "p": 0.5}
            r["definition"] = _canon(r["spec"])
    e2 = Engine.from_dict(json.loads(json.dumps(d)))
    assert e2.verify_hash("P")
    assert e2.preds["P"].frozen_rules["RA"].definition == legacy[[x[0] for x in legacy].index("RA")][1]  # 用的是锁定时的规则
    e2.confirm_outcome("O", "ask_owner", 1, False)
    e2.settle_prediction("P", {})
    assert e2.rows[("O", "ask_owner", "RA", "RB")].status == "counted"



# ───────── 第 5 轮复审（0ca6a7e）─────────
def test_r5_contradicting_contemporaneous_record_does_not_make_recall_countable(tmp_path):
    w, st, it, sess = T.shortcut(tmp_path, [T.report_out(), T.proposal()])
    sess.submit_report("c1", T.REPORT, hours_since_event=72)
    sess.answer_premise(T.Q_PREMISE, "都没有")
    mid = st.add_message("c1", "user", "当天记：他先问谁牵头")
    then = w.add_observation("MTG-F-1", ({"message_id": mid, "start": 0, "end": 10},), "behavior", SELF, "F",
                             "当天记：他先问谁牵头", source_mode="contemporaneous")
    _as_confirmed_record(st, then, "first_q_topic")  # 同一行为标签，但内容与所选结果相反
    sess.confirm_outcome(supported_by=(then.id,))
    rec = w.engine.outcomes[("MTG-F-1", "first_q_topic", 1)]
    assert rec.recalled_only is True  # 依据来自晚到的报告：仍是事后回忆
    assert all(r.status != "counted" for r in w.engine.rows.values())


def test_r5_settle_refuses_before_writing_when_other_settled_prediction_is_tampered():
    from types import MappingProxyType
    e, ra, rb = locked_engine()
    e.lock_prediction(id="Q", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="condition_match",
                      conditions={"submitter": "S"}, rules=[ra, rb], prob=0.6, settlement_criterion="c")
    e.confirm_outcome("O", "ask_owner", 1, False)
    e.settle_prediction("P", {})
    p = e.preds["P"]
    fr = dict(p.frozen_rules)
    fr["RA"] = make_rule("RA", "RA", "ask_owner", {"kind": "const_binary", "p": 0.01})
    p.frozen_rules = MappingProxyType(fr)
    with pytest.raises(EngineError):
        e.settle_prediction("Q", {}, opp_id="O")
    assert e.preds["Q"].st.status == "locked"  # 没有被标为 settled，之后修好还能正常结算
