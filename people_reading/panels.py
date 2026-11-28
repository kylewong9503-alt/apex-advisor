"""跨议题校准面板（侧栏入口）。二元与多类别分开统计、不混合平均；Brier 越低越好。

只呈现数字：锁定/结算/作废/无法观察/过期、平均 Brier 与平均基线、样本数。
样本少时注明“仅供参考”；不对个人判断力或记忆可靠性下结论。因事实更正而标 lock_facts_corrected 的预测默认不计入。
"""
from __future__ import annotations

import html
from typing import Optional

from .privacy import Redactor
from .world import World

E = html.escape
RANGE = {"binary": "0–1", "categorical": "0–2"}
TYPE_LABEL = {"binary": "二元预测", "categorical": "多类别预测"}
SMALL_N = 5


def _group(g: dict) -> dict:
    out = dict(g)
    out["range"] = None
    n = g["brier_n"]
    out["small_sample"] = 0 < n < SMALL_N
    out["vs_baseline"] = None if not n else ("低于基线" if g["mean_brier"] < g["mean_baseline"] - 1e-9
                                             else "高于基线" if g["mean_brier"] > g["mean_baseline"] + 1e-9 else "与基线相同")
    return out


def calibration_panel(w: World, include_corrected: bool = False, rd: Optional[Redactor] = None) -> dict:
    rd = rd or Redactor(w, "code")
    eng = w.engine
    groups = {t: {**_group(g), "range": RANGE[t], "label": TYPE_LABEL[t]} for t, g in eng.calibration(include_corrected).items()}
    per_matter = []
    for mid in sorted({p.pre.matter_id for p in eng.preds.values()}):
        title = w.matters[mid].title if mid in w.matters else mid
        per_matter.append({"matter_id": rd.mref(mid), "title": rd.m(title),
                           "groups": {t: {**_group(g), "range": RANGE[t], "label": TYPE_LABEL[t]}
                                      for t, g in eng.calibration(include_corrected, matter_id=mid).items()}})
    rows = []
    for p in sorted(eng.preds.values(), key=lambda x: x.pre.locked_at):
        excluded = p.st.status == "settled" and p.st.lock_facts_corrected and not include_corrected
        rows.append({"id": rd.pref(p.id), "person": rd.p(p.pre.person_id), "type": TYPE_LABEL[p.pre.outcome_type],
                     "status": p.st.status, "brier": p.st.brier, "baseline": p.st.brier_baseline,
                     "excluded": excluded})
    return {"groups": groups, "per_matter": per_matter, "rows": rows, "include_corrected": include_corrected,
            "excluded_count": sum(1 for r in rows if r["excluded"]),
            "notes": ["二元与多类别分开统计，不混合平均；Brier 越低越好（二元 0–1，多类别 0–2）",
                      f"样本少于 {SMALL_N} 份时仅供参考，系统不据此对判断力或记忆可靠性下结论",
                      "因事实更正而重算的预测默认不计入"]}


def _f(x) -> str:
    return "—" if x is None else f"{x:.4g}"


def render_calibration(p: dict) -> str:
    def table(groups: dict) -> str:
        if not groups:
            return "<p class='mut'>暂无预测</p>"
        head = "<tr><th>类型</th><th>锁定</th><th>结算</th><th>作废</th><th>无法观察</th><th>过期</th><th>样本</th><th>平均 Brier</th><th>平均基线</th><th>范围</th></tr>"
        body = "".join(
            f"<tr><td>{E(g['label'])}</td><td>{g['locked']}</td><td>{g['settled']}</td><td>{g['void']}</td><td>{g['unobservable']}</td>"
            f"<td>{g['expired']}</td><td>{g['brier_n']}{'（仅供参考）' if g['small_sample'] else ''}</td><td>{_f(g['mean_brier'])}</td>"
            f"<td>{_f(g['mean_baseline'])}{'（' + g['vs_baseline'] + '）' if g['vs_baseline'] else ''}</td><td>{g['range']}</td></tr>"
            for g in groups.values())
        return f"<table>{head}{body}</table>"
    h = ["<h3>全部议题</h3>", table(p["groups"])]
    for m in p["per_matter"]:
        h.append(f"<h3>议题：{E(m['title'])}</h3>" + table(m["groups"]))
    h.append("<h3>逐份预测</h3><table><tr><th>预测</th><th>人物</th><th>类型</th><th>状态</th><th>Brier</th><th>基线</th></tr>"
             + "".join(f"<tr><td>{E(r['id'])}</td><td>{E(r['person'])}</td><td>{E(r['type'])}</td><td>{E(r['status'])}"
                       f"{'（已更正，默认不计入）' if r['excluded'] else ''}</td><td>{_f(r['brier'])}</td><td>{_f(r['baseline'])}</td></tr>"
                       for r in p["rows"]) + "</table>")
    h.append("<ul class='mut'>" + "".join(f"<li>{E(n)}</li>" for n in p["notes"]) + "</ul>")
    return "".join(h)
