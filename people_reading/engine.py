"""识人系统 v4.6 · 纯逻辑层（内存实现，无 I/O，无模型调用）。

只实现第一项交付所需的证据规则：
  R2 两两比较 / R4 事前锁定 / R5 结算幂等 / R6 当前比较状态 / R7 计算归程序
  R10 回忆不进比较 / R11 前提（含截止点、各自核对）
  3.2 区分判定 / 3.3 顺序无关 / 3.4 事实更正 / opportunity_outcome 只确认一次

持久化（SQLite）、对话、判断卡之外的界面留给后续交付。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Callable, Optional

EPS = 1e-9


# ───────────────────────── 错误 ─────────────────────────
class EngineError(Exception):
    pass


class LockRejected(EngineError):
    """锁定被拒绝（重复 specific_item、缺截止点、概率不合法等）。"""


class SettlementWriteRejected(EngineError):
    """结算字段只允许从 locked 状态写入一次。"""


class OutcomeMissing(EngineError):
    """该机会在此定义版本下还没有被确认的实际结果。"""


class PremiseNotAsked(EngineError):
    """前提必须逐条核对；没有回答不得默认成立（C5-d 末行）。"""


class UnconfirmedCorrection(EngineError):
    pass


# ───────────────────────── 定义 ─────────────────────────
@dataclass(frozen=True)
class BehaviorDef:
    key: str
    version: int
    outcome_type: str  # binary | categorical
    description: str
    event_label: str = ""  # binary：被预测的事，如“先问职责”
    categories: tuple = ()
    category_criteria: tuple = ()  # ((类别, 判定标准), ...)
    no_event_rule: str = ""
    multi_topic_rule: str = ""

    def validate(self) -> None:
        if self.outcome_type not in ("binary", "categorical"):
            raise EngineError("outcome_type 必须是 binary 或 categorical")
        if self.outcome_type == "categorical":
            if len(set(self.categories)) != len(self.categories) or len(self.categories) < 2:
                raise EngineError("类别必须互斥且至少两个")
            if "其他" not in self.categories:
                raise EngineError("类别必须穷尽：需含「其他」")
            crit = dict(self.category_criteria)
            if set(crit) != set(self.categories) or not all(crit.values()):
                raise EngineError("每个类别都必须有可操作的判定标准")
            if not self.no_event_rule or not self.multi_topic_rule:
                raise EngineError("categorical 必须写明 no_event_rule 与 multi_topic_rule")


@dataclass(frozen=True)
class Rule:
    """冻结的规则定义。fn(facts) → binary: p_hit | categorical: {类别: 概率} | None（未知）。"""

    id: str
    name: str
    behavior_key: str
    outcome_type: str
    definition: str
    fn: Callable[[dict], Any] = field(compare=False, repr=False, default=lambda f: None)
    spec: Optional[dict] = field(compare=False, repr=False, default=None)  # 声明式定义，可持久化

    def value(self, facts: dict) -> Any:
        return self.fn(facts)

    @staticmethod
    def p_from_value(outcome_type: str, value: Any, outcome: Any) -> Optional[float]:
        if value is None:
            return None
        if outcome_type == "binary":
            return _rnd(value if outcome else 1 - value)
        return _rnd(value[outcome])

    def p_of(self, facts: dict, outcome: Any) -> Optional[float]:
        return self.p_from_value(self.outcome_type, self.value(facts), outcome)


def _fn_from_spec(spec: dict) -> Callable[[dict], Any]:
    k = spec["kind"]
    spec = copy.deepcopy(spec)  # 闭包持有自己的拷贝
    if k == "const_binary":
        p = spec["p"]
        return lambda f: p
    if k == "const_dist":
        dist = dict(spec["dist"])
        return lambda f: dict(dist)
    if k == "fact_binary":  # 事实 key 未知 → None（不可求值）
        def fn(f):
            v = f.get(spec["key"])
            if v is None:
                return None
            hit = (v is True) if spec["test"] == "is_true" else _same(v, spec["equals"])
            return spec["p_if"] if hit else spec["p_else"]
        return fn
    raise EngineError(f"未知规则类型 {k}")


def make_rule(id: str, name: str, behavior_key: str, spec: dict, definition: str = "") -> Rule:
    """由声明式 spec 构造规则；outcome_type 由 spec 推出。"""
    ot = "categorical" if spec["kind"] == "const_dist" else "binary"
    if spec["kind"] == "const_binary":
        _check_prob(spec["p"], "规则概率 p")
    elif spec["kind"] == "const_dist":
        for k, v in spec["dist"].items():
            _check_prob(v, f"规则类别“{k}”的概率")
        if abs(sum(spec["dist"].values()) - 1) > 1e-6:
            raise EngineError("规则的类别概率之和必须为 1")
    elif spec["kind"] == "fact_binary":
        _check_prob(spec["p_if"], "规则概率 p_if")
        _check_prob(spec["p_else"], "规则概率 p_else")
    spec = copy.deepcopy(spec)  # 规则自己持有一份，调用方之后再改原字典不影响
    return Rule(id, name, behavior_key, ot, definition or _canon(spec), _fn_from_spec(spec), spec)


def _freeze_rule(r: "Rule") -> "Rule":
    """锁定时冻结一份规则：声明式规则深拷贝后重建；只有函数的规则（测试用）无法拷贝，原样保留。"""
    if r.spec is None:
        return r
    return make_rule(r.id, r.name, r.behavior_key, r.spec, r.definition)


@dataclass(frozen=True)
class Premise:
    id: str
    text: str  # 按 R11 写“实际发生的事及其先后”
    anchor_event: str = ""  # 以“某事件发生之前”为条件时填写
    cutoff: str = ""  # 该事件始终未发生时的截止点；有 anchor_event 则必填
    # 以下只影响结算时的提问界面，不进入 content_hash：中性问题 + 选项（选项 → holds|violated|unclear）
    question: str = ""
    cutoff_question: str = ""  # 标志事件始终没发生时，改按截止点询问
    choices: tuple = ()


@dataclass
class Opportunity:
    id: str
    behavior_key: str
    facts: dict
    label: str = ""
    corrected: bool = False


@dataclass
class OutcomeRecord:
    opportunity_id: str
    behavior_key: str
    def_version: int
    outcome: Any  # True/False 或类别名；unobservable 时 None
    status: str  # confirmed | unobservable
    source_spans: tuple = ()
    recalled_only: bool = False  # 实际结果仅由事后回忆支撑（R10）
    proposal: str = ""
    confirmed_by_user: bool = True
    confirmed_at: int = 0


@dataclass(frozen=True)
class PreContent:
    id: str
    person_id: str
    matter_id: str
    behavior_key: str
    def_version: int
    outcome_type: str
    frozen_categories: Optional[tuple]
    settlement_criterion: Any
    bind_mode: str  # specific_item | condition_match
    bound_opportunity_id: Optional[str]
    conditions: tuple
    premises: tuple
    prob: Optional[float]
    prob_dist: Optional[tuple]
    baseline_prob: Optional[float]
    baseline_dist: Optional[tuple]
    baseline_note: str
    threshold: float
    window: Optional[tuple]
    rule_defs: tuple  # ((rule_id, definition), ...)
    quadrant_at_lock: Optional[str]
    locked_at: int
    content_hash: str


@dataclass
class Settlement:
    status: str = "locked"  # locked|settled|void_premise_changed|unobservable|expired
    outcome: Any = None  # 只读，取自 OutcomeRecord
    premise_check: dict = field(default_factory=dict)
    brier: Optional[float] = None
    brier_baseline: Optional[float] = None
    settled_opportunity_id: Optional[str] = None  # 仅 settled
    reviewed_opportunity_id: Optional[str] = None  # 作废/无法观察时也记下是哪个机会
    settled_at: Optional[int] = None
    lock_facts_corrected: bool = False
    correction_revision_id: Optional[int] = None


@dataclass
class RuleSnap:
    value: Any  # 锁定（condition_match 为结算）时的规则输出，永不改写
    evaluable: bool


@dataclass
class Prediction:
    pre: PreContent
    st: Settlement = field(default_factory=Settlement)
    rule_snaps: dict = field(default_factory=dict)  # prediction_rule：rule_id → RuleSnap
    frozen_rules: dict = field(default_factory=dict)  # 锁定时冻结的规则定义；结算与更正一律用它，不看后来的 engine.rules

    @property
    def id(self) -> str:
        return self.pre.id


@dataclass
class PairRow:
    opportunity_id: str
    behavior_key: str
    h1: str
    h2: str
    status: str  # counted | below_threshold | recall_only | not_evaluable
    source_prediction_id: Optional[str] = None
    outcome: Any = None
    p1: Optional[float] = None
    p2: Optional[float] = None
    lr: Optional[float] = None  # h2 相对 h1，仅 counted
    facts_basis: str = "as_locked"
    last_revision_id: Optional[int] = None
    updated_at: int = 0
    note: str = ""

    def state(self) -> dict:
        return {
            "status": self.status,
            "source": self.source_prediction_id,
            "outcome": self.outcome,
            "p1": self.p1,
            "p2": self.p2,
            "lr": self.lr,
            "facts_basis": self.facts_basis,
        }


@dataclass(frozen=True)
class Revision:
    id: int
    at: int
    kind: str
    subject: str
    detail: dict
    reason: str = ""
    prediction_id: Optional[str] = None
    source_spans: tuple = ()
    confirmed_by_user: bool = True


def _rnd(x: float) -> float:
    """相对精度舍入（12 位有效数字）：消除浮点噪声，但不会把 1e-10 这样的极小正数舍成 0。"""
    return float(f"{x:.12g}")


def _same(a: Any, b: Any) -> bool:
    """按规范 JSON 形式比较：元组与列表、保存前与 JSON 往返后视为同一个值。"""
    return _canon(a) == _canon(b)


def _check_prob(x: Any, what: str, exc=None) -> None:
    exc = exc or EngineError
    if not isinstance(x, (int, float)) or isinstance(x, bool) or not (0 <= x <= 1):
        raise exc(f"{what} 必须在 0–1 之间，收到 {x!r}")


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, default=list)


# ───────────────────────── 引擎 ─────────────────────────
class Engine:
    def __init__(self) -> None:
        self._tick = 0
        self.behaviors: dict[tuple, BehaviorDef] = {}
        self._frozen_behaviors: set[tuple] = set()
        self.rules: dict[str, Rule] = {}
        self.opps: dict[str, Opportunity] = {}
        self.preds: dict[str, Prediction] = {}
        self.outcomes: dict[tuple, OutcomeRecord] = {}
        self.rows: dict[tuple, PairRow] = {}
        self.revisions: list[Revision] = []
        self._cx: dict[tuple, bool] = {}
        self._previews: set = set()  # 已生成、尚未使用的更正预览令牌（临时，不持久化）
        self.outcome_questions_asked = 0  # 测试用：统计向用户询问结果的次数

    # —— 基础 ——
    def now(self) -> int:
        self._tick += 1
        return self._tick

    def _log(self, kind, subject, detail, reason="", prediction_id=None, spans=()) -> Revision:
        r = Revision(len(self.revisions) + 1, self.now(), kind, subject, detail, reason, prediction_id, tuple(spans))
        self.revisions.append(r)
        return r

    def define_behavior(self, b: BehaviorDef) -> BehaviorDef:
        b.validate()
        k = (b.key, b.version)
        if k in self.behaviors and self.behaviors[k] != b:
            if k in self._frozen_behaviors:
                raise EngineError("定义已被预测冻结，只能生成新版本")
            raise EngineError("同一版本不得改写；请递增 version")
        self.behaviors[k] = b
        return b

    def add_rule(self, r: Rule) -> Rule:
        old = self.rules.get(r.id)
        if old is not None and (old.definition != r.definition or not _same(old.spec, r.spec)):
            if any(r.id in p.frozen_rules for p in self.preds.values()):
                raise EngineError(f"规则 {r.id} 已被预测冻结，不能改写；请新建一条规则")
        self.rules[r.id] = r
        return r

    def add_opportunity(self, opp_id: str, behavior_key: str, facts: dict, label: str = "") -> Opportunity:
        o = Opportunity(opp_id, behavior_key, dict(facts), label)
        self.opps[opp_id] = o
        return o

    def _latest_version(self, key: str) -> int:
        vs = [v for (k, v) in self.behaviors if k == key]
        if not vs:
            raise EngineError(f"未定义行为 {key}")
        return max(vs)

    # —— 锁定（R4、R11） ——
    def lock_prediction(
        self,
        *,
        id: str,
        person_id: str,
        matter_id: str,
        behavior_key: str,
        bind_mode: str,
        rules: list,
        opportunity_id: Optional[str] = None,
        conditions: Optional[dict] = None,
        premises: tuple = (),
        prob: Optional[float] = None,
        prob_dist: Optional[dict] = None,
        baseline_prob: Optional[float] = None,
        baseline_dist: Optional[dict] = None,
        baseline_note: str = "",
        threshold: float = 0.3,
        settlement_criterion: Optional[str] = None,
        window: Optional[tuple] = None,
        quadrant_at_lock: Optional[str] = None,
        def_version: Optional[int] = None,
    ) -> Prediction:
        if id in self.preds:
            raise LockRejected("预测 id 已存在")
        ver = def_version or self._latest_version(behavior_key)
        b = self.behaviors[(behavior_key, ver)]
        if bind_mode not in ("specific_item", "condition_match"):
            raise LockRejected("bind_mode 非法")
        for pr in premises:  # R11 截止点
            if pr.anchor_event and not pr.cutoff:
                raise LockRejected(f"前提「{pr.text}」以“{pr.anchor_event}之前”为条件，必须写明该事件始终未发生时的截止点")
        if bind_mode == "specific_item":
            if opportunity_id not in self.opps:
                raise LockRejected("specific_item 必须绑定已存在的机会")
            for q in self.preds.values():  # A.5.3：结算前后都拒绝
                if (
                    q.pre.bind_mode == "specific_item"
                    and q.pre.bound_opportunity_id == opportunity_id
                    and q.pre.behavior_key == behavior_key
                ):
                    raise LockRejected("同一机会同一行为已有 specific_item 预测")
        if b.outcome_type == "binary":
            if prob is None or not (0 <= prob <= 1):
                raise LockRejected("binary 需要 0–1 的 prob")
            if baseline_prob is not None:
                _check_prob(baseline_prob, "baseline_prob", LockRejected)
            if not settlement_criterion:
                raise LockRejected("binary 预测必须写结算标准")
            bprob = 0.5 if baseline_prob is None else baseline_prob
            note = baseline_note or "默认：binary 0.5"
            bdist = None
            pdist = None
            crit: Any = settlement_criterion
            cats = None
        else:
            if prob_dist is None or set(prob_dist) != set(b.categories) or abs(sum(prob_dist.values()) - 1) > 1e-6:
                raise LockRejected("categorical 需要覆盖全部类别且和为 1 的 prob_dist")
            for c_, v_ in prob_dist.items():
                _check_prob(v_, f"prob_dist[{c_}]", LockRejected)
            n = len(b.categories)
            bd = baseline_dist or {c: 1 / n for c in b.categories}
            if set(bd) != set(b.categories) or abs(sum(bd.values()) - 1) > 1e-6:
                raise LockRejected("baseline_dist 必须覆盖全部类别且和为 1")
            for c_, v_ in bd.items():
                _check_prob(v_, f"baseline_dist[{c_}]", LockRejected)
            note = baseline_note or "默认：categorical 均匀分布"
            bdist = tuple(sorted(bd.items()))
            pdist = tuple(sorted(prob_dist.items()))
            bprob = None
            crit = b.category_criteria  # 冻结类别判定标准
            cats = tuple(b.categories)
        frozen: dict = {}
        for r in rules:
            if r.behavior_key != behavior_key:
                raise LockRejected("规则与预测的行为不一致")
            old = self.rules.get(r.id)
            if old is not None and (old.definition != r.definition or not _same(old.spec, r.spec)):
                raise LockRejected(f"规则 id {r.id} 已被另一个定义占用")
            self.rules.setdefault(r.id, r)
            frozen[r.id] = _freeze_rule(r)
        content = dict(
            id=id, person_id=person_id, matter_id=matter_id, behavior_key=behavior_key, def_version=ver,
            outcome_type=b.outcome_type, frozen_categories=cats, settlement_criterion=crit, bind_mode=bind_mode,
            bound_opportunity_id=opportunity_id, conditions=tuple(sorted((conditions or {}).items())),
            premises=tuple((p.id, p.text, p.anchor_event, p.cutoff) for p in premises),
            prob=prob, prob_dist=pdist, baseline_prob=bprob, baseline_dist=bdist, baseline_note=note,
            threshold=threshold, window=window,
            rule_defs=tuple((r.id, r.definition, _canon(r.spec)) for r in rules),  # 哈希覆盖规则的实际规格，不只是标签
        )
        h = hashlib.sha256(_canon(content).encode()).hexdigest()
        pre = PreContent(
            id=id, person_id=person_id, matter_id=matter_id, behavior_key=behavior_key, def_version=ver,
            outcome_type=b.outcome_type, frozen_categories=cats, settlement_criterion=crit, bind_mode=bind_mode,
            bound_opportunity_id=opportunity_id, conditions=content["conditions"], premises=tuple(premises),
            prob=prob, prob_dist=pdist, baseline_prob=bprob, baseline_dist=bdist, baseline_note=note,
            threshold=threshold, window=window, rule_defs=content["rule_defs"], quadrant_at_lock=quadrant_at_lock,
            locked_at=self.now(), content_hash=h,
        )
        p = Prediction(pre)
        p.frozen_rules = frozen
        if bind_mode == "specific_item":  # specific_item：锁定时用已知事实求值
            facts = self.opps[opportunity_id].facts
            for r in (frozen[x.id] for x in rules):
                v = r.value(facts)
                p.rule_snaps[r.id] = RuleSnap(copy.deepcopy(v), v is not None)
        else:  # condition_match：规则在结算时对匹配机会求值
            for r in rules:
                p.rule_snaps[r.id] = RuleSnap(None, False)
        self.preds[id] = p
        self._frozen_behaviors.add((behavior_key, ver))
        return p

    def verify_hash(self, pid: str) -> bool:
        pre = self.preds[pid].pre
        content = dict(
            id=pre.id, person_id=pre.person_id, matter_id=pre.matter_id, behavior_key=pre.behavior_key,
            def_version=pre.def_version, outcome_type=pre.outcome_type, frozen_categories=pre.frozen_categories,
            settlement_criterion=pre.settlement_criterion, bind_mode=pre.bind_mode,
            bound_opportunity_id=pre.bound_opportunity_id, conditions=pre.conditions,
            premises=tuple((p.id, p.text, p.anchor_event, p.cutoff) for p in pre.premises),
            prob=pre.prob, prob_dist=pre.prob_dist, baseline_prob=pre.baseline_prob,
            baseline_dist=pre.baseline_dist, baseline_note=pre.baseline_note, threshold=pre.threshold,
            window=pre.window, rule_defs=pre.rule_defs,
        )
        return hashlib.sha256(_canon(content).encode()).hexdigest() == pre.content_hash

    # —— 结算字段只写一次（A.5.2） ——
    def write_settlement_fields(self, pid: str, **fields) -> None:
        p = self.preds[pid]
        if p.st.status != "locked":
            raise SettlementWriteRejected(f"{pid} 已处于 {p.st.status}，结算字段不可再写")
        for k, v in fields.items():
            setattr(p.st, k, v)

    # —— 实际结果：按机会确认一次 ——
    def outcome_for(self, opp_id: str, behavior_key: str, def_version: int) -> Optional[OutcomeRecord]:
        return self.outcomes.get((opp_id, behavior_key, def_version))

    def needs_outcome_confirmation(self, opp_id: str, behavior_key: str, def_version: int) -> bool:
        """已有结果就直接复用，不再向用户询问。"""
        return (opp_id, behavior_key, def_version) not in self.outcomes

    def confirm_outcome(
        self, opp_id: str, behavior_key: str, def_version: int, outcome: Any, *,
        status: str = "confirmed", source_spans: tuple = (), recalled_only: bool = False, proposal: str = "",
    ) -> OutcomeRecord:
        key = (opp_id, behavior_key, def_version)
        if key in self.outcomes:
            return self.outcomes[key]  # 同一版本绝不重复确认
        b = self.behaviors[(behavior_key, def_version)]
        if status == "confirmed":
            if b.outcome_type == "binary" and not isinstance(outcome, bool):
                raise EngineError("binary 结果必须是 True/False")
            if b.outcome_type == "categorical" and outcome not in b.categories:
                raise EngineError("结果不在冻结的类别内")
        rec = OutcomeRecord(opp_id, behavior_key, def_version, outcome if status == "confirmed" else None,
                            status, tuple(source_spans), recalled_only, proposal, True, self.now())
        self.outcomes[key] = rec
        self.outcome_questions_asked += 1
        return rec

    # —— 结算（R5 幂等、R11 各自核对） ——
    def settle_prediction(self, pid: str, premise_answers: dict, opp_id: Optional[str] = None) -> Prediction:
        """premise_answers: {premise_id: (holds|violated|unclear, 依据原话)}。"""
        p = self.preds[pid]
        if p.st.status != "locked":
            return p  # R5：重复结算不改变任何计数
        pre = p.pre
        opp_id = pre.bound_opportunity_id or opp_id
        if opp_id not in self.opps:
            raise EngineError("condition_match 结算需要指明匹配的机会")
        opp = self.opps[opp_id]
        if pre.bind_mode == "condition_match":
            if any(not _same(opp.facts.get(k), v) for k, v in pre.conditions):
                raise EngineError("该机会不满足预测的情境条件")
        rec = self.outcomes.get((opp_id, pre.behavior_key, pre.def_version))
        if rec is None:
            raise OutcomeMissing("先确认该机会的实际结果")
        check = {}
        for pr in pre.premises:
            if pr.id not in premise_answers:
                raise PremiseNotAsked(f"前提「{pr.text}」尚未核对")
            status, quote = premise_answers[pr.id]
            check[pr.id] = {"text": pr.text, "status": status, "quote": quote}
        end = None
        if any(c["status"] == "violated" for c in check.values()):
            end = "void_premise_changed"
        elif any(c["status"] == "unclear" for c in check.values()) or rec.status == "unobservable":
            end = "unobservable"
        updates: dict[str, Any] = {"premise_check": check, "settled_at": self.now(), "reviewed_opportunity_id": opp_id}
        if end:
            updates["status"] = end
        else:
            updates["status"] = "settled"
            updates["settled_opportunity_id"] = opp_id
            updates["outcome"] = rec.outcome
            if pre.outcome_type == "binary":
                o = 1.0 if rec.outcome else 0.0
                updates["brier"] = round((pre.prob - o) ** 2, 10)
                updates["brier_baseline"] = round((pre.baseline_prob - o) ** 2, 10)
            else:
                d, bd = dict(pre.prob_dist), dict(pre.baseline_dist)
                updates["brier"] = round(sum((d[c] - (1.0 if c == rec.outcome else 0.0)) ** 2 for c in d), 10)
                updates["brier_baseline"] = round(sum((bd[c] - (1.0 if c == rec.outcome else 0.0)) ** 2 for c in bd), 10)
            if pre.bind_mode == "condition_match":
                for rid in p.rule_snaps:
                    v = p.frozen_rules[rid].value(opp.facts)
                    p.rule_snaps[rid] = RuleSnap(copy.deepcopy(v), v is not None)
        self.write_settlement_fields(pid, **updates)
        if end:
            self._log("void", pid,
                      {"status": end, "premise_check": check}, reason="前提核对：" + "；".join(
                          f"{c['text']} → {c['status']}（{c['quote']}）" for c in check.values()), prediction_id=pid)
        self._recompute(opp_id, "结算 " + pid)
        return p

    # —— 当前比较状态的计算（纯函数，预览与写入共用） ——
    def _settled_on(self, opp_id: str) -> list:
        return [p for p in self.preds.values() if p.st.status == "settled" and p.st.settled_opportunity_id == opp_id]

    def _source(self, opp_id: str, rule_ids: list) -> Optional[Prediction]:
        """3.3：只从 settled 预测中选；specific_item 优先，其次最早锁定的 condition_match。"""
        ok = [p for p in self._settled_on(opp_id)
              if all(r in p.rule_snaps and p.rule_snaps[r].evaluable for r in rule_ids)]
        ok.sort(key=lambda p: (p.pre.bind_mode != "specific_item", p.pre.locked_at))
        return ok[0] if ok else None

    def _compute_rows(self, opp_id: str, facts: dict, corrected: bool) -> dict:
        rule_ids = sorted({r for p in self._settled_on(opp_id) for r in p.rule_snaps})
        out: dict[tuple, PairRow] = {}
        for h1, h2 in combinations(rule_ids, 2):
            src = self._source(opp_id, [h1, h2])
            bk = self.rules[h1].behavior_key
            key = (opp_id, bk, h1, h2)
            basis = "corrected" if corrected else "as_locked"
            if src is None:
                out[key] = PairRow(opp_id, bk, h1, h2, "not_evaluable", facts_basis=basis)
                continue
            rec = self.outcomes[(opp_id, bk, src.pre.def_version)]
            p1 = src.frozen_rules[h1].p_of(facts, rec.outcome)  # 用锁定时冻结的规则，不看后来的 engine.rules
            p2 = src.frozen_rules[h2].p_of(facts, rec.outcome)
            row = PairRow(opp_id, bk, h1, h2, "not_evaluable", src.id, rec.outcome, p1, p2, None, basis)
            if p1 is None or p2 is None:
                row.note = "所需字段未知"
            elif rec.recalled_only:
                row.status = "recall_only"  # R10
            elif abs(p1 - p2) + EPS >= src.pre.threshold:
                if p1 <= 0 or p2 <= 0:
                    row.note = "概率为 0，似然比无定义"  # 不静默计入
                else:
                    row.status, row.lr = "counted", _rnd(p2 / p1)
            else:
                row.status = "below_threshold"
            out[key] = row
        return out

    @staticmethod
    def _core(row: PairRow) -> dict:
        d = row.state()
        d.pop("facts_basis")
        return d

    def _apply_rows(self, new: dict, reason: str) -> None:
        for key, row in new.items():
            old = self.rows.get(key)
            row.updated_at = self.now()
            if old is None:
                if row.status in ("counted", "below_threshold"):
                    rev = self._log("pair_update" if row.status == "counted" else "no_discrimination",
                                    f"{key[2]}–{key[3]}@{key[0]}", row.state(), reason, row.source_prediction_id)
                    row.last_revision_id = rev.id
                self.rows[key] = row
            elif self._core(old) != self._core(row):  # 仅 facts_basis 变化不算比较变化
                rev = self._log("comparison_changed", f"{key[2]}–{key[3]}@{key[0]}",
                                {"before": old.state(), "after": row.state()}, reason, row.source_prediction_id)
                row.last_revision_id = rev.id
                self.rows[key] = row
            else:
                old.facts_basis = row.facts_basis

    def _counterexamples(self, opp_id: str, facts: dict, reason: str) -> None:
        rule_ids = sorted({r for p in self._settled_on(opp_id) for r in p.rule_snaps})
        for h in rule_ids:
            src = self._source(opp_id, [h])
            if src is None:
                continue
            rec = self.outcomes[(opp_id, src.pre.behavior_key, src.pre.def_version)]
            flag = False
            if not rec.recalled_only:
                rule = src.frozen_rules[h]
                v = rule.value(facts)
                if v is not None:
                    if rule.outcome_type == "binary":
                        flag = (v >= 0.7 - EPS and not rec.outcome) or (v <= 0.3 + EPS and bool(rec.outcome))
                    else:
                        flag = v[rec.outcome] <= 0.2 + EPS
            was = self._cx.get((opp_id, h), False)
            if flag != was:
                self._cx[(opp_id, h)] = flag
                self._log("counterexample", f"{h}@{opp_id}", {"flag": flag, "outcome": rec.outcome},
                          reason if flag else "更正后不再构成反例", src.id)

    def recompute(self, opp_id: str, reason: str) -> None:
        """补全未知事实后重算该机会的当前比较状态（没有已结算预测时什么也不会发生）。"""
        self._recompute(opp_id, reason)

    def _recompute(self, opp_id: str, reason: str) -> None:
        o = self.opps[opp_id]
        self._apply_rows(self._compute_rows(opp_id, o.facts, o.corrected), reason)
        self._counterexamples(opp_id, o.facts, reason)

    # —— 事实更正（3.4） ——
    def _preview_token(self, opp_id: str, field_name: str, value: Any) -> str:
        """令牌绑定：这一笔变更 + 预览当时该机会的事实与比较状态。状态之后变了，令牌作废。"""
        o = self.opps[opp_id]
        state = [o.facts, sorted((list(k), r.state()) for k, r in self.rows.items() if k[0] == opp_id),
                 sorted((p.id, p.st.status) for p in self.preds.values())]
        return hashlib.sha256(_canon([opp_id, field_name, value, state]).encode()).hexdigest()[:24]

    def correction_preview(self, opp_id: str, field_name: str, value: Any) -> dict:
        o = self.opps[opp_id]
        facts = dict(o.facts)
        facts[field_name] = value
        new = self._compute_rows(opp_id, facts, True)
        changes = []
        for key, row in new.items():
            old = self.rows.get(key)
            if old is None or self._core(old) != self._core(row):
                changes.append({"pair": key[2:], "before": old.state() if old else None, "after": row.state()})
        affected = []
        for p in self._settled_on(opp_id):
            diff = [rid for rid, s in p.rule_snaps.items()
                    if p.frozen_rules[rid].value(facts) != s.value]
            if diff:
                affected.append({"prediction": p.id, "rules_whose_value_changes": diff, "brier_unchanged": p.st.brier})
        token = self._preview_token(opp_id, field_name, value)
        self._previews.add(token)
        return {"opportunity": opp_id, "field": field_name, "old": o.facts.get(field_name), "new": value, "token": token,
                "comparison_changes": changes, "predictions_marked_lock_facts_corrected": affected,
                "not_yet_implemented": ["解读", "角色", "关系边"]}

    def apply_correction(self, opp_id: str, field_name: str, value: Any, *, confirmed_by_user: bool,
                         source_spans: tuple = (), reason: str = "", preview_token: Optional[str] = None) -> Revision:
        if not confirmed_by_user:
            raise UnconfirmedCorrection("事实更正必须先预览并由用户确认")
        if preview_token is None or preview_token not in self._previews:
            raise UnconfirmedCorrection("事实更正必须先调用 correction_preview，并带上它返回的 token")
        if preview_token != self._preview_token(opp_id, field_name, value):
            raise UnconfirmedCorrection("预览与这次更正不一致，或预览之后数据已变化；请重新预览")
        self._previews.discard(preview_token)  # 一次性
        o = self.opps[opp_id]
        before = o.facts.get(field_name)
        rev = self._log("fact_correction", f"{opp_id}.{field_name}", {"before": before, "after": value},
                        reason or f"用户更正 {field_name}", spans=source_spans)
        o.facts[field_name] = value
        o.corrected = True
        for p in self._settled_on(opp_id):  # 快照与原 Brier 不动，仅打标
            if any(p.frozen_rules[rid].value(o.facts) != s.value for rid, s in p.rule_snaps.items()):
                p.st.lock_facts_corrected = True
                p.st.correction_revision_id = rev.id
        self._recompute(opp_id, f"事实更正：{field_name} {before!r} → {value!r}")
        return rev

    # —— 查询 ——
    def cumulative(self, h1: str, h2: str) -> dict:
        """h2 相对 h1 的累计倍数；只读各机会的当前状态（counted）。"""
        mult, opps = 1.0, []
        for (opp, _bk, a, b), row in sorted(self.rows.items()):
            if row.status != "counted":
                continue
            if (a, b) == (h1, h2):
                mult *= row.lr
            elif (a, b) == (h2, h1):
                mult /= row.lr
            else:
                continue
            opps.append(opp)
        return {"multiple": _rnd(mult), "n": len(opps), "opportunities": opps}

    def comparison_detail(self, opp_id: str, h1: str, h2: str) -> dict:
        """展开：锁定时的概率 / 更正前的比较结果 / 当前比较结果。"""
        a, b = sorted((h1, h2))
        row = next(r for k, r in self.rows.items() if k[0] == opp_id and k[2:] == (a, b))
        rec = self.outcomes[(opp_id, row.behavior_key, self.preds[row.source_prediction_id].pre.def_version)]
        src = self.preds[row.source_prediction_id]
        lock_p = {h: Rule.p_from_value(src.frozen_rules[h].outcome_type, src.rule_snaps[h].value, rec.outcome) for h in (a, b)}
        changes = [r for r in self.revisions if r.kind == "comparison_changed" and r.subject == f"{a}–{b}@{opp_id}"
                   and r.reason.startswith("事实更正")]  # 仅来源变化（如先后结算顺序不同）不算“更正前”
        return {"at_lock": lock_p, "before_correction": changes[0].detail["before"] if changes else None,
                "current": row.state(), "corrected": row.facts_basis == "corrected"}

    def calibration(self, include_corrected: bool = False, matter_id: Optional[str] = None) -> dict:
        out: dict[str, dict] = {}
        for p in self.preds.values():
            if matter_id is not None and p.pre.matter_id != matter_id:
                continue
            g = out.setdefault(p.pre.outcome_type, {"locked": 0, "settled": 0, "void": 0, "unobservable": 0,
                                                    "expired": 0, "_b": [], "_bb": []})
            g["locked"] += 1
            s = p.st.status
            if s == "settled":
                g["settled"] += 1
                if include_corrected or not p.st.lock_facts_corrected:
                    g["_b"].append(p.st.brier)
                    g["_bb"].append(p.st.brier_baseline)
            elif s == "void_premise_changed":
                g["void"] += 1
            elif s in ("unobservable", "expired"):
                g[s] += 1
        for g in out.values():
            b, bb = g.pop("_b"), g.pop("_bb")
            g["brier_n"] = len(b)
            g["mean_brier"] = round(sum(b) / len(b), 10) if b else None
            g["mean_baseline"] = round(sum(bb) / len(bb), 10) if bb else None
        return out

    def fingerprint(self) -> tuple:
        """顺序无关检验：当前比较状态与各预测结算结果（不含来源与修订日志）。"""
        rows = tuple(sorted((k, r.status, r.lr, r.p1, r.p2) for k, r in self.rows.items()))
        preds = tuple(sorted((p.id, p.st.status, p.st.brier, p.st.brier_baseline) for p in self.preds.values()))
        return rows, preds


    # ───────── 持久化：整体转成 JSON 友好的字典 ─────────
    def to_dict(self) -> dict:
        import dataclasses as dc

        def pre(p: PreContent) -> dict:
            d = dc.asdict(p)
            d["premises"] = [dc.asdict(x) for x in p.premises]
            return d
        for r in self.rules.values():
            if r.spec is None:
                raise EngineError(f"规则 {r.id} 没有声明式 spec，无法持久化")
        return {
            "tick": self._tick, "asked": self.outcome_questions_asked,
            "behaviors": [dc.asdict(b) for b in self.behaviors.values()],
            "frozen": [list(x) for x in self._frozen_behaviors],
            "rules": [{"id": r.id, "name": r.name, "behavior_key": r.behavior_key, "definition": r.definition,
                       "spec": r.spec} for r in self.rules.values()],
            "opps": [dc.asdict(o) for o in self.opps.values()],
            "preds": [{"pre": pre(p.pre), "st": dc.asdict(p.st),
                       "snaps": {k: dc.asdict(v) for k, v in p.rule_snaps.items()},
                       "frozen": {k: {"name": r.name, "behavior_key": r.behavior_key, "definition": r.definition, "spec": r.spec}
                                  for k, r in p.frozen_rules.items()}} for p in self.preds.values()],
            "outcomes": [dc.asdict(o) for o in self.outcomes.values()],
            "rows": [dc.asdict(r) for r in self.rows.values()],
            "revisions": [dc.asdict(r) for r in self.revisions],
            "cx": [[k[0], k[1], v] for k, v in self._cx.items()],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Engine":
        e = cls()
        e._tick, e.outcome_questions_asked = d["tick"], d["asked"]
        for b in d["behaviors"]:
            b["categories"] = tuple(b["categories"])
            b["category_criteria"] = tuple(tuple(x) for x in b["category_criteria"])
            bd = BehaviorDef(**b)
            e.behaviors[(bd.key, bd.version)] = bd
        e._frozen_behaviors = {tuple(x) for x in d["frozen"]}
        for r in d["rules"]:
            e.rules[r["id"]] = make_rule(r["id"], r["name"], r["behavior_key"], r["spec"], r["definition"])
        for o in d["opps"]:
            e.opps[o["id"]] = Opportunity(**o)
        for pd in d["preds"]:
            x = pd["pre"]
            x["premises"] = tuple(Premise(**{**q, "choices": tuple(tuple(c) for c in q.get("choices", ()))})
                                  for q in x["premises"])
            for k in ("conditions", "rule_defs"):
                x[k] = tuple(tuple(i) for i in x[k])
            for k in ("prob_dist", "baseline_dist"):
                x[k] = tuple(tuple(i) for i in x[k]) if x[k] is not None else None
            if x["frozen_categories"] is not None:
                x["frozen_categories"] = tuple(x["frozen_categories"])
            if isinstance(x["settlement_criterion"], list):
                x["settlement_criterion"] = tuple(tuple(i) for i in x["settlement_criterion"])
            if x["window"] is not None:
                x["window"] = tuple(x["window"])
            p = Prediction(PreContent(**x), Settlement(**pd["st"]),
                           {k: RuleSnap(**v) for k, v in pd["snaps"].items()})
            p.frozen_rules = {k: make_rule(k, f["name"], f["behavior_key"], f["spec"], f["definition"])
                              for k, f in pd.get("frozen", {}).items()} or {k: e.rules[k] for k in p.rule_snaps}
            e.preds[p.id] = p
        for o in d["outcomes"]:
            o["source_spans"] = tuple(o["source_spans"])
            r = OutcomeRecord(**o)
            e.outcomes[(r.opportunity_id, r.behavior_key, r.def_version)] = r
        for r in d["rows"]:
            row = PairRow(**r)
            e.rows[(row.opportunity_id, row.behavior_key, row.h1, row.h2)] = row
        for r in d["revisions"]:
            r["source_spans"] = tuple(r["source_spans"])
            e.revisions.append(Revision(**r))
        e._cx = {(a, b): v for a, b, v in d["cx"]}
        return e
