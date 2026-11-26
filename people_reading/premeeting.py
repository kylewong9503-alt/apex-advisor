"""会前准备 Skill（第 5 节）与会前流程（C1–C4）。

模型只负责产出“分叉问题、准备项、会上一句话、类别草稿”等内容；程序负责：
  校验（类别互斥穷尽含「其他」且每类有判定标准；有预测的准备项必须附结算标准）、
  行动与预测分开锁定、前提必须写实际顺序与截止点（R11）、锁定后不可改（R4）。
模型不可用或输出无效时不编造内容：返回“无法生成，请手动”。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .engine import BehaviorDef, EngineError, Premise
from .intake import Intake
from .skills import SkillVersion
from .world import Followup, Intervention

PREP = SkillVersion(
    "会前准备", 1, "会前把目的、人物角色、各解释的预测，变成一个分叉问题、具体准备项和会上一句话。",
    applicable_questions=("怎么准备", "会前准备", "怎么跟他谈"),  # 不含“明天要和”：那是在陈述一件事，走录入
    inputs=("议题目的", "相关人物的议题角色（区分 can 与 will）", "各规则在本次条件下的预测", "相关关系边与近期互动"),
    steps=("读取目的与角色", "找出用户判断与模型解释预测不同内容之处", "提出最值得问的一个分叉问题", "给出准备项与会上一句话"),
    output_format="一个分叉问题；具体准备项（记为行动 / 锁定相关预测 各自独立）；一句会上可用的请求及理由",
    allowed_actions=("记为行动", "锁定相关预测", "建议多类别预测"),
)


def _is_str(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _validate_items(d: dict) -> list:
    errs = []
    items = d.get("items")
    if not isinstance(items, list) or not items:
        return ["items 必须是非空数组"]
    for i, it in enumerate(items):
        if not isinstance(it, dict) or not _is_str(it.get("text")):
            errs.append(f"items[{i}].text 必须是非空字符串")
            continue
        if it.get("prediction_text") is not None and not _is_str(it.get("settlement_criterion")):
            errs.append(f"items[{i}] 要锁定预测，必须附结算标准 settlement_criterion")
    rl = d.get("request_line")
    if not (isinstance(rl, dict) and _is_str(rl.get("text")) and _is_str(rl.get("reason"))):
        errs.append("request_line 需要 text 与 reason")
    if d.get("premise_warning") is not None and not _is_str(d["premise_warning"]):
        errs.append("premise_warning 必须是字符串或 null")
    return errs


def _category_draft(cd) -> tuple:
    """返回 (BehaviorDef | None, 错误列表)。互斥穷尽含「其他」、每类有标准、两条归类规则都必须有。"""
    if cd is None:
        return None, []
    try:
        b = BehaviorDef(cd["behavior_key"], 1, "categorical", cd["description"], categories=tuple(cd["categories"]),
                        category_criteria=tuple((c, cd["criteria"][c]) for c in cd["categories"]),
                        no_event_rule=cd["no_event_rule"], multi_topic_rule=cd["multi_topic_rule"])
        b.validate()
        return b, []
    except (KeyError, TypeError, EngineError) as e:
        return None, [f"category_draft 不合格：{e}"]


def validate_prep(d) -> list:
    if not isinstance(d, dict):
        return ["必须是对象"]
    errs = _validate_items(d)
    fq = d.get("fork_question")
    if not (isinstance(fq, dict) and _is_str(fq.get("question")) and all(
            isinstance(fq.get(b), dict) and _is_str(fq[b].get("condition")) and _is_str(fq[b].get("prep")) for b in ("branch_a", "branch_b"))):
        errs.append("fork_question 需要 question、branch_a/branch_b（各含 condition 与 prep）")
    mh = d.get("model_hypothesis")
    if mh is not None and not _is_str(mh.get("statement") if isinstance(mh, dict) else None):
        errs.append("model_hypothesis 需要 statement")
    errs += _category_draft(d.get("category_draft"))[1]
    return errs


def validate_revise(d) -> list:
    return _validate_items(d) if isinstance(d, dict) else ["必须是对象"]


def validate_branch(d) -> list:
    return [] if isinstance(d, dict) and d.get("branch") in ("A", "B", "unsure") else ["branch 必须是 A/B/unsure"]


SYSTEM_PREP = """你是会前准备助手。根据议题目的、人物角色和用户的判断，只输出 JSON：
fork_question: 只问一个最值得问用户的、答案会改变建议的问题 {"question","branch_a":{"condition","prep"},"branch_b":{"condition","prep"}}
items: 具体准备项 [{"text": 行动，如“准备职责分工说明”, "prediction_text": 可锁定的预测，如“F 会先问职责归属”或 null, "settlement_criterion": 该预测如何判定结果(有 prediction_text 时必填)}]
request_line: 一句会上可用的请求 {"text","reason"}
model_hypothesis: 你提出的另一种解释 {"statement"}，没有则 null
category_draft: 当用户判断与你的解释预测了不同的具体内容时，给多类别预测草稿
  {"behavior_key","description","categories":[含“其他”],"criteria":{每个类别: 可操作判定标准},"no_event_rule","multi_topic_rule"}，否则 null
premise_warning: 若某准备项可能改变被预测的行为（如提前展示材料可能改变对方首问），说明需要把它实际展示的时点写进预测前提，否则 null
类别必须互斥且穷尽；不得编造用户没说过的事实。"""
SYSTEM_REVISE = """用户回答了分叉问题。按他的回答调整建议，只输出 JSON：items、request_line、premise_warning（字段含义同前）。"""
SYSTEM_BRANCH = """判断用户的回答更符合哪个分支。只输出 JSON：{"branch":"A"|"B"|"unsure"}。用户记不清或不确定时输出 unsure。"""


@dataclass
class PrepOutput:
    fork_question: dict
    items: list
    request_line: dict
    category_draft: Optional[BehaviorDef]
    premise_warning: Optional[str]
    suggest_categorical: bool
    model_hypothesis_id: Optional[str] = None
    hypothesis_for: dict = field(default_factory=dict)  # 分支 → 假设 id

    def buttons(self, i: int) -> dict:
        """每个准备项两个独立按钮：记为行动 / 锁定相关预测（没有可结算的预测时后者不可用）。"""
        it = self.items[i]
        return {"record_action": True, "lock_prediction": it.get("prediction_text") is not None}


class PreMeeting:
    def __init__(self, intake: Intake, matter_id: str, person_id: str, interaction_id: str, model=None) -> None:
        self.intake, self.w, self.model = intake, intake.world, model
        self.matter_id, self.person_id, self.iid = matter_id, person_id, interaction_id
        self.prep: Optional[PrepOutput] = None
        self.behavior: Optional[BehaviorDef] = None
        self.last_meeting_iid = f"PREV-{person_id}"

    # —— C2：用户判断 + 模型另一种解释 + 建议多类别 ——
    def prepare(self) -> Optional[PrepOutput]:
        if self.model is None:
            return None
        m = self.w.matters[self.matter_id]
        mine = [h for h in self.w.hypotheses.values() if h.person_id == self.person_id and h.proposed_by == "user"]
        roles = [f"{r.role}/{r.dimension or '未定'}/{r.status}" for r in self.w.roles.values()
                 if r.matter_id == self.matter_id and r.person_id == self.person_id]
        prompt = f"议题：{m.title}；目的：{m.purpose}\n人物角色：{roles}\n用户的判断：{[h.statement for h in mine]}"
        try:
            d = self.model.structured(SYSTEM_PREP, prompt, validate_prep)
        except Exception:
            return None  # 不编造：交给手动
        cat, _ = _category_draft(d.get("category_draft"))
        for h in mine:
            h.matter_id = self.matter_id
        mid = None
        if d.get("model_hypothesis"):
            mh = self.w.add_hypothesis(self.person_id, d["model_hypothesis"]["statement"], matter_id=self.matter_id,
                                       proposed_by="model")
            mid = mh.id
        hyp_for = {}
        if mine:
            hyp_for["A"] = mine[0].id
        if mid:
            hyp_for["B"] = mid
        self.prep = PrepOutput(d["fork_question"], d["items"], d["request_line"], cat, d.get("premise_warning"),
                               suggest_categorical=cat is not None and len(hyp_for) >= 2, model_hypothesis_id=mid,
                               hypothesis_for=hyp_for)
        return self.prep

    # —— C3：分叉追问的回答 = 事后回忆（当时记录不可能：事件早已过去/模型为支持判断而问） ——
    def answer_fork(self, cid: str, text: str) -> dict:
        assert self.prep is not None
        if self.last_meeting_iid not in self.w.interactions:
            self.w.add_interaction(self.last_meeting_iid, "上次会谈", "occurred")
        reply = self.intake.handle(cid, text, prompt_kind="judgment_support_question", interaction_id=self.last_meeting_iid)
        obs = [self.intake.confirm(d) for d in reply.draft_ids]
        branch = "unsure"
        try:
            d = self.model.structured(SYSTEM_BRANCH, f"分叉问题：{self.prep.fork_question}\n用户回答：{text}", validate_branch)
            branch = d["branch"]
        except Exception:
            pass
        linked = None
        if branch in ("A", "B") and obs and branch in self.prep.hypothesis_for:
            linked = self.prep.hypothesis_for[branch]
            for o in obs:
                self.w.link_recall(o, linked)  # 回忆材料：只展示，不进比较（R10）
        self.intake.store.sync_world(self.w)
        return {"observations": obs, "branch": branch, "linked_hypothesis": linked, "reply": reply}

    def revise_after_fork(self, branch: str, answer: str) -> Optional[dict]:
        """按回答调整建议；返回新的准备项与会上一句话。"""
        if self.model is None or self.prep is None:
            return None
        try:
            d = self.model.structured(SYSTEM_REVISE, f"分叉问题：{self.prep.fork_question}\n回答倾向：{branch}\n回答：{answer}",
                                      validate_revise)
        except Exception:
            return None
        self.prep.items, self.prep.request_line = d["items"], d["request_line"]
        self.prep.premise_warning = d.get("premise_warning")
        return d

    # —— C2 确认类别、规则形式化 ——
    def confirm_categories(self, overrides: Optional[BehaviorDef] = None) -> BehaviorDef:
        b = overrides or (self.prep.category_draft if self.prep else None)
        if b is None:
            raise EngineError("没有可确认的类别草稿")
        self.w.engine.define_behavior(b)
        self.behavior = b
        return b

    def formalize(self, dists: dict) -> dict:
        """{假设 id: 各类别概率}。补齐后才 formal，才进入比较。"""
        assert self.behavior is not None
        return {hid: self.w.formalize(hid, self.behavior.key, dist=dist) for hid, dist in dists.items()}

    # —— C4：行动与预测分开锁定 ——
    def record_action(self, goal: str, item_texts: list) -> Intervention:
        itv = Intervention(f"I-{len(self.w.interventions) + 1}", self.iid, self.matter_id, goal,
                           "；".join(item_texts), list(item_texts))
        self.w.interventions[itv.id] = itv
        return itv

    def lock_prediction(self, prob_dist: dict, premise: Premise, *, baseline_note: str = "无历史，默认均匀分布"):
        assert self.behavior is not None
        beh = self.behavior
        if self.iid not in self.w.engine.opps:
            self.w.engine.add_opportunity(self.iid, beh.key, {"with": self.person_id}, self.iid)
        rules = [self.w.engine.rules[h] for h in self.prep.hypothesis_for.values() if h in self.w.engine.rules]
        p = self.w.engine.lock_prediction(
            id=f"P-{len(self.w.engine.preds) + 1}", person_id=self.person_id, matter_id=self.matter_id,
            behavior_key=beh.key, bind_mode="specific_item", opportunity_id=self.iid, rules=rules,
            premises=(premise,), prob_dist=prob_dist, baseline_note=baseline_note)
        self.w.followups.append(Followup(f"F{len(self.w.followups) + 1}", "settle_prediction", p.id, "after_meeting",
                                         f"会后结算 {p.id}"))
        self.intake.store.sync_world(self.w)
        return p
