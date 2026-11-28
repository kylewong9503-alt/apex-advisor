"""验收用例的数据构造：验收 A（E1–E4）与验收 D（C4、C5 系列）。"""
from __future__ import annotations

from .engine import BehaviorDef, Engine, Premise, Rule, make_rule

# ───────── 验收 A：ask_owner（二元） ─────────
ASK = BehaviorDef("ask_owner", 1, "binary", "X 对该提案追问负责人", event_label="追问负责人")


H_A = make_rule("H_A", "X 一直追问，与情境无关", "ask_owner", {"kind": "const_binary", "p": 0.8}, "p_hit=p_miss=0.8")
H_B = make_rule("H_B", "X 只对跨部门提案追问", "ask_owner",
                {"kind": "fact_binary", "key": "cross_dept", "test": "is_true", "p_if": 0.8, "p_else": 0.2},
                "cross_dept→0.8 否则0.2")
H_C = make_rule("H_C", "X 只对我提交的提案追问", "ask_owner",
                {"kind": "fact_binary", "key": "submitter", "test": "eq", "equals": "user", "p_if": 0.8, "p_else": 0.2},
                "submitter=user→0.8 否则0.2")
ABC = [H_A, H_B, H_C]
HOLDS = {"resp": ("holds", "")}


def build_ask_owner(with_e3: bool = True) -> Engine:
    """E2、E3、E4（E1 只补录观察与机会计数，不影响比较，本交付不建）。"""
    e = Engine()
    e.define_behavior(ASK)
    resp = Premise("resp", "提案未写明负责人")
    # E2：S 提交，跨部门，未写负责人，S 在场；X 未追问
    e.add_opportunity("E2", "ask_owner", {"cross_dept": True, "submitter": "S", "responsible_named": False}, "E2 S 提交 跨部门")
    e.lock_prediction(id="P-E2", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="E2", rules=ABC, prob=0.7, premises=(resp,),
                      settlement_criterion="会上 X 是否就负责人对该提案追问", baseline_note="默认 0.5")
    e.confirm_outcome("E2", "ask_owner", 1, False, source_spans=("X 没有追问",))
    e.settle_prediction("P-E2", HOLDS)
    if with_e3:  # E3：我提交，部门内；我提前写了负责人 → 前提不成立
        e.add_opportunity("E3", "ask_owner", {"cross_dept": False, "submitter": "user", "responsible_named": False}, "E3 我提交 部门内")
        e.lock_prediction(id="P-1", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                          opportunity_id="E3", rules=ABC, prob=0.35, premises=(resp,),
                          settlement_criterion="会上 X 是否就负责人追问")
        e.confirm_outcome("E3", "ask_owner", 1, False, source_spans=("会议进入资源讨论，X 未追问",))
        e.settle_prediction("P-1", {"resp": ("violated", "我提前写上了负责人")})
    # E4：丙提交，部门内，未写负责人；X 未追问
    e.add_opportunity("E4", "ask_owner", {"cross_dept": False, "submitter": "丙", "responsible_named": False}, "E4 丙提交 部门内")
    e.lock_prediction(id="P-2", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="E4", rules=ABC, prob=0.35, premises=(resp,),
                      settlement_criterion="会上 X 是否就负责人追问")
    e.confirm_outcome("E4", "ask_owner", 1, False, source_spans=("X 没有追问",))
    e.settle_prediction("P-2", HOLDS)
    return e


# ───────── 验收 D：first_q_topic（多类别）与 first_q_responsibility（二元） ─────────
TOPIC = BehaviorDef(
    "first_q_topic", 1, "categorical", "F 的第一个实质性问题涉及的主题",
    categories=("职责", "专家需求", "其他"),
    category_criteria=(("职责", "第一个实质性问题涉及谁负责、归谁管"),
                       ("专家需求", "第一个实质性问题涉及专家要解决什么问题、需要什么专家"),
                       ("其他", "以上两者之外（如预算、时间表）")),
    no_event_rule="整场未提出实质性问题 → 归入「其他」",
    multi_topic_rule="第一个实质性问题同时涉及多个类别 → 按句中先出现的主题归类",
)
H1 = make_rule("H1", "先问职责", "first_q_topic", {"kind": "const_dist", "dist": {"职责": 0.70, "专家需求": 0.15, "其他": 0.15}},
               "职责0.70/专家需求0.15/其他0.15")
H2 = make_rule("H2", "先确认专家要解决的问题", "first_q_topic",
               {"kind": "const_dist", "dist": {"职责": 0.20, "专家需求": 0.60, "其他": 0.20}}, "职责0.20/专家需求0.60/其他0.20")

PREMISE_F1 = Premise(
    "no_show", "F 提出第一个实质性问题之前，我没有主动展示或讲解分工说明",
    anchor_event="F 的第一个实质性问题", cutoff="若全程没有实质性提问，以会议结束为截止",
    question="在 F 提出第一个实质性问题之前，你有没有展示材料，或口头讲过分工？",
    cutoff_question="到会议结束前，你有没有展示材料，或口头讲过分工？",
    choices=(("都没有", "holds"), ("展示了材料", "violated"), ("口头讲过", "violated"), ("记不清", "unclear")))


def build_cfo_engine() -> Engine:
    """C4 结束时的状态：计划中的会谈 + 已锁定的 P-F1。"""
    e = Engine()
    e.define_behavior(TOPIC)
    e.add_opportunity("MTG-F", "first_q_topic", {"with": "F"}, "与 F 的会谈（明天）")
    e.lock_prediction(
        id="P-F1", person_id="F", matter_id="争取集团专家支持", behavior_key="first_q_topic",
        bind_mode="specific_item", opportunity_id="MTG-F", rules=[H1, H2], premises=(PREMISE_F1,),
        prob_dist={"职责": 0.60, "专家需求": 0.25, "其他": 0.15}, baseline_note="无历史，默认均匀分布")
    return e


def add_p_f1b(e: Engine) -> None:
    """C5-g：condition_match，无前提；prob_dist 职责 0.50 / 专家需求 0.30 / 其他 0.20。"""
    e.lock_prediction(
        id="P-F1b", person_id="F", matter_id="争取集团专家支持", behavior_key="first_q_topic",
        bind_mode="condition_match", conditions={"with": "F"}, rules=[H1, H2], premises=(),
        prob_dist={"职责": 0.50, "专家需求": 0.30, "其他": 0.20}, baseline_note="无历史，默认均匀分布")


# ───────── 验收 A（完整数据面）：World = Engine + 人 + 观察 + 机会统计 ─────────
from .world import SELF, Person, World  # noqa: E402

ASK_PREM = Premise("resp", "提案未写明负责人")


def build_world_a(*, through: str = "E4") -> World:
    """A.1 期初 16 次已知机会 + E1–E4。through 可取 'E1'|'E2'|'E3'|'E4'，用于核对 E4 之前的数字。"""
    w = World()
    e = w.engine
    e.define_behavior(ASK)
    w.add_unit("U", "战略组")
    for pid, name, grade in (("X", "X", 8), ("丁", "丁", 5), ("戊", "戊", 5), ("丙", "丙", 5),
                             ("下属", "下属", 4), ("S", "S", 7), ("甲", "甲", 5), ("乙", "乙", 5)):
        w.add_person(Person(pid, name))
        w.add_position(pid, "职员", "U", grade)
    w.add_position(SELF, "战略组成员", "U", 5)
    # (id, 提交人, 跨部门, 是否追问)；E1 是期初数据中“我”的第一次跨部门提案
    initial = [("E1", SELF, True, True), ("I02", SELF, True, True), ("I03", SELF, True, True), ("I04", SELF, True, True),
               ("I05", "丁", True, True), ("I06", "下属", True, False),
               ("I07", SELF, False, True), ("I08", SELF, False, False), ("I09", "戊", False, False)]
    initial += [(f"I{10 + i}", "下属", False, i < 2) for i in range(7)]
    for i, (oid, tgt, cross, asked) in enumerate(initial, start=1):
        w.add_opportunity(oid, actor="X", target=tgt, cross_dept=cross, responsible_named=False, occurred=asked, day=i)
    # E1：补录 O1（亲见三次追问，只计一次发生）、O2/O3（转述）
    w.add_interaction("E1", "E1 我提交的跨部门提案", day=1)
    w.add_observation("E1", ("今天会上，X 连问三次谁负责",), "behavior", SELF, "X", "X 连问三次谁负责")
    w.add_observation("E1", ("散会后甲说，X 很不满",), "hearsay", "甲", "X", "X 很不满", is_interpretation=True)
    w.add_observation("E1", ("乙说 X 一直追问",), "hearsay", "乙", "X", "X 一直追问", is_interpretation=True)
    if through == "E1":
        return w
    # E2
    w.add_opportunity("E2", actor="X", target="S", cross_dept=True, responsible_named=False, day=20)
    e.lock_prediction(id="P-E2", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="E2", rules=ABC, prob=0.7, premises=(ASK_PREM,), settlement_criterion="X 是否就负责人追问")
    w.add_observation("E2", ("S 提交，X 没有追问",), "behavior", SELF, "X", "X 未追问")
    e.confirm_outcome("E2", "ask_owner", 1, False, source_spans=("X 没有追问",))
    e.settle_prediction("P-E2", HOLDS)
    if through == "E2":
        return w
    # E3：我提交部门内；我提前写上了负责人（干预改变了事实，不是更正）
    w.add_opportunity("E3", actor="X", target=SELF, cross_dept=False, responsible_named=False, day=25)
    e.lock_prediction(id="P-1", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="E3", rules=ABC, prob=0.35, premises=(ASK_PREM,), settlement_criterion="X 是否就负责人追问")
    e.confirm_outcome("E3", "ask_owner", 1, False, source_spans=("会议进入资源讨论，X 未追问",))
    e.settle_prediction("P-1", {"resp": ("violated", "我提前写上了负责人")})
    w.stat_opps["E3"].responsible_named = True
    e.opps["E3"].facts["responsible_named"] = True
    if through == "E3":
        return w
    # E4
    w.add_opportunity("E4", actor="X", target="丙", cross_dept=False, responsible_named=False, day=30)
    e.lock_prediction(id="P-2", person_id="X", matter_id="M", behavior_key="ask_owner", bind_mode="specific_item",
                      opportunity_id="E4", rules=ABC, prob=0.35, premises=(ASK_PREM,), settlement_criterion="X 是否就负责人追问")
    w.add_observation("E4", ("丙提交，X 没有追问",), "behavior", SELF, "X", "X 未追问")
    e.confirm_outcome("E4", "ask_owner", 1, False, source_spans=("X 没有追问",))
    e.settle_prediction("P-2", HOLDS)
    return w


# ───────── 验收 B ─────────
def build_world_b() -> World:
    w = World()
    w.add_unit("STRAT", "战略组"); w.add_unit("FIN", "Finance 线"); w.add_unit("CEO", "CEO 办公室")
    w.add_person(Person("F", "F")); w.add_person(Person("M", "M"))
    w.add_position(SELF, "战略组成员", "STRAT", 5)
    w.add_position("M", "战略组负责人", "STRAT", 7, head=True)
    w.add_position("F", "CFO", "FIN", 9, head=True)
    w.add_unit_link("STRAT", "FIN", "solid_report")
    w.add_unit_link("STRAT", "CEO", "support")
    w.add_edge(SELF, "M", "structural", "direct_report")
    return w


def correct(w, opp_id: str, field: str, value, **kw):
    """事实更正：先预览（拿到令牌），再确认写入。w 可以是 World 或 Engine。"""
    eng = getattr(w, "engine", w)
    token = eng.correction_preview(opp_id, field, value)["token"]
    fn = w.correct_fact if hasattr(w, "correct_fact") else w.apply_correction
    return fn(opp_id, field, value, confirmed_by_user=True, preview_token=token, **kw)
