"""验收用例的数据构造：验收 A（E1–E4）与验收 D（C4、C5 系列）。"""
from __future__ import annotations

from .engine import BehaviorDef, Engine, Premise, Rule

# ───────── 验收 A：ask_owner（二元） ─────────
ASK = BehaviorDef("ask_owner", 1, "binary", "X 对该提案追问负责人", event_label="追问负责人")


def _p(f, key, test):
    return None if f.get(key) is None else (0.8 if test(f[key]) else 0.2)


H_A = Rule("H_A", "X 一直追问，与情境无关", "ask_owner", "binary", "p_hit=p_miss=0.8", lambda f: 0.8)
H_B = Rule("H_B", "X 只对跨部门提案追问", "ask_owner", "binary", "cross_dept→0.8 否则0.2",
           lambda f: _p(f, "cross_dept", bool))
H_C = Rule("H_C", "X 只对我提交的提案追问", "ask_owner", "binary", "submitter=user→0.8 否则0.2",
           lambda f: _p(f, "submitter", lambda s: s == "user"))
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
H1 = Rule("H1", "先问职责", "first_q_topic", "categorical", "职责0.70/专家需求0.15/其他0.15",
          lambda f: {"职责": 0.70, "专家需求": 0.15, "其他": 0.15})
H2 = Rule("H2", "先确认专家要解决的问题", "first_q_topic", "categorical", "职责0.20/专家需求0.60/其他0.20",
          lambda f: {"职责": 0.20, "专家需求": 0.60, "其他": 0.20})

PREMISE_F1 = Premise(
    "no_show", "F 提出第一个实质性问题之前，我没有主动展示或讲解分工说明",
    anchor_event="F 的第一个实质性问题", cutoff="若全程没有实质性提问，以会议结束为截止")


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
