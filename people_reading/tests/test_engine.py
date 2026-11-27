import dataclasses

import pytest

from people_reading import scenarios as S
from people_reading.cards import FORBIDDEN, prediction_row, settlement_screen
from people_reading.engine import (BehaviorDef, Engine, LockRejected, OutcomeMissing, Premise, PremiseNotAsked,
                                   Rule, SettlementWriteRejected, UnconfirmedCorrection)

ORAL = {"no_show": ("violated", "开场口头讲了分工")}
NONE = {"no_show": ("holds", "都没有")}


def kinds(e, k):
    return [r for r in e.revisions if r.kind == k]


# ───────── 验收 A ─────────
def test_a3_events_and_cumulative():
    e = S.build_ask_owner()
    st = {k: (r.status, r.lr) for k, r in e.rows.items()}
    assert st[("E2", "ask_owner", "H_A", "H_B")][0] == "below_threshold"
    assert st[("E2", "ask_owner", "H_A", "H_C")] == ("counted", 4.0)
    assert st[("E2", "ask_owner", "H_B", "H_C")] == ("counted", 4.0)
    assert st[("E4", "ask_owner", "H_A", "H_B")] == ("counted", 4.0)
    assert st[("E4", "ask_owner", "H_A", "H_C")] == ("counted", 4.0)
    assert st[("E4", "ask_owner", "H_B", "H_C")][0] == "below_threshold"
    assert not [k for k in e.rows if k[0] == "E3"]  # P-1 作废，无比较
    assert e.preds["P-1"].st.status == "void_premise_changed" and kinds(e, "void")
    assert (e.preds["P-E2"].st.brier, e.preds["P-E2"].st.brier_baseline) == (0.49, 0.25)
    assert (e.preds["P-2"].st.brier, e.preds["P-2"].st.brier_baseline) == (0.1225, 0.25)
    assert e.cumulative("H_A", "H_B") == {"multiple": 4.0, "n": 1, "opportunities": ["E4"]}
    assert e.cumulative("H_A", "H_C")["multiple"] == 16.0 and e.cumulative("H_A", "H_C")["n"] == 2
    assert e.cumulative("H_B", "H_C")["multiple"] == 4.0 and e.cumulative("H_B", "H_C")["n"] == 1
    assert len(kinds(e, "pair_update")) == 4 and len(kinds(e, "no_discrimination")) == 2
    cx = {(r.subject) for r in kinds(e, "counterexample")}
    assert cx == {"H_A@E2", "H_B@E2", "H_A@E4"}
    cal = e.calibration()["binary"]
    assert (cal["locked"], cal["settled"], cal["void"], cal["unobservable"]) == (3, 2, 1, 0)
    assert cal["mean_brier"] == 0.30625 and cal["mean_baseline"] == 0.25


def test_a5_1_idempotent_settlement():
    e = S.build_ask_owner()
    before = (e.fingerprint(), len(e.revisions), e.calibration())
    e.settle_prediction("P-2", S.HOLDS)
    assert (e.fingerprint(), len(e.revisions), e.calibration()) == before


def test_a5_2_immutability():
    e = S.build_ask_owner()
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.preds["P-2"].pre.prob = 0.9
    assert e.verify_hash("P-2")
    with pytest.raises(SettlementWriteRejected):
        e.write_settlement_fields("P-2", brier=0.0)


def test_a5_3_no_second_specific_item():
    e = Engine()
    e.define_behavior(S.ASK)
    e.add_opportunity("E2", "ask_owner", {"cross_dept": True, "submitter": "S"})
    kw = dict(person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
              opportunity_id="E2", rules=S.ABC, prob=0.7, settlement_criterion="c")
    e.lock_prediction(id="a", **kw)
    with pytest.raises(LockRejected):
        e.lock_prediction(id="b", **kw)  # 结算前
    e.confirm_outcome("E2", "ask_owner", 1, False)
    e.settle_prediction("a", {})
    with pytest.raises(LockRejected):
        e.lock_prediction(id="c", **kw)  # 结算后


def _p_c(e):
    e.lock_prediction(id="P-c", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="condition_match",
                      conditions={"cross_dept": True, "responsible_named": False}, rules=S.ABC, prob=0.6,
                      settlement_criterion="c")


@pytest.mark.parametrize("pc_first", [False, True])
def test_a5_4_and_8_order_independent_single_outcome(pc_first):
    e = Engine()
    e.define_behavior(S.ASK)
    e.add_opportunity("E2", "ask_owner", {"cross_dept": True, "submitter": "S", "responsible_named": False})
    e.lock_prediction(id="P-E2", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="E2", rules=S.ABC, prob=0.7, settlement_criterion="c")
    _p_c(e)
    assert e.needs_outcome_confirmation("E2", "ask_owner", 1)
    e.confirm_outcome("E2", "ask_owner", 1, False)
    assert not e.needs_outcome_confirmation("E2", "ask_owner", 1)
    e.confirm_outcome("E2", "ask_owner", 1, False)  # 再确认不产生第二条
    assert len(e.outcomes) == 1 and e.outcome_questions_asked == 1
    order = ["P-c", "P-E2"] if pc_first else ["P-E2", "P-c"]
    for pid in order:
        e.settle_prediction(pid, {}, opp_id="E2")
    row = e.rows[("E2", "ask_owner", "H_B", "H_C")]
    assert row.source_prediction_id == "P-E2" and row.lr == 4.0
    assert e.cumulative("H_B", "H_C")["multiple"] == 4.0
    assert e.preds["P-c"].st.brier is not None
    assert bool(kinds(e, "comparison_changed")) == pc_first  # 仅 P-c 先结算时多一条，数值不变


def test_a5_4_fingerprint_same_both_orders():
    def run(order):
        e = Engine(); e.define_behavior(S.ASK)
        e.add_opportunity("E2", "ask_owner", {"cross_dept": True, "submitter": "S", "responsible_named": False})
        e.lock_prediction(id="P-E2", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                          opportunity_id="E2", rules=S.ABC, prob=0.7, settlement_criterion="c")
        _p_c(e)
        e.confirm_outcome("E2", "ask_owner", 1, False)
        for pid in order:
            e.settle_prediction(pid, {}, opp_id="E2")
        return e.fingerprint()
    assert run(["P-c", "P-E2"]) == run(["P-E2", "P-c"])


def test_a5_5_not_evaluable_when_submitter_unknown():
    e = Engine(); e.define_behavior(S.ASK)
    e.add_opportunity("E9", "ask_owner", {"cross_dept": True, "submitter": None})
    e.lock_prediction(id="P", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="E9", rules=S.ABC, prob=0.7, settlement_criterion="c")
    assert not e.preds["P"].rule_snaps["H_C"].evaluable
    e.confirm_outcome("E9", "ask_owner", 1, False)
    e.settle_prediction("P", {})
    assert e.rows[("E9", "ask_owner", "H_A", "H_C")].status == "not_evaluable"
    assert e.rows[("E9", "ask_owner", "H_B", "H_C")].status == "not_evaluable"
    assert e.rows[("E9", "ask_owner", "H_A", "H_B")].status == "below_threshold"
    assert e.cumulative("H_A", "H_C")["n"] == 0


def test_a5_6_fact_correction():
    e = S.build_ask_owner()
    snap_before = {k: dict(v.__dict__) for k, v in e.preds["P-2"].rule_snaps.items()}
    prev = e.correction_preview("E4", "submitter", "user")
    assert prev["predictions_marked_lock_facts_corrected"][0]["prediction"] == "P-2"
    assert e.cumulative("H_A", "H_C")["multiple"] == 16.0  # 预览不写入
    with pytest.raises(UnconfirmedCorrection):
        e.apply_correction("E4", "submitter", "user", confirmed_by_user=False)
    S.correct(e, "E4", "submitter", "user", source_spans=("E4 其实是我提交的",))
    r = {k[2:]: (v.status, v.lr) for k, v in e.rows.items() if k[0] == "E4"}
    assert r[("H_A", "H_B")] == ("counted", 4.0)
    assert r[("H_A", "H_C")][0] == "below_threshold"
    assert r[("H_B", "H_C")] == ("counted", 0.25)  # 即 B 比 C 高 4 倍
    assert e.cumulative("H_A", "H_B")["multiple"] == 4.0
    assert e.cumulative("H_A", "H_C") == {"multiple": 4.0, "n": 1, "opportunities": ["E2"]}
    assert e.cumulative("H_B", "H_C")["multiple"] == 1.0
    p2 = e.preds["P-2"]
    assert p2.st.brier == 0.1225 and p2.st.lock_facts_corrected
    assert {k: dict(v.__dict__) for k, v in p2.rule_snaps.items()} == snap_before  # 锁定快照不改写
    ch = [x for x in kinds(e, "comparison_changed") if x.subject.endswith("@E4")]
    assert len(ch) == 2
    ac = next(x for x in ch if x.subject.startswith("H_A–H_C"))
    assert ac.detail["before"]["status"] == "counted" and ac.detail["before"]["lr"] == 4.0
    assert ac.detail["after"]["status"] == "below_threshold"
    d = e.comparison_detail("E4", "H_A", "H_C")
    assert d["at_lock"] == {"H_A": 0.2, "H_C": 0.8}  # 当时你知道的（提交人=丙）
    assert d["before_correction"]["lr"] == 4.0 and d["current"]["status"] == "below_threshold"
    assert "提交人已更正，比较已重算" in prediction_row(e, "E4", "H_A", "H_B")
    assert e.calibration()["binary"]["mean_brier"] == 0.49  # P-2 默认不计入
    assert e.calibration(include_corrected=True)["binary"]["mean_brier"] == 0.30625


def test_a5_7_recalled_outcome_is_recall_only():
    e = S.build_ask_owner(with_e3=False)
    e2 = Engine(); e2.define_behavior(S.ASK)
    for opp, facts in (("E2", {"cross_dept": True, "submitter": "S"}), ("E4", {"cross_dept": False, "submitter": "丙"})):
        e2.add_opportunity(opp, "ask_owner", facts)
        e2.lock_prediction(id="P" + opp, person_id="X", matter_id="M", behavior_key="ask_owner",
                           bind_mode="specific_item", opportunity_id=opp, rules=S.ABC, prob=0.7 if opp == "E2" else 0.35,
                           settlement_criterion="c")
        e2.confirm_outcome(opp, "ask_owner", 1, False, recalled_only=(opp == "E4"))
        e2.settle_prediction("P" + opp, {})
    assert {r.status for k, r in e2.rows.items() if k[0] == "E4"} == {"recall_only"}
    assert e2.cumulative("H_A", "H_B")["n"] == 0
    assert e2.cumulative("H_A", "H_C") == {"multiple": 4.0, "n": 1, "opportunities": ["E2"]}
    assert e2.preds["PE4"].st.brier == 0.1225  # Brier 照常
    assert not [r for r in kinds(e2, "counterexample") if r.subject.endswith("@E4")]


# ───────── 验收 D ─────────
def settle_topic(e, outcome, answers, extra=()):
    e.confirm_outcome("MTG-F", "first_q_topic", 1, outcome, source_spans=("原话",))
    for pid in ("P-F1", *extra):
        e.settle_prediction(pid, answers if pid == "P-F1" else {}, opp_id="MTG-F")


def test_c4_categorical_lock_validation():
    with pytest.raises(Exception):
        S.TOPIC.__class__("k", 1, "categorical", "d", categories=("a", "b")).validate()  # 无「其他」/标准
    e = Engine(); e.define_behavior(S.TOPIC)
    e.add_opportunity("M", "first_q_topic", {})
    base = dict(person_id="F", matter_id="m", behavior_key="first_q_topic", bind_mode="specific_item",
                opportunity_id="M", rules=[S.H1, S.H2])
    with pytest.raises(LockRejected):  # R11：有“……之前”的前提必须写截止点
        e.lock_prediction(id="x", premises=(Premise("p", "之前我没有展示", anchor_event="首问"),),
                          prob_dist={"职责": .6, "专家需求": .25, "其他": .15}, **base)
    with pytest.raises(LockRejected):  # 概率和不为 1
        e.lock_prediction(id="y", prob_dist={"职责": .6, "专家需求": .25, "其他": .3}, **base)


def test_c5_main_and_cards():
    e = S.build_cfo_engine()
    settle_topic(e, "专家需求", NONE)
    p = e.preds["P-F1"]
    assert (p.st.status, p.st.brier, round(p.st.brier_baseline, 3)) == ("settled", 0.945, 0.667)
    row = e.rows[("MTG-F", "first_q_topic", "H1", "H2")]
    assert (row.status, row.lr, row.p1, row.p2) == ("counted", 4.0, 0.15, 0.6)
    assert [r.subject for r in kinds(e, "counterexample")] == ["H1@MTG-F"]
    txt = prediction_row(e, "MTG-F", "H1", "H2")
    assert txt == ("这次结果（专家需求）在“先确认专家要解决的问题”下的事前概率，是在“先问职责”下的 4 倍；"
                   "累计 4 倍，来自 1 次有区分力的互动。")


def test_c5b_below_threshold():
    e = S.build_cfo_engine()
    settle_topic(e, "其他", NONE)
    assert (e.preds["P-F1"].st.brier, round(e.preds["P-F1"].st.brier_baseline, 3)) == (1.145, 0.667)
    assert e.rows[("MTG-F", "first_q_topic", "H1", "H2")].status == "below_threshold"
    assert {r.subject for r in kinds(e, "counterexample")} == {"H1@MTG-F", "H2@MTG-F"}
    t = prediction_row(e, "MTG-F", "H1", "H2")
    assert t == "实际首问归入“其他”。两条规则事前分别给这一类 15% 和 20%，相差 5 个百分点，未达到 30 个百分点的比较门槛。"
    assert not any(w in t for w in FORBIDDEN)


def test_c5c_binary_only():
    e = Engine()
    e.define_behavior(BehaviorDef("first_q_responsibility", 1, "binary", "F 首问是否涉及职责", event_label="先问职责"))
    h1 = Rule("H1", "先问职责", "first_q_responsibility", "binary", "0.75", lambda f: 0.75)
    h2 = Rule("H2", "先确认专家要解决的问题", "first_q_responsibility", "binary", "0.25", lambda f: 0.25)
    e.add_opportunity("M", "first_q_responsibility", {})
    e.lock_prediction(id="P-F1", person_id="F", matter_id="m", behavior_key="first_q_responsibility",
                      bind_mode="specific_item", opportunity_id="M", rules=[h1, h2], prob=0.7,
                      settlement_criterion="F 的第一个实质性问题是否涉及谁负责")
    e.confirm_outcome("M", "first_q_responsibility", 1, False)
    e.settle_prediction("P-F1", {})
    row = e.rows[("M", "first_q_responsibility", "H1", "H2")]
    assert row.lr == 3.0
    assert (e.preds["P-F1"].st.brier, e.preds["P-F1"].st.brier_baseline) == (0.49, 0.25)
    t = prediction_row(e, "M", "H1", "H2")
    assert "这次结果（没有先问职责）在“先确认专家要解决的问题”下的事前概率，是在“先问职责”下的 3 倍" in t
    assert "这次只说明没有发生“先问职责”。" in t
    assert "专家需求" not in t.replace("先确认专家要解决的问题", "") and not any(w in t for w in FORBIDDEN)


@pytest.mark.parametrize("answers,status", [
    (NONE, "settled"),
    ({"no_show": ("holds", "都没有；后来才拿出")}, "settled"),
    ({"no_show": ("violated", "展示了材料")}, "void_premise_changed"),
    (ORAL, "void_premise_changed"),
    ({"no_show": ("unclear", "记不清")}, "unobservable"),
])
def test_c5d_premise_matrix(answers, status):
    e = S.build_cfo_engine()
    settle_topic(e, "专家需求", answers)
    assert e.preds["P-F1"].st.status == status
    if status == "settled":
        assert e.rows[("MTG-F", "first_q_topic", "H1", "H2")].lr == 4.0
    else:
        assert not e.rows and e.preds["P-F1"].st.brier is None


def test_c5d_unasked_premise_is_not_assumed():
    e = S.build_cfo_engine()
    e.confirm_outcome("MTG-F", "first_q_topic", 1, "专家需求")
    with pytest.raises(PremiseNotAsked):
        e.settle_prediction("P-F1", {})
    with pytest.raises(OutcomeMissing):
        S.build_cfo_engine().settle_prediction("P-F1", NONE)


@pytest.mark.parametrize("order", [("P-F1", "P-F1b"), ("P-F1b", "P-F1")])
def test_c5f_two_predictions_specific_item_is_source(order):
    e = S.build_cfo_engine(); S.add_p_f1b(e)
    e.confirm_outcome("MTG-F", "first_q_topic", 1, "专家需求")
    for pid in order:
        e.settle_prediction(pid, NONE if pid == "P-F1" else {}, opp_id="MTG-F")
    assert e.outcome_questions_asked == 1 and len(e.outcomes) == 1
    assert e.rows[("MTG-F", "first_q_topic", "H1", "H2")].source_prediction_id == "P-F1"
    assert e.cumulative("H1", "H2")["multiple"] == 4.0
    assert e.preds["P-F1b"].st.brier == 0.78


def _g(order):
    e = S.build_cfo_engine(); S.add_p_f1b(e)
    e.confirm_outcome("MTG-F", "first_q_topic", 1, "专家需求", source_spans=("他先问专家具体要解决哪个问题",))
    for pid in order:
        e.settle_prediction(pid, ORAL if pid == "P-F1" else {}, opp_id="MTG-F")
    return e


@pytest.mark.parametrize("order", [("P-F1", "P-F1b"), ("P-F1b", "P-F1")])
def test_c5g_shared_outcome_different_premises(order):
    e = _g(order)
    assert len(e.outcomes) == 1 and e.outcomes[("MTG-F", "first_q_topic", 1)].outcome == "专家需求"
    f1, f1b = e.preds["P-F1"].st, e.preds["P-F1b"].st
    assert f1.status == "void_premise_changed" and f1.brier is None
    assert f1b.status == "settled" and f1b.brier == 0.78 and round(f1b.brier_baseline, 3) == 0.667
    row = e.rows[("MTG-F", "first_q_topic", "H1", "H2")]
    assert (row.status, row.lr, row.source_prediction_id) == ("counted", 4.0, "P-F1b")
    void = kinds(e, "void")[0]
    assert void.prediction_id == "P-F1" and "开场口头讲了分工" in void.reason
    assert not kinds(e, "comparison_changed")  # P-F1 作废后从未成为来源


def test_c5g_orders_identical():
    assert _g(("P-F1", "P-F1b")).fingerprint() == _g(("P-F1b", "P-F1")).fingerprint()


def test_settlement_screen_shows_nothing_but_four_items():
    s = settlement_screen("争取集团专家支持", "2026-11-27 10:00")
    assert set(s) == {"matter", "meeting_time", "open_question", "input_box"}
    blob = " ".join(map(str, s.values()))
    assert not any(w in blob for w in ("职责", "专家需求", "P-F1", "H1", "H2"))


def test_behavior_definition_frozen_after_lock():
    e = S.build_cfo_engine()
    changed = dataclasses.replace(S.TOPIC, no_event_rule="另一种规则")
    with pytest.raises(Exception):
        e.define_behavior(changed)
    e.define_behavior(dataclasses.replace(S.TOPIC, version=2, no_event_rule="另一种规则"))  # 新版本可以
