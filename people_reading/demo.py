"""第一项交付演示：python -m people_reading.demo  →  people_reading/demo/demo.html

页面上的每个数字、状态和句子都由 engine / cards 当场算出，没有手写结果。
  例一 C5-g：同一场会、同一个实际结果，两份预测因前提不同而结算状态不同
  例二 A.5.6：把 E4 提交人更正为“我”，旧依据、当前累计值与修订原因
"""
from __future__ import annotations

import html
import pathlib

from . import scenarios as S
from .cards import prediction_row, settlement_screen
from .engine import Engine

OUT = pathlib.Path(__file__).parent / "demo" / "demo.html"
ORAL = {"no_show": ("violated", "开场就口头讲了分工")}


def fmt(x):
    return "—" if x is None else (f"{x:.4g}" if isinstance(x, float) else str(x))


def table(head, rows):
    h = "".join(f"<th>{html.escape(str(c))}</th>" for c in head)
    b = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{b}</table>"


def pred_rows(e: Engine):
    return [[p.id, p.pre.bind_mode, p.st.status, fmt(p.st.brier), fmt(p.st.brier_baseline),
             "；".join(c["status"] + "（" + c["quote"] + "）" for c in p.st.premise_check.values()) or "无前提"]
            for p in e.preds.values()]


def pair_rows(e: Engine, opp):
    return [[f"{k[2]} → {k[3]}", r.status, r.source_prediction_id or "—", fmt(r.p1), fmt(r.p2), fmt(r.lr), r.facts_basis]
            for k, r in sorted(e.rows.items()) if k[0] == opp]


def rev_rows(e: Engine, kinds=None):
    return [[r.id, r.kind, r.subject, r.reason or "—"] for r in e.revisions if not kinds or r.kind in kinds]


# ───────── 例一 ─────────
def example_one():
    out = {}
    scr = settlement_screen("争取集团专家支持", "第二天 · 与 F 的会谈")
    out["screen"] = scr
    e = S.build_cfo_engine(); S.add_p_f1b(e)
    out["locked"] = pred_rows(e)
    asked = e.needs_outcome_confirmation("MTG-F", "first_q_topic", 1)
    e.confirm_outcome("MTG-F", "first_q_topic", 1, "专家需求", source_spans=("他先问专家具体要解决哪个问题",))
    out["asked_first"] = asked
    for pid in ("P-F1", "P-F1b"):
        e.settle_prediction(pid, ORAL if pid == "P-F1" else {}, opp_id="MTG-F")
    out.update(preds=pred_rows(e), pairs=pair_rows(e, "MTG-F"), card=prediction_row(e, "MTG-F", "H1", "H2"),
               revs=rev_rows(e, {"void", "pair_update", "no_discrimination", "counterexample", "comparison_changed"}),
               outcomes=len(e.outcomes), questions=e.outcome_questions_asked)
    e2 = S.build_cfo_engine(); S.add_p_f1b(e2)
    e2.confirm_outcome("MTG-F", "first_q_topic", 1, "专家需求")
    for pid in ("P-F1b", "P-F1"):  # 反向顺序
        e2.settle_prediction(pid, ORAL if pid == "P-F1" else {}, opp_id="MTG-F")
    out["order_same"] = e.fingerprint() == e2.fingerprint()
    return out


# ───────── 例二 ─────────
def example_two():
    e = S.build_ask_owner()
    out = {"before_preds": pred_rows(e), "before_pairs": pair_rows(e, "E4"),
           "before_cum": {n: e.cumulative(a, b) for n, (a, b) in CUM.items()},
           "before_cal": e.calibration()["binary"]}
    out["detail_before"] = prediction_row(e, "E4", "H_A", "H_C") if e.rows[("E4", "ask_owner", "H_A", "H_C")].status == "counted" else ""
    lock_p_before = {r: e.preds["P-2"].rule_snaps[r].value for r in ("H_A", "H_B", "H_C")}
    out["preview"] = e.correction_preview("E4", "submitter", "user")
    S.correct(e, "E4", "submitter", "user", source_spans=("E4 其实是我提交的",),
                       reason="用户更正：E4 其实是我提交的")
    out.update(after_pairs=pair_rows(e, "E4"), after_cum={n: e.cumulative(a, b) for n, (a, b) in CUM.items()},
               after_cal=e.calibration(), detail=e.comparison_detail("E4", "H_A", "H_C"),
               revs=rev_rows(e, {"fact_correction", "comparison_changed", "counterexample"}),
               p2=e.preds["P-2"], lock_p=lock_p_before, card=prediction_row(e, "E4", "H_A", "H_B"),
               card_ac=prediction_row(e, "E4", "H_A", "H_C"))
    return out


CUM = {"B 对 A": ("H_A", "H_B"), "C 对 A": ("H_A", "H_C"), "C 对 B": ("H_B", "H_C")}


def cum_rows(before, after):
    return [[n, f"{fmt(before[n]['multiple'])} 倍（{before[n]['n']} 次）", f"{fmt(after[n]['multiple'])} 倍（{after[n]['n']} 次）"]
            for n in CUM]


def render() -> str:
    a, b = example_one(), example_two()
    d = b["detail"]
    css = """:root{--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;--line:#d8d8de;--card:#f5f5f8;--acc:#2a5bd7;--warn:#b4530a}
@media(prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececf0;--mut:#9a9aa2;--line:#34343a;--card:#202024;--acc:#7aa2ff;--warn:#f0a35a}}
body{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC","Noto Sans SC",sans-serif;margin:0}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px}h2{font-size:18px;margin-top:40px;border-top:1px solid var(--line);padding-top:20px}
h3{font-size:15px;margin:22px 0 6px;color:var(--acc)}table{border-collapse:collapse;width:100%;font-size:13px;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:5px 8px;text-align:left;vertical-align:top}th{background:var(--card)}
.card{background:var(--card);border-left:3px solid var(--acc);padding:10px 14px;margin:8px 0;border-radius:4px}
.warn{border-left-color:var(--warn)}.mut{color:var(--mut);font-size:13px}code{font-size:13px}"""
    s = [f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>Kagami 第一项交付演示</title><style>{css}</style></head><body><main>",
         "<h1>Kagami v4.6 · 第一项交付演示</h1>",
         "<p class='mut'>本页由 <code>python -m people_reading.demo</code> 生成。所有数字、状态、句子均由计算内核当场算出，页面里没有手写结果。</p>"]
    # 例一
    s.append("<h2>例一 · 同一场会、同一个结果，两份预测的结算状态不同（C5-g）</h2>")
    s.append("<h3>① 会后首屏（结算模式）：首份报告提交前，页面只有这四项</h3>")
    s.append(table(["议题", "会谈时间", "开放问题", "输入框"], [[a["screen"]["matter"], a["screen"]["meeting_time"], a["screen"]["open_question"], "有"]]))
    s.append("<p class='mut'>判断卡、P-F1、P-F1b、分叉建议、任何候选答案此时都不在页面上。</p>")
    s.append("<h3>② 会前已锁定的两份预测（版本均为 first_q_topic v1）</h3>")
    s.append(table(["预测", "绑定方式", "状态", "Brier", "基线", "前提核对"], a["locked"]))
    s.append("<p class='mut'>P-F1：specific_item，前提=首问前未展示或讲解分工（含截止点：若全程没有实质性提问，以会议结束为截止）。P-F1b：condition_match，窗口内与 F 的任一会谈，无前提。</p>")
    s.append(f"<h3>③ 会后：实际首问为“专家需求”，前提核对我选“口头讲过”</h3><p>实际结果只确认一次：<b>{a['outcomes']}</b> 条 opportunity_outcome，向用户询问结果 <b>{a['questions']}</b> 次（P-F1b 结算时直接复用）。</p>")
    s.append(table(["预测", "绑定方式", "结算状态", "Brier（0–2，越低越好）", "基线", "前提核对"], a["preds"]))
    s.append("<h3>④ H1（先问职责）对 H2（先确认专家要解决的问题）的当前比较状态</h3>")
    s.append(table(["规则对", "状态", "证据来源", "p(H1)", "p(H2)", "lr（H2/H1）", "事实基础"], a["pairs"]))
    s.append(f"<div class='card'>{html.escape(a['card'])}</div>")
    s.append("<p class='mut'>来源是 P-F1b：P-F1 前提不成立，不是符合结算条件的预测，不得作为来源。</p>")
    s.append(f"<h3>⑤ 顺序无关</h3><p>反向结算（P-F1b 先、P-F1 后）后，全部比较状态与各预测结算结果：<b>{'完全一致' if a['order_same'] else '不一致！'}</b>。</p>")
    s.append("<h3>⑥ 修订日志</h3>" + table(["#", "类型", "对象", "原因"], a["revs"]))
    # 例二
    s.append("<h2>例二 · 事后更正：E4 其实是我提交的（A.5.6）</h2>")
    s.append("<h3>① 更正前（E4 提交人=丙）：E4 的当前比较状态</h3>" + table(["规则对", "状态", "来源", "p1", "p2", "lr（后/前）", "事实基础"], b["before_pairs"]))
    s.append("<h3>② 更正预览（用户确认前，不写入任何数据）</h3>")
    pv = b["preview"]
    s.append(f"<p>字段 <code>{pv['field']}</code>：{html.escape(str(pv['old']))} → {html.escape(str(pv['new']))}。将受影响的比较：</p>")
    s.append(table(["规则对", "更正前", "更正后"], [[" → ".join(c["pair"]), f"{c['before']['status']}（lr {fmt(c['before']['lr'])}）", f"{c['after']['status']}（lr {fmt(c['after']['lr'])}）"] for c in pv["comparison_changes"]]))
    s.append("<p class='mut'>将标记 lock_facts_corrected 的预测：" + "、".join(x["prediction"] for x in pv["predictions_marked_lock_facts_corrected"]) + "（锁定快照与原 Brier 保持不变）。解读、角色、关系边的影响预览留待后续交付。</p>")
    s.append("<h3>③ 更正后：E4 的当前比较状态</h3>" + table(["规则对", "状态", "来源", "p1", "p2", "lr（后/前）", "事实基础"], b["after_pairs"]))
    s.append("<h3>④ 累计倍数（只读各机会的当前状态）</h3>" + table(["", "更正前", "更正后"], cum_rows(b["before_cum"], b["after_cum"])))
    s.append("<h3>⑤ 人物页展开：A–C 这对比较的三项</h3>")
    s.append(table(["项目", "内容"], [
        ["锁定时的概率（当时你知道的：提交人=丙）", f"H_A {fmt(d['at_lock']['H_A'])}，H_C {fmt(d['at_lock']['H_C'])}"],
        ["更正前的比较结果", f"{d['before_correction']['status']}，lr {fmt(d['before_correction']['lr'])}"],
        ["当前比较结果（提交人=我）", f"{d['current']['status']}，p(H_A) {fmt(d['current']['p1'])}，p(H_C) {fmt(d['current']['p2'])}"],
    ]))
    s.append(f"<div class='card warn'>{html.escape(b['card'])}</div>")
    p2 = b["p2"]
    s.append(f"<h3>⑥ 不被改写的部分</h3><p>P-2：状态 {p2.st.status}，原 Brier <b>{fmt(p2.st.brier)}</b>（基线 {fmt(p2.st.brier_baseline)}），lock_facts_corrected = <b>{p2.st.lock_facts_corrected}</b>；锁定时规则输出 H_A/H_B/H_C = {b['lock_p']['H_A']}/{b['lock_p']['H_B']}/{b['lock_p']['H_C']}，更正后仍是这三个数。</p>")
    cal0, cal1 = b["before_cal"], b["after_cal"]["binary"]
    s.append(f"<p>校准面板（二元）：更正前平均 Brier {fmt(cal0['mean_brier'])}（2 份）；更正后 {fmt(cal1['mean_brier'])}（{cal1['brier_n']} 份，P-2 默认不计入）。</p>")
    s.append("<h3>⑦ 修订原因（只追加日志）</h3>" + table(["#", "类型", "对象", "原因"], b["revs"]))
    s.append("<p class='mut'>E4 原有的 A–C 4 倍仍可在上面的 comparison_changed 中追溯（前值 counted、lr 4），但已退出累计。</p>")
    s.append("</main></body></html>")
    return "".join(s)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(), encoding="utf-8")
    a, b = example_one(), example_two()
    print("例一 C5-g")
    for r in a["preds"]:
        print("  ", r)
    print("  ", a["card"], "| 顺序无关:", a["order_same"])
    print("例二 A.5.6")
    for n in CUM:
        print(f"   {n}: {b['before_cum'][n]['multiple']} → {b['after_cum'][n]['multiple']}")
    print("   ", b["card"])
    print("已写入", OUT)


if __name__ == "__main__":
    main()
