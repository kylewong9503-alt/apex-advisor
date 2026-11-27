"""第 4 步演示：python -m people_reading.demo_step4 → people_reading/demo/step4.html

第 10 节 C1–C6 的完整闭环。模型输出来自测试里的脚本化数据（未接真实模型）；
类别、概率、Brier、比较、状态、文案、判断卡、时间线、关系图、校准全部由程序算出。
"""
from __future__ import annotations

import html
import pathlib
import tempfile

from . import scenarios as S
from .graph import build_matter_graph, render_svg
from .judgment import event_timeline, judgment_card, judgment_timeline
from .premeeting import PreMeeting
from .settle_flow import SettlementSession
from .tests import test_step4 as T
from .workbench import CSS, card_html, render_settlement_page, workbench_view

OUT = pathlib.Path(__file__).parent / "demo" / "step4.html"
E = html.escape


def run() -> str:
    tmp = pathlib.Path(tempfile.mkdtemp())
    w, st, it, pm, mid, iid = T.c2(tmp)
    it.model.outputs = [T.PREP_OUT, T.C3_OUT, {"branch": "A"}, T.REVISED, T.report_out(T.REPORT_DRAFTS), T.proposal(), T.REVIEW]
    prep = pm.prepare(); pm.confirm_categories(); pm.formalize(T.DIST_H)
    sec = []

    def block(title, body, note=""):
        sec.append(f"<h2>{E(title)}</h2>" + (f"<p class='mut'>{E(note)}</p>" if note else "") + body)
    v = workbench_view(w, mid, st, prep=prep)
    from .workbench import render_matter_page
    page = render_matter_page(v)
    block("C1–C2 会前 · 议题、两种看法、类别草稿", page[page.index("<h1>"):page.index("</main>")],
          "我的判断（H1，随口说出，已形式化）与模型提出的另一种可能（H2）都显示“尚无互动支持”；分叉问题只有一个，每个准备项的两个按钮相互独立。")
    res = pm.answer_fork("c1", T.C3_TEXT)
    pm.revise_after_fork(res["branch"], T.C3_TEXT)
    block("C3 回忆材料：只展示，不计入比较", card_html(judgment_card(w, "H1", st)),
          "“回忆材料”行单独展示；徽标注明其中 1 次为事后回忆；此时没有任何 pair_evidence。")
    pm.record_action("会谈结束时 F 答应引荐一位具体的集团专家", ["分工说明", "专家介入案例"])
    p = pm.lock_prediction(T.DIST_P, S.PREMISE_F1)
    block("C4 行动与预测分开锁定", f"<div class='card'>行动 I-1：准备 分工说明、专家介入案例（不影响任何预测）<br>"
          f"预测 {E(p.id)}：{E(p.pre.behavior_key)}，类别 {E('/'.join(p.pre.frozen_categories))}，概率 {E(str(dict(p.pre.prob_dist)))}<br>"
          f"前提：{E(p.pre.premises[0].text)}；{E(p.pre.premises[0].cutoff)}<br>基线：{E(p.pre.baseline_note)}</div>")
    sess = SettlementSession(it, mid, iid, it.model, "第二天 · 与 F 的会谈")
    scr = render_settlement_page(sess.first_screen())
    block("C5 会后 · 结算模式首屏",
          "<div style='border:1px solid var(--line);padding:8px'>" + scr[scr.index("<main>") + 6:-len("</main></body></html>")] + "</div>",
          "首份报告提交前，页面只有议题名、会谈时间、开放问题和输入框；判断卡、原预测、分叉建议、候选答案都不在页面上。"
          "电脑（1280px）与手机（390px）两种宽度的真实页面检查在测试 test_settlement_first_screen_has_no_candidate_answers_desktop_and_mobile 里。")
    sess.submit_report("c1", T.REPORT)
    q = sess.premise_questions()[0]
    sess.answer_premise(q["premise_text"], "都没有", "分工说明是他问谁牵头时才拿出来的")
    prop = sess.propose_outcome()
    sess.confirm_outcome()
    rev = sess.review(confirm_intervention="partial")
    block("C5 前提核对与结果确认",
          f"<div class='card'>问：{E(q['question'])}<br>选项：{E(' / '.join(q['options']))}<br>答：都没有；补充“分工说明是他问谁牵头时才拿出来的”<br>"
          f"execution_log：{E(str([(x['item'], x['timing']) for x in w.interventions['I-1'].execution_log]))}</div>"
          f"<div class='card'>模型提出结果：{E(prop['outcome'])}；依据原话：“{E(prop['basis'])}”；对照标准：{E(prop['criterion'])}<br>"
          f"用户确认后写入 opportunity_outcome；P-1 Brier {w.engine.preds['P-1'].st.brier}（基线 {w.engine.preds['P-1'].st.brier_baseline:.3f}）</div>")
    block("C5 复盘 · 三行判断卡（H1：先问职责）", "".join(card_html(c) for c in rev["cards"] if c["id"] == "H1"))
    block("C5 复盘 · 干预、角色、watch", "<div class='card'>"
          f"干预 I-1 结果：{E(w.interventions['I-1'].immediate_outcome)}（用户确认）<br>"
          f"角色证据：F · referrer · will：{E(rev['role_update']['statement'])}（{E(rev['role_update']['status'])}）；can：未知<br>"
          f"模型另提角色草稿：{E(rev['alt_role']['statement'])}<br>watch：{E(rev['watch']['text'])}（观察信号，代价 0）<br>"
          f"后续预测建议（由你决定）：{E(rev['suggested_prediction']['statement'])}</div>")
    g = build_matter_graph(w, mid, st)
    block("C6 议题关系小图", f"<div class='card'>{render_svg(g)}</div>",
          "实线为已核实，虚线为提议中并标出提出者；没有 F 指向其他人的连线。点节点展开依据、替代解释和区分信号。")
    jt = judgment_timeline(w, "H1")
    block("C6 两条时间线", "<div class='card'><b>判断形成时间线（H1）</b><ul>" + "".join(f"<li>{E(e['text'])}</li>" for e in jt["events"]) +
          f"</ul><span class='mut'>{E(jt['note'])}</span></div><div class='card'><b>事件时间线（F）</b><ul>" +
          "".join(f"<li>{E(e['text'])}</li>" for e in event_timeline(w, 'F')) + "</ul></div>")
    cal = w.engine.calibration(matter_id=mid)["categorical"]
    block("C6 本议题校准结果（多类别组，范围 0–2）", f"<div class='card'>锁定 {cal['locked']}，结算 {cal['settled']}；平均 Brier {cal['mean_brier']}，平均基线 {cal['mean_baseline']:.3f}</div>")
    return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Kagami 第 4 步演示</title><style>{CSS}h2{{border-top:1px solid var(--line);padding-top:16px;margin-top:32px;font-size:17px}}</style></head><body><main>"
            "<h1>Kagami v4.6 · 第 4 步演示（CFO 会谈闭环）</h1><p class='mut'>模型输出为脚本化演示数据（未接真实模型）；其余全部由程序算出。</p>"
            + "".join(sec) + "</main></body></html>")


if __name__ == "__main__":
    OUT.write_text(run(), encoding="utf-8")
    print("已写入", OUT)
