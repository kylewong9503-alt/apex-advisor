"""发生率、三维对照（3.6）、观察排序（3.9）。数字全部由程序计算（R7）。"""
from __future__ import annotations

from typing import Optional

from .world import SELF, World

MIN_KNOWN = 3


def known_opps(w: World, actor: str, *, behavior_key: str = "ask_owner", responsible_named: bool = False):
    """条件：responsible_named = 指定值。字段为 null 的机会不进入统计（并已在 followup 中生成补充条件）。"""
    return [s for s in w.stat_opps.values()
            if s.actor_id == actor and s.behavior_key == behavior_key and s.responsible_named is responsible_named]


def rate(w: World, opps: list) -> dict:
    t = sum(1 for s in opps if w.occurred(s.id) is True)
    f = sum(1 for s in opps if w.occurred(s.id) is False)
    n = sum(1 for s in opps if w.occurred(s.id) is None)
    return {"true": t, "false": f, "null": n, "known": t + f, "rate": (t / (t + f)) if t + f else None,
            "text": f"{t}/{t + f}"}


def _grade(w: World, pid: str) -> Optional[int]:
    pos = w.current_position(pid)
    return pos.grade_level if pos else None


def peers_of_user(w: World) -> set:
    g = _grade(w, SELF)
    return {p for p in w.persons if p != SELF and not w.persons[p].is_placeholder and g is not None and _grade(w, p) == g}


def _cross_share(opps: list) -> str:
    c = sum(1 for s in opps if s.cross_dept)
    return f"{c}/{len(opps)}"


def dim_a(w: World, actor: str) -> dict:
    """A 维：X 对我 vs X 对我的同级他人（更高级别如 S 不计入）。"""
    mine = [s for s in known_opps(w, actor) if s.target_id == SELF and w.occurred(s.id) is not None]
    peers = peers_of_user(w)
    theirs = [s for s in known_opps(w, actor) if s.target_id in peers and w.occurred(s.id) is not None]
    a, b = rate(w, mine), rate(w, theirs)
    out = {"dimension": "A", "me": a, "peers": b}
    short = [n for n, r in (("我", a), ("同级", b)) if r["known"] < MIN_KNOWN]
    if short:
        out.update(state="待区分", text="待区分：" + "；".join(
            f"{n}一侧仅 {(a if n == '我' else b)['known']} 次已知机会" for n in short))
    else:
        out.update(state="开放比较", text=f"X 对我 {a['text']}，X 对同级 {b['text']}（开放比较，不自动归因）",
                   unmatched={"跨部门占比": {"我": _cross_share(mine), "同级": _cross_share(theirs)}})
    return out


def dim_b(w: World, actor: str) -> dict:
    """B 维：X 对我 vs 其他人对我。"""
    x = rate(w, [s for s in known_opps(w, actor) if s.target_id == SELF])
    others = [s for s in w.stat_opps.values() if s.actor_id != actor and s.target_id == SELF and s.responsible_named is False
              and s.behavior_key == "ask_owner"]
    o = rate(w, others)
    if o["known"] < MIN_KNOWN:
        return {"dimension": "B", "x_to_me": x, "others_to_me": o, "state": "待区分",
                "text": f"X 对我 {x['text']}；其他人对我 {o['known']} 次已知机会；待区分"}
    return {"dimension": "B", "x_to_me": x, "others_to_me": o, "state": "开放比较",
            "text": f"X 对我 {x['text']}，其他人对我 {o['text']}（开放比较，不自动归因）"}


def dim_c(w: World, actor: str, recent_from_day: int) -> dict:
    """C 维：X 近期 vs X 过去。"""
    allo = [s for s in known_opps(w, actor) if w.occurred(s.id) is not None]
    rec, past = rate(w, [s for s in allo if s.day >= recent_from_day]), rate(w, [s for s in allo if s.day < recent_from_day])
    short = [n for n, r in (("近期", rec), ("过去", past)) if r["known"] < MIN_KNOWN]
    state = "待区分" if short else "开放比较"
    text = ("待区分：" + "；".join(f"{n}一侧仅 {(rec if n == '近期' else past)['known']} 次已知机会" for n in short)) if short \
        else f"X 近期 {rec['text']}，过去 {past['text']}（开放比较，不自动归因）"
    return {"dimension": "C", "recent": rec, "past": past, "state": state, "text": text}


# ───────── 观察排序（3.9） ─────────
def discrimination(rules: list, facts: dict) -> tuple:
    """返回 (最大区分力, [达到区分力的规则对])。区分力 = 两条规则给“发生”的事前概率之差。"""
    best, pairs = 0.0, []
    vals = [(r.id, r.value(facts)) for r in rules]
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            if vals[i][1] is None or vals[j][1] is None:
                continue
            d = round(abs(vals[i][1] - vals[j][1]), 10)
            if d > best:
                best = d
            pairs.append((vals[i][0], vals[j][0], d))
    return best, pairs


def rank_observation_scenes(scenes: list, rules: list, threshold: float = 0.3) -> list:
    """scenes: [{name, facts, cost}]。先筛区分力不足阈值的；零代价按区分力排；正代价按区分力/代价排在零代价之后。"""
    scored = []
    for sc in scenes:
        power, pairs = discrimination(rules, sc["facts"])
        if power + 1e-9 < threshold:
            continue
        top = [(a, b) for a, b, d in pairs if abs(d - power) < 1e-9]
        scored.append({**sc, "power": power, "separates": top,
                       "score": power if sc["cost"] == 0 else round(power / sc["cost"], 10)})
    zero = sorted([s for s in scored if s["cost"] == 0], key=lambda s: (-s["power"], s["name"]))
    pos = sorted([s for s in scored if s["cost"] > 0], key=lambda s: (-s["score"], s["name"]))
    return zero + pos
