"""议题关系小图（6.3，按议题组织）。视图模型 + 内联 SVG。

R8：节点上的角色来自 matter_role，连线来自 matter_edge；“我→引荐人：需其引荐”只是对 matter_role(referrer)
的展示，不写成存储的边。已核实为实线，提议中为虚线并标出提出者；已否定的默认隐藏。
没有原话指向具体人时，不画人与人之间的连线。
"""
from __future__ import annotations

import html
import math

from .profile import PROPOSER, ROLE_LABEL
from .world import SELF, World

EDGE_LABEL = {"can_refer": "可能引荐", "needs_approval_of": "需其批准", "influences": "影响", "reports_in_matter": "向其汇报"}


def _quote(store, span) -> str:
    if isinstance(span, dict) and store is not None and "start" in span:
        return store.quote(span)
    return str(span)


def _node_label(name: str, roles: list) -> str:
    parts = []
    for r in roles:
        if r.dimension == "will" and r.statement:
            parts.append(r.statement)
        elif r.dimension == "can" and r.statement:
            parts.append(r.statement)
    if any(r.role == "referrer" for r in roles) and not any(r.dimension == "can" for r in roles):
        parts.append("渠道未知")
    if not parts:
        parts = [ROLE_LABEL.get(r.role, r.role) for r in roles]
    return " · ".join([name] + parts)


def build_matter_graph(w: World, matter_id: str, store=None) -> dict:
    roles = [r for r in w.roles.values() if r.matter_id == matter_id and r.status != "refuted"]
    medges = [e for e in w.matter_edges.values() if e.matter_id == matter_id and e.status != "refuted"]
    pids = {SELF} | {r.person_id for r in roles} | {e.from_person_id for e in medges} | {e.to_person_id for e in medges}
    name = lambda p: w.persons[p].display_name if p in w.persons else p  # noqa: E731
    nodes = []
    for p in sorted(pids):
        mine = [r for r in roles if r.person_id == p and not (r.proposed_by == "model" and r.dimension is None)]
        alts = [r for r in roles if r.person_id == p and r.proposed_by == "model" and r.dimension is None]
        basis = [_quote(store, ev["span"]) for r in mine for ev in r.evidence_refs if isinstance(ev, dict) and "span" in ev]
        signals = [{"text": f.text, "kind": "观察信号", "cost": 0} for f in w.followups
                   if f.kind == "watch" and matter_id in f.target_ref and p in f.target_ref]
        nodes.append({"id": p, "label": name(p) if p == SELF else _node_label(name(p), mine),
                      "expand": {"basis": basis, "alternatives": [{"text": a.statement, "proposed_by": PROPOSER.get(a.proposed_by)} for a in alts],
                                 "signals": signals,
                                 "who": sorted({PROPOSER.get(r.proposed_by, r.proposed_by) for r in mine})}})
    edges = []
    for r in roles:  # 展示：我 → 引荐人 “需其引荐”
        if r.role == "referrer" and r.person_id != SELF:
            # 已核实才是实线：用户说“希望他引荐”只是意愿，不算核实；can 或 will 维度被确认才画实线
            solid = any(x.status == "confirmed" and x.dimension in ("can", "will")
                        for x in roles if x.person_id == r.person_id and x.role == "referrer")
            edges.append({"from": SELF, "to": r.person_id, "label": "需其引荐", "style": "solid" if solid else "dashed",
                          "proposer": PROPOSER.get(r.proposed_by), "source": "matter_role"})
    for e in medges:
        edges.append({"from": e.from_person_id, "to": e.to_person_id, "label": EDGE_LABEL.get(e.type, e.type),
                      "style": "solid" if e.status == "confirmed" else "dashed", "proposer": PROPOSER.get(e.proposed_by),
                      "source": "matter_edge"})
    return {"matter": w.matters[matter_id].title, "nodes": nodes, "edges": edges}


def render_svg(g: dict, width: int = 640) -> str:
    n = len(g["nodes"])
    cx, cy, rad = width / 2, 150, 110
    pos = {nd["id"]: (cx + rad * math.cos(2 * math.pi * i / max(n, 1) - math.pi / 2),
                      cy + rad * math.sin(2 * math.pi * i / max(n, 1) - math.pi / 2)) for i, nd in enumerate(g["nodes"])}
    e = html.escape
    out = [f"<svg viewBox='0 0 {width} 320' width='100%' role='img' aria-label='{e(g['matter'])} 关系图'>",
           "<defs><marker id='ar' markerWidth='8' markerHeight='8' refX='7' refY='4' orient='auto'><path d='M0,0 L8,4 L0,8 z' fill='currentColor'/></marker></defs>"]
    for ed in g["edges"]:
        (x1, y1), (x2, y2) = pos[ed["from"]], pos[ed["to"]]
        dash = " stroke-dasharray='6 4'" if ed["style"] == "dashed" else ""
        out.append(f"<line x1='{x1:.0f}' y1='{y1:.0f}' x2='{x2:.0f}' y2='{y2:.0f}' stroke='currentColor'{dash} marker-end='url(#ar)'/>")
        out.append(f"<text x='{(x1 + x2) / 2:.0f}' y='{(y1 + y2) / 2 - 4:.0f}' font-size='12' text-anchor='middle' fill='currentColor'>{e(ed['label'])}</text>")
    for nd in g["nodes"]:
        x, y = pos[nd["id"]]
        out.append(f"<g><rect x='{x - 70:.0f}' y='{y - 15:.0f}' width='140' height='30' rx='6' fill='none' stroke='currentColor'/>"
                   f"<text x='{x:.0f}' y='{y + 4:.0f}' font-size='11' text-anchor='middle' fill='currentColor'>{e(nd['label'])}</text></g>")
    out.append("</svg>")
    det = []
    for nd in g["nodes"]:
        ex = nd["expand"]
        if not (ex["basis"] or ex["alternatives"] or ex["signals"]):
            continue
        det.append(f"<details><summary>{e(nd['label'])}</summary><ul>"
                   + "".join(f"<li>依据：{e(b)}</li>" for b in ex["basis"])
                   + "".join(f"<li>替代解释：{e(a['text'])}（{e(a['proposed_by'] or '')}）</li>" for a in ex["alternatives"])
                   + "".join(f"<li>区分信号：{e(s['text'])}（{s['kind']}，代价 {s['cost']}）</li>" for s in ex["signals"])
                   + "</ul></details>")
    return "".join(out) + "".join(det)
