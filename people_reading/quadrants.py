"""人物档案首页：四象限（6.2）。横轴关系远近，纵轴了解深浅；MVP 由用户手动设置；象限内按影响力排序。
人物卡：代号｜正式职务｜最近活跃议题中的角色（带议题名）｜一条值得关注的信息。
"""
from __future__ import annotations

import html
from typing import Optional

from .privacy import Redactor
from .profile import ROLE_LABEL
from .stats import MIN_KNOWN, dim_a, known_opps
from .world import SELF, World

E = html.escape
LAYOUT = (("近-深", "远-深"), ("近-浅", "远-浅"))  # 上行：了解深；下行：了解浅
UNSET = "未分类"
NOTE = "象限由你手动设置"
NOTE_AUTO = "手动设置的优先；没有手动设置的，由系统按互动关系与证据自动计算（标“自动”）"


def role_summary(w: World, pid: str) -> Optional[str]:
    """最近活跃议题中的角色：议题名：表述（渠道未知）。can 与 will 分开，不互相推导（R8）。"""
    mine = [r for r in w.roles.values() if r.person_id == pid and r.status != "refuted"
            and not (r.proposed_by == "model" and r.dimension is None)]
    if not mine:
        return None
    last = max(mine, key=lambda r: (r.valid_from, r.id))
    same = [r for r in mine if r.matter_id == last.matter_id]
    parts = [r.statement for r in same if r.dimension in ("will", "can") and r.statement]
    label = ROLE_LABEL.get(last.role, last.role)
    unknown_can = any(r.role == "referrer" for r in same) and not any(r.dimension == "can" for r in same)
    if parts:
        text = "；".join(parts) + ("（渠道未知）" if unknown_can else "")
    else:
        text = label + ("（渠道未知）" if unknown_can else "")
    title = w.matters[last.matter_id].title if last.matter_id in w.matters else last.matter_id
    return f"{title}：{text}"


def notable(w: World, pid: str) -> Optional[str]:
    """一条值得关注的信息，按优先级：依据已变化的解读 → 待办/观察项 → 观察刚达到门槛 → 样本不足。"""
    for iid, d in w.interpretations.items():
        if pid in d.get("person_ids", []) and w.interpretation_changed(iid):
            return f"解读 {iid} 的依据已变化"
    opp_ids = {s.id for s in w.stat_opps.values() if s.actor_id == pid}
    for f in w.followups:
        if f.status in ("open", "surfaced") and f.kind in ("watch", "fill_unknown", "verify_role") and \
                (f.target_ref in opp_ids or pid in f.target_ref.split(":")):
            return f.text
    if known_opps(w, pid):
        a = dim_a(w, pid)
        for side, label in (("peers", "同级"), ("me", "我")):
            if a[side]["known"] == MIN_KNOWN:
                return f"对{label}的观察刚达到 {MIN_KNOWN} 次"
        if a["state"] == "待区分":
            return a["text"].replace("待区分：", "")
    return None


def build_quadrants(w: World, rd: Optional[Redactor] = None, auto: bool = False) -> dict:
    """auto=False：MVP 口径，只看手动设置。auto=True：手动优先，其余用系统计算的 quadrant_auto。"""
    rd = rd or Redactor(w, "code")
    cards = {q: [] for q in (*(c for row in LAYOUT for c in row), UNSET)}
    for p in w.persons.values():
        if p.is_self or p.is_placeholder or p.merged_into_id:
            continue
        pos = w.current_position(p.id)
        card = {"id": p.id, "ref": rd.ref(p.id), "code": rd.p(p.id), "title": rd.t(pos.title) if pos else rd.t("职务未知"),
                "role": rd.t(role_summary(w, p.id) or "暂无议题角色"), "note": rd.t(notable(w, p.id) or ""),
                "influence": p.influence}
        q = p.quadrant if auto else p.quadrant_manual
        if auto:
            card["source"] = p.quadrant_source
            if p.quadrant_manual and p.quadrant_auto and p.quadrant_auto != p.quadrant_manual:
                card["suggestion"] = p.quadrant_auto  # 手动与系统建议不一致：提示，不覆盖
        cards[q if q in cards else UNSET].append(card)
    for lst in cards.values():
        lst.sort(key=lambda c: (-c["influence"], c["code"]))
    ph = sum(1 for p in w.persons.values() if p.is_placeholder and not p.merged_into_id)
    return {"layout": [list(r) for r in LAYOUT], "cards": cards, "note": NOTE_AUTO if auto else NOTE, "placeholders": ph,
            "axes": {"x": "关系远近（近 ← → 远）", "y": "了解深浅（深 ↑ ↓ 浅）"}}


def card_html(c: dict) -> str:
    tag = " <span class='mut'>（自动）</span>" if c.get("source") == "auto" else ""
    sug = f"<div class='mut'>系统建议：{E(c['suggestion'])}</div>" if c.get("suggestion") else ""
    return (f"<a class='pcard' href='#/person/{E(c['ref'])}' data-person='{E(c['ref'])}'><b>{E(c['code'])}</b>{tag}｜{E(c['title'])}｜{E(c['role'])}"
            + (f"<div class='mut'>{E(c['note'])}</div>" if c["note"] else "") + sug + "</a>")


def render_quadrants(q: dict) -> str:
    cell = lambda name: (f"<div class='quad'><h3>{E(name)}</h3>" + ("".join(card_html(c) for c in q["cards"][name])  # noqa: E731
                                                                      or "<p class='mut'>暂无</p>") + "</div>")
    grid = "".join(cell(n) for row in q["layout"] for n in row)
    tray = ""
    if q["cards"][UNSET]:
        tray = "<div class='quad wide'><h3>未分类</h3>" + "".join(card_html(c) for c in q["cards"][UNSET]) + "</div>"
    ph = f"<p class='mut'>另有 {q['placeholders']} 个未具名的占位人物</p>" if q["placeholders"] else ""
    return (f"<p class='mut'>{E(q['note'])}。横轴：{E(q['axes']['x'])}；纵轴：{E(q['axes']['y'])}；象限内按影响力排序。</p>"
            f"<div class='quads'>{grid}</div>{tray}{ph}")
