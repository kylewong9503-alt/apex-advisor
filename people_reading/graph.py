"""议题关系小图（6.3，按议题组织）。视图模型 + 内联 SVG。

R8：节点上的角色来自 matter_role，连线来自 matter_edge；“我→引荐人：需其引荐”只是对 matter_role(referrer)
的展示，不写成存储的边。已核实为实线，提议中为虚线并标出提出者；已否定的默认隐藏。
没有原话指向具体人时，不画人与人之间的连线。
"""
from __future__ import annotations

import html
import math

from .privacy import NO_CAPTURE_META, Redactor
from .profile import PROPOSER, ROLE_LABEL
from .world import SELF, World

EDGE_LABEL = {"can_refer": "可能引荐", "needs_approval_of": "需其批准", "influences": "影响", "reports_in_matter": "向其汇报"}


def _quote(store, span) -> str:
    if isinstance(span, dict) and store is not None and "start" in span:
        return store.quote(span)
    return str(span)


def _label_parts(roles: list) -> list:
    """节点标签里“人名之后”的部分：will/can 表述；引荐人缺 can 时写“渠道未知”；都没有则写角色名。"""
    parts = [r.statement for r in roles if r.dimension in ("will", "can") and r.statement]
    if any(r.role == "referrer" for r in roles) and not any(r.dimension == "can" for r in roles):
        parts.append("渠道未知")
    return parts or [ROLE_LABEL.get(r.role, r.role) for r in roles]


def build_matter_graph(w: World, matter_id: str, store=None, rd=None) -> dict:
    rd = rd or Redactor(w, "plain")
    roles = [r for r in w.roles.values() if r.matter_id == matter_id and r.status != "refuted"]
    medges = [e for e in w.matter_edges.values() if e.matter_id == matter_id and e.status != "refuted"]
    pids = {SELF} | {r.person_id for r in roles} | {e.from_person_id for e in medges} | {e.to_person_id for e in medges}
    name = rd.p
    nodes = []
    for p in sorted(pids):
        mine = [r for r in roles if r.person_id == p and not (r.proposed_by == "model" and r.dimension is None)]
        alts = [r for r in roles if r.person_id == p and r.proposed_by == "model" and r.dimension is None]
        basis = [rd.quote(_quote(store, ev["span"])) for r in mine for ev in r.evidence_refs if isinstance(ev, dict) and "span" in ev]
        signals = [{"text": rd.t(f.text), "kind": "观察信号", "cost": 0} for f in w.followups
                   if f.kind == "watch" and matter_id in f.target_ref and p in f.target_ref]
        label = rd.p(p) if p == SELF else " · ".join([rd.p(p)] + [rd.t(x) for x in _label_parts(mine)])
        nodes.append({"id": p, "label": label,
                      "expand": {"basis": basis, "alternatives": [{"text": rd.t(a.statement), "proposed_by": PROPOSER.get(a.proposed_by)} for a in alts],
                                 "signals": signals,
                                 "who": sorted({PROPOSER.get(r.proposed_by, r.proposed_by) for r in mine})}})
    edges = []
    for r in roles:  # 展示：我 → 引荐人 “需其引荐”
        if r.role == "referrer" and r.person_id != SELF:
            # 已核实才是实线：用户说“希望他引荐”只是意愿，不算核实；can 或 will 维度被确认才画实线
            solid = any(x.status == "confirmed" and x.dimension in ("can", "will")
                        for x in roles if x.person_id == r.person_id and x.role == "referrer")
            edges.append({"from": SELF, "to": r.person_id, "label": rd.rel("需其引荐"), "style": "solid" if solid else "dashed",
                          "proposer": PROPOSER.get(r.proposed_by), "source": "matter_role"})
    for e in medges:
        edges.append({"from": e.from_person_id, "to": e.to_person_id, "label": rd.rel(EDGE_LABEL.get(e.type, e.type)),
                      "style": "solid" if e.status == "confirmed" else "dashed", "proposer": PROPOSER.get(e.proposed_by),
                      "source": "matter_edge"})
    return {"matter": rd.m(w.matters[matter_id].title), "nodes": nodes, "edges": edges}


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


# ───────── 组织结构（全局视图）：只显示 position、unit_link 和 structural 边 ─────────
LINK_LABEL = {"solid_report": ("实线汇报", "solid"), "dotted_report": ("虚线汇报", "dashed"), "support": ("支持", "dotted")}
STRUCT_LABEL = {"direct_report": "直属汇报", "dotted_report": "虚线汇报"}


def build_org_graph(w: World, rd=None) -> dict:
    rd = rd or Redactor(w, "plain")
    units = []
    for uid, u in w.units.items():
        ppl = [{"id": pos.person_id, "label": f"{rd.p(pos.person_id)} · {rd.t(pos.title)}", "head": pos.is_unit_head}
               for pos in w.positions.values() if pos.org_unit_id == uid and pos.valid_to is None]
        units.append({"id": uid, "name": rd.t(u.name), "persons": ppl})
    links = [{"from": ul.from_unit_id, "to": ul.to_unit_id, "label": rd.rel(LINK_LABEL[ul.type][0]),
              "style": LINK_LABEL[ul.type][1], "type": ul.type}
             for ul in w.unit_links.values() if ul.valid_to is None]
    struct = [{"from": e.from_person_id, "to": e.to_person_id, "label": rd.rel(STRUCT_LABEL.get(e.type, e.type)),
               "text": f"{rd.p(e.from_person_id)} → {rd.p(e.to_person_id)}：{rd.rel(STRUCT_LABEL.get(e.type, e.type))}",
               "style": "solid" if e.type == "direct_report" else "dashed", "proposer": PROPOSER.get(e.proposed_by)}
              for e in w.edges.values() if e.kind == "structural" and e.status != "refuted" and e.valid_to is None]
    return {"units": units, "unit_links": links, "structural": struct,
            "scope": "只显示职务、单元关系和结构关系；议题内角色与互动关系不在此图"}


def render_org_svg(g: dict, width: int = 640) -> str:
    e = html.escape
    n = max(len(g["units"]), 1)
    bw = min(190, (width - 20) / n - 14)
    ux = {u["id"]: 10 + i * (bw + 14) for i, u in enumerate(g["units"])}
    height = 60 + max((len(u["persons"]) for u in g["units"]), default=1) * 26 + 60
    ppos = {}
    out = [f"<svg viewBox='0 0 {width} {height}' width='100%' role='img' aria-label='组织结构'>",
           "<defs><marker id='ao' markerWidth='8' markerHeight='8' refX='7' refY='4' orient='auto'><path d='M0,0 L8,4 L0,8 z' fill='currentColor'/></marker></defs>"]
    for u in g["units"]:
        x = ux[u["id"]]
        out.append(f"<rect x='{x:.0f}' y='20' width='{bw:.0f}' height='{height - 60}' rx='8' fill='none' stroke='currentColor' stroke-dasharray='2 3'/>"
                   f"<text x='{x + 8:.0f}' y='38' font-size='12' font-weight='bold' fill='currentColor'>{e(u['name'])}</text>")
        for i, p in enumerate(u["persons"]):
            py = 62 + i * 26
            ppos[p["id"]] = (x + bw / 2, py)
            out.append(f"<rect x='{x + 8:.0f}' y='{py - 14}' width='{bw - 16:.0f}' height='22' rx='4' fill='none' stroke='currentColor'"
                       f"{' stroke-width=\'2\'' if p['head'] else ''}/><text x='{x + bw / 2:.0f}' y='{py + 1}' font-size='10.5' text-anchor='middle' fill='currentColor'>{e(p['label'])}</text>")
    for l in g["unit_links"]:
        if l["from"] in ux and l["to"] in ux:
            x1, x2 = ux[l["from"]] + bw / 2, ux[l["to"]] + bw / 2
            dash = {"dashed": " stroke-dasharray='6 4'", "dotted": " stroke-dasharray='2 3'"}.get(l["style"], "")
            y = height - 22
            out.append(f"<path d='M{x1:.0f},{height - 40} Q{(x1 + x2) / 2:.0f},{y + 20} {x2:.0f},{height - 40}' fill='none' stroke='currentColor'{dash} marker-end='url(#ao)'/>"
                       f"<text x='{(x1 + x2) / 2:.0f}' y='{y + 8}' font-size='11' text-anchor='middle' fill='currentColor'>{e(l['label'])}</text>")
    for s_ in g["structural"]:
        if s_["from"] in ppos and s_["to"] in ppos:
            (x1, y1), (x2, y2) = ppos[s_["from"]], ppos[s_["to"]]
            x1, x2 = x1 - bw / 2 + 14, x2 - bw / 2 + 14  # 画在人物框左缘，不压住框里的字
            dash = " stroke-dasharray='6 4'" if s_["style"] == "dashed" else ""
            out.append(f"<line x1='{x1:.0f}' y1='{y1 - 10}' x2='{x2:.0f}' y2='{y2 - 10}' stroke='currentColor'{dash} marker-end='url(#ao)'/>")
    out.append("</svg>")
    return "".join(out)


def org_legend(g: dict) -> str:
    """结构关系单列在图下方（线上不写字，避免和同单元里挨着的人物框重叠）。"""
    items = "".join(f"<li>{html.escape(s['text'])}（{'实线' if s['style'] == 'solid' else '虚线'}）</li>" for s in g["structural"])
    return f"<ul class='mut'>{items}</ul>" if items else ""


# ───────── 关系图浏览：议题切换 + 组织结构 ─────────
def graph_views(w: World, rd=None) -> list:
    rd = rd or Redactor(w, "plain")
    return [{"id": "org", "title": "组织结构"}] + [{"id": m.id, "title": rd.m(m.title)} for m in w.matters.values()]


def render_graph_browser(w: World, store=None, rd=None) -> str:
    """每个视图一个面板（面板带 data-sub，外壳里的脚本负责切换；没有脚本时依次全部显示）。
    整个关系图页带 data-no-capture：原生外壳据此启用系统级禁截屏。"""
    rd = rd or Redactor(w, "plain")
    e = html.escape
    views = graph_views(w, rd)
    tabs = "".join(f"<button class='tab' data-tab='{e(v['id'])}'>{e(v['title'])}</button>" for v in views)
    panels = [f"<section class='gpanel' data-sub='org'><h3>组织结构</h3><p class='mut'>{e(build_org_graph(w, rd)['scope'])}</p>"
              f"{render_org_svg(build_org_graph(w, rd))}{org_legend(build_org_graph(w, rd))}</section>"]
    for v in views[1:]:
        g = build_matter_graph(w, v["id"], store, rd)
        panels.append(f"<section class='gpanel' data-sub='{e(v['id'])}'><h3>{e(g['matter'])}</h3>{render_svg(g)}</section>")
    return f"<div data-no-capture='graph'><div class='tabs'>{tabs}</div>{''.join(panels)}</div>"
