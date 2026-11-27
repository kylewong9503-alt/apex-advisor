"""工作台视图（6.1）：结算模式首屏 与 “有议题”布局。视图模型 + 静态 HTML。

结算模式：首份报告提交前，页面只有 议题名 / 会谈时间 / 开放问题 / 输入框；
手机端卡片前一条可见消息也不得包含任何候选答案。判断卡、模型提出的另一种可能、分叉建议、原预测、对照卡一律隐藏。
"""
from __future__ import annotations

import html
from typing import Optional

from .graph import build_matter_graph, render_svg
from .judgment import judgment_card
from .settle_flow import SettlementSession
from .theme import BASE_CSS, wave_band
from .world import World

E = html.escape
CSS = BASE_CSS


def page(body: str, title: str = "Kagami") -> str:
    return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{E(title)}</title><style>{CSS}</style></head><body><main>{wave_band()}{body}</main></body></html>")


def render_settlement_page(screen: dict) -> str:
    """首屏只有这四项；没有导航、侧栏、判断卡、任何预测或类别名。"""
    return page(f"<h1>{E(screen['matter'])}</h1><p class='mut'>{E(screen['meeting_time'])}</p>"
                f"<p><b>{E(screen['open_question'])}</b></p><textarea rows='4' aria-label='你的回答'></textarea>", "会后")


def card_html(c: dict, rd=None) -> str:
    T = rd.t if rd is not None else (lambda x: x)
    e = lambda x: E(T(x))  # noqa: E731  动态内容：遮挡时不输出原文
    h = c["header"]
    rows = "".join(f"<div class='row{' recall' if r.get('counts') is False else ''}'><b>{E(r['label'])}{'' if r.get('counts') is None else '（计入累计）' if r['counts'] else '（不计入）'}</b>{e(r['text'])}</div>"
                   for r in c["rows"])
    note = f" · {E(h['note'])}" if h["note"] else ""
    kind = h.get("css", "")
    return (f"<div class='card {E(kind)}'><b><i class='mk mk-{E(kind)}' aria-hidden='true'></i>{e(h['statement'])}</b><div class='mut'>{E(h['by'])}{note}</div>{rows}"
            f"<div class='mut'>{E(c['badge'])}</div>" + ("<div class='mut'>仍待区分：" + e("；".join(c["pending"])) + "</div>" if c["pending"] else "")
            + "</div>")


def workbench_view(w: World, matter_id: str, store=None, *, session: Optional[SettlementSession] = None, prep=None) -> dict:
    """返回 {"mode": "settlement"|"matter", ...}。同一时间只出现一个追问。"""
    if session is not None and session.state == "awaiting_report":
        return {"mode": "settlement", "screen": session.first_screen()}
    m = w.matters[matter_id]
    mine = [h for h in w.hypotheses.values() if h.matter_id == matter_id and h.proposed_by == "user"]
    model = [h for h in w.hypotheses.values() if h.matter_id == matter_id and h.proposed_by == "model"]
    surfaced = w.surface_followups(matter_id=matter_id)
    if store is not None:
        for f in surfaced:
            store.save_followup(f)  # 浮出状态也要落库，重新打开不重复
    v = {"mode": "matter", "title": m.title, "purpose": m.purpose,
         "my_judgments": [judgment_card(w, h.id, store) for h in mine],
         "model_alternatives": [judgment_card(w, h.id, store) for h in model],
         "fork": None, "actions": [], "followups": [f.text for f in surfaced], "placeholder": "继续说，或随手记一件事……"}
    if prep is not None:
        fq = prep.fork_question
        v["fork"] = {"question": fq["question"], "branches": [fq["branch_a"], fq["branch_b"]], "unsure_option": "记不清"}
        v["actions"] = [{"text": it["text"], "prediction": it.get("prediction_text"), "buttons": prep.buttons(i)}
                        for i, it in enumerate(prep.items)] + [{"request_line": prep.request_line}]
    return v


def render_matter_page(v: dict, graph: Optional[dict] = None, rd=None) -> str:
    return page(matter_body(v, graph, rd), "Kagami" if rd is not None and rd.masked else v["title"])


def matter_body(v: dict, graph: Optional[dict] = None, rd=None) -> str:
    T = rd.t if rd is not None else (lambda x: x)
    E_ = lambda x: E(T(x))  # noqa: E731
    h = [f"<h1>{E_(v['title'])} <button>切换议题</button></h1>", f"<p>当前目的：{E_(v['purpose'])} <button>修改</button></p>",
         "<h2>我的判断</h2>" + "".join(card_html(c, rd) for c in v["my_judgments"]),
         "<h2>模型提出的另一种可能</h2>" + "".join(card_html(c, rd) for c in v["model_alternatives"])]
    if v["fork"]:
        f = v["fork"]
        h.append(f"<h2>值得问你的一件事</h2><div class='card'><b>{E_(f['question'])}</b><ul>"
                 + "".join(f"<li>若{E_(b['condition'])}，建议{E_(b['prep'])}</li>" for b in f["branches"])
                 + f"</ul><button>{E_(f['unsure_option'])}</button></div>")
    if v["actions"]:
        h.append("<h2>行动建议</h2>")
        for a in v["actions"]:
            if "request_line" in a:
                h.append(f"<div class='card'>会上可用的一句话：{E_(a['request_line']['text'])}<div class='mut'>理由：{E_(a['request_line']['reason'])}</div></div>")
            else:
                h.append(f"<div class='card'>{E_(a['text'])}<br><button>记为行动</button>"
                         + (f"<button>锁定相关预测</button><span class='mut'>{E_(a['prediction'])}</span>" if a["buttons"]["lock_prediction"] else "")
                         + "</div>")
    if graph:
        h.append("<h2>本议题关系小图</h2>" + render_svg(graph))
    h.append(f"<textarea rows='2' placeholder='{E(v['placeholder'])}'></textarea>")
    return "".join(h)
