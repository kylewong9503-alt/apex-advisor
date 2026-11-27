"""观察排序（3.9，3.7 的区分问题）。

  1. 先筛掉区分力不足阈值的场景
  2. 零代价场景（观察信号：不必问对方）按区分力排序
  3. 正代价场景（当面询问：会消耗关系，代价由你评估）按“区分力 ÷ 代价”排序，排在零代价之后
  4. 每项写明：看到什么，能分清哪两种解释
候选场景由规则的声明式定义自动枚举；代价必须由你评估，没评估的场景单列在最后，不参与排序。
区分力：二元规则 = 两条规则给“发生”的事前概率之差；多类别规则 = 各类别概率差的最大值（并指出是哪个类别）。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

from .engine import Rule
from .world import World

FACT_LABELS = {("cross_dept", True): "跨部门提案", ("cross_dept", False): "部门内提案",
               ("submitter", "user"): "我提交的", ("submitter", "someone_else"): "他人提交的"}
ORDER = ("submitter", "cross_dept")
MAX_SCENES = 16


@dataclass
class Scene:
    name: str
    facts: dict
    kind: Optional[str] = None  # observe：观察信号（代价 0）| ask：当面询问（代价由你评）| None：未评估
    cost: Optional[float] = None


def scene_name(facts: dict) -> str:
    keys = [k for k in ORDER if k in facts] + [k for k in sorted(facts) if k not in ORDER]
    return "".join(FACT_LABELS.get((k, facts[k]), f"{k}={facts[k]}") for k in keys) or "他的下一次相关互动"


def generate_scenes(rules: list) -> list:
    """从规则的声明式定义里枚举会改变预测的事实取值，做笛卡尔积（最多 MAX_SCENES 个）。"""
    values: dict = {}
    for r in rules:
        sp = r.spec or {}
        if sp.get("kind") == "fact_binary":
            vs = [True, False] if sp["test"] == "is_true" else [sp["equals"], "someone_else"]
            values.setdefault(sp["key"], vs)
    if not values:
        return [Scene(scene_name({}), {})]  # 常量规则：区分只取决于出现了哪个结果
    keys = sorted(values)
    out = []
    for combo in itertools.islice(itertools.product(*(values[k] for k in keys)), MAX_SCENES):
        f = dict(zip(keys, combo))
        out.append(Scene(scene_name(f), f))
    return out


def pair_power(a: Rule, b: Rule, facts: dict) -> Optional[tuple]:
    """(区分力, 说明)。未知（None）返回 None。"""
    va, vb = a.value(facts), b.value(facts)
    if va is None or vb is None:
        return None
    if isinstance(va, dict):
        cat = max(va, key=lambda k: abs(va[k] - vb[k]))
        return round(abs(va[cat] - vb[cat]), 10), {"category": cat, "pa": va[cat], "pb": vb[cat]}
    return round(abs(va - vb), 10), {"pa": va, "pb": vb}


def _text(sc: Scene, a: Rule, b: Rule, power: float, d: dict, what: Optional[str] = None) -> str:
    if "category" in d:
        see = f"看到：{sc.name}，他的首个结果是否落在“{d['category']}”"
        sep = f"“{a.name}”给这一类 {d['pa']:.0%}，“{b.name}”给 {d['pb']:.0%}"
    else:
        see = f"看到：{sc.name}，是否发生“{what}”" if what else f"看到：{sc.name}，他是否发生该行为"
        sep = f"“{a.name}”预测 {d['pa']:.0%}，“{b.name}”预测 {d['pb']:.0%}"
    return f"{see}；能分清“{a.name}”与“{b.name}”（{sep}，相差 {power:.0%}）"


def rank_scenes(scenes: list, rules: list, threshold: float = 0.3, what: Optional[str] = None) -> list:
    entries = []
    for sc in scenes:
        best = None
        for a, b in itertools.combinations(rules, 2):
            pp = pair_power(a, b, sc.facts)
            if pp and (best is None or pp[0] > best[0]):
                best = (pp[0], a, b, pp[1])
        if best is None or best[0] + 1e-9 < threshold:
            continue  # 1. 区分力不足阈值，直接筛掉
        power, a, b, d = best
        if sc.kind == "observe":
            status, score = "ranked", power
        elif sc.kind == "ask" and sc.cost:
            status, score = "ranked", round(power / sc.cost, 10)
        else:
            status, score = "unrated", power  # 没有评估代价：不参与排序
        entries.append({"name": sc.name, "facts": sc.facts, "kind": {"observe": "观察信号", "ask": "当面询问"}.get(sc.kind),
                        "cost": 0 if sc.kind == "observe" else sc.cost, "power": power, "score": score, "status": status,
                        "separates": (a.name, b.name), "text": _text(sc, a, b, power, d, what)})
    zero = sorted([e for e in entries if e["status"] == "ranked" and e["kind"] == "观察信号"], key=lambda e: (-e["power"], e["name"]))
    pos = sorted([e for e in entries if e["status"] == "ranked" and e["kind"] == "当面询问"], key=lambda e: (-e["score"], e["name"]))
    unrated = sorted([e for e in entries if e["status"] == "unrated"], key=lambda e: (-e["power"], e["name"]))
    return zero + pos + unrated  # 零代价 → 正代价 → 待评估


# ───────── 与 World 对接 ─────────
def person_rules(w: World, pid: str) -> list:
    ids = []
    for p in w.engine.preds.values():
        if p.pre.person_id == pid:
            ids += [r for r in p.rule_snaps if r not in ids]
    ids += [h.id for h in w.hypotheses.values() if h.person_id == pid and h.formal and h.id in w.engine.rules and h.id not in ids]
    return [w.engine.rules[i] for i in ids]


def discriminating_signals(w: World, pid: str, threshold: float = 0.3) -> list:
    """该人物当前规则之间，下一步最值得观察的场景（已按 3.9 排序，含看到什么/能分清什么）。"""
    rules = [r for r in person_rules(w, pid) if r.spec is not None]
    if len(rules) < 2:
        return []
    scenes = generate_scenes(rules)
    for sc in scenes:
        rating = w.scene_ratings.get((pid, sc.name))
        if rating:
            sc.kind, sc.cost = rating
    versions = [v for (k, v) in w.engine.behaviors if k == rules[0].behavior_key]
    beh = w.engine.behaviors[(rules[0].behavior_key, max(versions))] if versions else None
    return rank_scenes(scenes, rules, threshold, what=beh.description if beh and beh.outcome_type == "binary" else None)
