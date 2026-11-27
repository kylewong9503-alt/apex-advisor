"""人物档案个人页（6.2）：视图模型 + 静态 HTML。三个底层问题分别回答，不互相推导（R8）：
  1 职务  2 和我的关系（结构关系 / 组织路径 / 互动关系三者分开）  3 各议题中的角色（can 与 will 分开）
判断卡此处只含“事前预测行”与三维对照；完整三行卡与两条时间线在第 4 步。
"""
from __future__ import annotations

import html
from typing import Optional

from .cards import prediction_row
from .stats import dim_a, dim_b, dim_c, known_opps
from .world import SELF, World

EDGE_LABEL = {"direct_report": "直属汇报", "dotted_report": "虚线汇报", "depends_on": "依赖", "trusts": "信任",
              "info_channel": "信息渠道", "owes_commitment": "承诺"}
ROLE_LABEL = {"approver": "审批人", "referrer": "引荐人", "influencer": "影响者", "blocker": "阻碍者",
              "informant": "信息提供者", "other": "其他"}
PROPOSER = {"user": "你", "model": "模型提出"}


def fmt_day(day) -> str:
    """真实日期（序数 ≥ 100000）显示为 ISO 日期；合成数据显示“第 N 天”；没有日期显示“日期未知”。"""
    import datetime
    if not day:
        return "日期未知"
    return datetime.date.fromordinal(day).isoformat() if day >= 100000 else f"第 {day} 天"


def _quote(store, span) -> str:
    if isinstance(span, dict) and store is not None and "start" in span:
        return store.quote(span)
    return str(span)


def build_profile(w: World, person_id: str, store=None) -> dict:
    p = w.persons[person_id]
    # 1 职务：当前 + 历史
    positions = [{"title": x.title, "unit": w.units[x.org_unit_id].name if x.org_unit_id in w.units else x.org_unit_id,
                  "grade": x.grade_level, "from": x.valid_from, "to": x.valid_to, "current": x.valid_to is None}
                 for x in sorted(w.positions.values(), key=lambda z: z.valid_from) if x.person_id == person_id]
    # 2 关系：三者分开
    v = w.relationship_view(person_id)
    relation = {
        "structural": [{"type": EDGE_LABEL.get(e.type, e.type), "direction": "我→他" if e.from_person_id == SELF else "他→我"}
                       for e in v["structural"]],
        "org_path": v["org_path"],
        "interactional": [{"type": EDGE_LABEL.get(e.type, e.type), "direction": "我→他" if e.from_person_id == SELF else "他→我",
                           "status": e.status, "proposed_by": PROPOSER.get(e.proposed_by, e.proposed_by),
                           "tier": e.attributes.get("source_tier", "")} for e in v["interactional"]]}
    # 3 议题角色：按议题，can / will 分开
    by_matter: dict = {}
    for r in w.roles.values():
        if r.person_id != person_id:
            continue
        m = by_matter.setdefault(r.matter_id, {"matter": w.matters[r.matter_id].title if r.matter_id in w.matters else r.matter_id,
                                               "roles": {}})
        slot = m["roles"].setdefault(ROLE_LABEL.get(r.role, r.role), {"can": "未知", "will": "未知", "entries": []})
        if r.dimension in ("can", "will"):
            slot[r.dimension] = f"{r.statement or '已记录'}（{r.status}）"
        slot["entries"].append({"dimension": r.dimension, "status": r.status, "proposed_by": PROPOSER.get(r.proposed_by, r.proposed_by)})
    # 4 判断卡
    cards = []
    pids = {pr.id for pr in w.engine.preds.values() if pr.pre.person_id == person_id}
    pairs = {}
    for (opp, bk, a, b), row in sorted(w.engine.rows.items()):
        if any(a in w.engine.preds[i].rule_snaps for i in pids):
            pairs.setdefault((a, b), []).append(opp)
    for (a, b), opps in pairs.items():
        cards.append({"pair": (w.engine.rules[a].name, w.engine.rules[b].name),
                      "text": prediction_row(w.engine, opps[-1], a, b), "cumulative": w.engine.cumulative(a, b)})
    dims = None
    if known_opps(w, person_id):
        days = [s.day for s in known_opps(w, person_id)]
        dims = {"A": dim_a(w, person_id)["text"], "B": dim_b(w, person_id)["text"],
                "C": dim_c(w, person_id, max(days) - 9)["text"]}
    # 5 互动时间线，可点回原话
    timeline = []
    for iid, it in sorted(w.interactions.items(), key=lambda kv: kv[1].get("day", 0)):
        obs = [o for o in w.observations.values() if o.interaction_id == iid and person_id in (o.subject_person_id, o.reporter_id)]
        if not obs:
            continue
        timeline.append({"interaction": iid, "scenes": list(it.get("scenes", [])), "label": it.get("label", ""), "day": it.get("day", 0), "day_label": fmt_day(it.get("day", 0)), "observations": [
            {"id": o.id, "kind": o.kind, "reporter": w.persons[o.reporter_id].display_name if o.reporter_id in w.persons else o.reporter_id,
             "content": o.content, "interpretation": o.is_interpretation,
             "mode": "当时记录" if o.source_mode == "contemporaneous" else "事后回忆",
             "quotes": [_quote(store, s) for s in o.source_spans]} for o in obs]})
    # 6 预测复盘与修订时间线
    rev = [{"id": r.id, "kind": r.kind, "subject": r.subject, "reason": r.reason}
           for r in w.engine.revisions if person_id in r.subject or any(
               w.engine.preds[i].pre.person_id == person_id for i in [r.prediction_id] if i in w.engine.preds)]
    rev += [{"id": r.id, "kind": r.kind, "subject": r.subject, "reason": r.reason}
            for r in w.revisions if person_id in r.subject or person_id in str(r.detail)]
    # 7 待核实
    opp_ids = {s.id for s in w.stat_opps.values() if s.actor_id == person_id}
    follow = [{"kind": f.kind, "text": f.text, "status": f.status} for f in w.followups
              if f.target_ref in opp_ids or person_id in f.target_ref]
    ledger = w.commitment_ledger(person_id)
    for rows in ledger.values():
        for r in rows:
            ob = {o.id: o for o in w.observations.values()}
            r["quotes"] = [_quote(store, sp) for oid in (r["evidence_obs"], r["closing_obs"]) if oid in ob for sp in ob[oid].source_spans]
    p_ = w.persons[person_id]
    interps = [{"id": i, "skill": d.get("skill_version_id"), "question": d["question"], "stale": w.interpretation_changed(i)}
               for i, d in w.interpretations.items() if person_id in d.get("person_ids", [])]
    return {"header": {"id": person_id, "name": p.display_name, "placeholder": p.is_placeholder,
                       "action": f"继续聊 {p.display_name}"},
            "positions": positions, "relation": relation, "matter_roles": list(by_matter.values()),
            "judgment_cards": cards, "dimensions": dims, "badge": w.evidence_badge(person_id), "timeline": timeline,
            "revisions": rev, "followups": follow, "interpretations": interps, "commitments": ledger,
            "quadrant": {"effective": p_.quadrant, "source": p_.quadrant_source, "manual": p_.quadrant_manual,
                         "auto": p_.quadrant_auto}}


def render_html(pf: dict, rd=None) -> str:
    """rd：Redactor。所有动态字符串都经过它（遮挡时不输出原文）；固定的栏目标题不是敏感内容。"""
    T = rd.t if rd is not None else (lambda x: x)
    e = lambda x: html.escape(T(str(x)))  # noqa: E731

    def li(items):
        return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>" if items else "<p class='mut'>暂无</p>"
    name = rd.p(pf["header"]["id"]) if rd is not None else pf["header"]["name"]
    h = [f"<section><h2>{html.escape(name)} <button>{html.escape('继续聊 ' + name)}</button></h2>"]
    h.append("<h3>职务</h3>" + li([e(f"{x['title']}｜{x['unit']}｜{x['from']}–{x['to'] if x['to'] else '至今'}") for x in pf["positions"]]))
    r = pf["relation"]
    rel = [f"结构关系：{e(x['direction'])} {e(x['type'])}" for x in r["structural"]]
    if r["org_path"]:
        rel.append(f"组织路径：{e(' → '.join(r['org_path']['path']))}（{e(r['org_path']['label'])}，不是直属上级）")
    rel += [f"互动关系：{e(x['direction'])} {e(x['type'])}｜{e(x['status'])}｜提出者：{e(x['proposed_by'])}" for x in r["interactional"]]
    h.append("<h3>和我的关系</h3>" + li(rel))
    roles = []
    for m in pf["matter_roles"]:
        for name, s in m["roles"].items():
            roles.append(e(f"{m['matter']}｜{name}｜can：{s['can']}｜will：{s['will']}"))
    h.append("<h3>各议题中的角色</h3>" + li(roles))
    h.append(f"<h3>判断卡 <span class='mut'>{html.escape(pf['badge']['text'])}</span></h3>")
    h.append(li([e(c["text"]) for c in pf["judgment_cards"]] + ([e(f"三维对照 {k}：{v}") for k, v in pf["dimensions"].items()] if pf["dimensions"] else [])))
    scene_names = sorted({s for t in pf["timeline"] for s in t.get("scenes", [])})
    tl = []
    for t in pf["timeline"]:
        idx = ",".join(str(scene_names.index(s)) for s in t.get("scenes", []))  # 属性里只放序号，不放场景名（遮挡时不泄露）
        tags = "".join(f" <span class='tag'>#{e(s)}</span>" for s in t.get("scenes", []))
        for o in t["observations"]:
            q = "；".join(o["quotes"])
            tl.append((idx, e(f"{t['day_label']} {t['label']}｜{o['mode']}｜{o['reporter']}：{o['content']}") + tags +
                       (" <span class='mut'>[解读]</span>" if o["interpretation"] else "") + f"<br><span class='mut' data-evidence='{e(q)}'>原话：{e(q)}</span>"))
    bar = ("<div class='sfilter' role='group' aria-label='按场景筛选'>" + "".join(
        f"<button type='button' class='sfb' aria-pressed='false' data-sf='{i}'>#{e(n)}</button>" for i, n in enumerate(scene_names)) + "</div>") if scene_names else ""
    lst = ("<ul>" + "".join(f"<li data-scenes='{ix}'>{x}</li>" for ix, x in tl) + "</ul>") if tl else "<p class='mut'>暂无</p>"
    h.append("<h3>互动时间线</h3>" + bar + lst)
    h.append("<h3>预测复盘与修订</h3>" + li([e(f"#{x['id']} {x['kind']}｜{x['subject']}｜{x['reason']}") for x in pf["revisions"]]))
    h.append("<h3>解读</h3>" + li([e(f"{x['id']} {x['skill']}：{x['question']}") + (" <b>依据已变化</b>" if x["stale"] else "") for x in pf["interpretations"]]))
    led = pf["commitments"]
    STATE = {"open": "进行中", "overdue": "已逾期", "fulfilled": "已兑现", "broken": "未兑现", "cancelled": "已取消"}
    def crow(r):
        q = "；".join(r["quotes"])
        return e(f"{r['content']}｜到期：{r['due'] or '未定'}｜{STATE.get(r['state'], r['state'])}") + \
            (f"<br><span class='mut' data-evidence='{e(q)}'>原话：{e(q)}</span>" if q else "")
    h.append("<h3>承诺</h3><p class='mut'>只记录，不打分，不据此给人下判断。</p>"
             + "<p><b>他欠我</b></p>" + li([crow(r) for r in led["owes_me"]])
             + "<p><b>我欠他</b></p>" + li([crow(r) for r in led["i_owe"]]))
    qd = pf["quadrant"]
    if qd["effective"]:
        h.append(f"<p class='mut'>象限：{html.escape(qd['effective'])}（{'手动设置' if qd['source'] == 'manual' else '系统自动计算'}）"
                 + (f"；系统建议：{html.escape(qd['auto'])}" if qd["manual"] and qd["auto"] and qd["auto"] != qd["manual"] else "") + "</p>")
    h.append("<h3>待核实事项</h3>" + li([e(f"{x['text']}（{x['status']}）") for x in pf["followups"]]))
    return "".join(h) + "</section>"
