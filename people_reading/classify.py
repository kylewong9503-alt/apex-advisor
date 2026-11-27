"""分类层（3.10）：自动计算象限。手动设置优先；自动结果只写在 quadrant_auto。

closeness（近/远）：只用全局 interactional edge 与互动频率，不受某个议题里临时角色（matter_role / matter_edge）的影响。
depth（深/浅）：该人物的有效证据量与预测表现。事后回忆材料算半份证据；因事实更正而重算的预测不计入表现。
阈值与权重都是可调的默认值，计算过程完整给出（可解释），不下结论。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .world import SELF, World

CLOSE_THRESHOLD = 0.4
DEPTH_THRESHOLD = 0.5
RECENT_DAYS = 30
FULL_FREQ = 6  # 窗口内互动达到这个次数，频率分满
FULL_EVIDENCE = 8  # 有效证据达到这个量，证据分满
INTERACTIONAL = ("depends_on", "trusts", "info_channel", "owes_commitment")


def _interactions_of(w: World, pid: str) -> dict:
    """与该人物相关的互动：{iid: day}。只用互动事实（观察、机会、会谈），不看议题角色。"""
    ids = set()
    for o in w.observations.values():
        if pid in (o.subject_person_id, o.reporter_id) or pid in o.involved:
            ids.add(o.interaction_id)
    for s in w.stat_opps.values():
        if pid in (s.actor_id, s.target_id):
            ids.add(s.interaction_id)
    for iid, meta in w.interactions.items():
        if meta.get("with") == pid and meta.get("status") != "planned":
            ids.add(iid)
    return {i: (w.interactions.get(i, {}).get("day") or 0) for i in ids}


def closeness(w: World, pid: str, today: Optional[int] = None) -> dict:
    edges = [e for e in w.edges.values() if e.kind == "interactional" and e.status == "confirmed" and e.valid_to is None
             and e.type in INTERACTIONAL and {e.from_person_id, e.to_person_id} == {SELF, pid}]
    weight = sum(float(e.attributes.get("degree", 3)) / 5 for e in edges)
    edge_score = min(weight, 2.0) / 2.0
    days = _interactions_of(w, pid)
    ref = today if today is not None else max([d for d in (m.get("day") or 0 for m in w.interactions.values())] + [0])
    recent = [i for i, d in days.items() if d and ref - RECENT_DAYS < d <= ref]  # 没有日期的互动不算“近期”
    freq_score = min(len(recent) / FULL_FREQ, 1.0)
    return {"score": round(0.5 * edge_score + 0.5 * freq_score, 4), "edge_score": round(edge_score, 4),
            "freq_score": round(freq_score, 4), "n_edges": len(edges), "n_recent": len(recent), "ref_day": ref}


def depth(w: World, pid: str) -> dict:
    by_int: dict = {}
    for o in w.observations.values():
        if o.subject_person_id == pid:
            by_int.setdefault(o.interaction_id, []).append(o)
    inter = sum(0.5 if all(x.source_mode == "recalled" for x in obs) else 1.0 for obs in by_int.values())
    counted = 0
    for k, row in w.engine.rows.items():
        if row.status == "counted" and any(p.pre.person_id == pid and k[2] in p.rule_snaps for p in w.engine.preds.values()):
            counted += 1
    eff = inter + counted
    evidence_score = min(eff / FULL_EVIDENCE, 1.0)
    ratios = [p.st.brier / p.st.brier_baseline for p in w.engine.preds.values()
              if p.pre.person_id == pid and p.st.status == "settled" and not p.st.lock_facts_corrected and p.st.brier_baseline]
    n = len(ratios)
    perf = 0.0 if not n else max(0.0, 1.0 - sum(ratios) / n) * (n / (n + 2))  # 样本少时向 0 收缩
    return {"score": round(0.6 * evidence_score + 0.4 * perf, 4), "effective_evidence": round(eff, 2),
            "evidence_score": round(evidence_score, 4), "performance_score": round(perf, 4), "n_settled": n}


@dataclass
class Suggestion:
    person_id: str
    quadrant: str
    closeness: dict
    depth: dict

    def explain(self) -> str:
        c, d = self.closeness, self.depth
        return (f"关系{'近' if self.quadrant.startswith('近') else '远'}：全局互动关系边 {c['n_edges']} 条，"
                f"近 {RECENT_DAYS} 天互动 {c['n_recent']} 次（得分 {c['score']}，门槛 {CLOSE_THRESHOLD}）；"
                f"了解{'深' if self.quadrant.endswith('深') else '浅'}：有效证据 {d['effective_evidence']}，"
                f"已结算预测 {d['n_settled']} 份（得分 {d['score']}，门槛 {DEPTH_THRESHOLD}）")


def suggest_quadrant(w: World, pid: str, today: Optional[int] = None) -> Suggestion:
    c, d = closeness(w, pid, today), depth(w, pid)
    q = ("近" if c["score"] >= CLOSE_THRESHOLD else "远") + "-" + ("深" if d["score"] >= DEPTH_THRESHOLD else "浅")
    return Suggestion(pid, q, c, d)


def refresh_quadrants(w: World, today: Optional[int] = None) -> list:
    """重算所有人物的 quadrant_auto。手动设置优先：有手动值的人只更新“系统建议”，不改变其有效象限。
    有效象限变化时触发迁移规则。返回变化清单。"""
    changes = []
    for p in w.persons.values():
        if p.is_self or p.is_placeholder or p.merged_into_id:
            continue
        sug = suggest_quadrant(w, p.id, today)
        old_eff = p.quadrant
        p.quadrant_auto = sug.quadrant
        if p.quadrant != old_eff:
            w.apply_quadrant_change(p.id, old_eff, p.quadrant)
            changes.append({"person": p.id, "before": old_eff, "after": p.quadrant, "why": sug.explain()})
    return changes
