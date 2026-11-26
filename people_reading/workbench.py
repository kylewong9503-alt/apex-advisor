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
from .world import World

E = html.escape
CSS = """:root{--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;--line:#d8d8de;--card:#f5f5f8;--acc:#2a5bd7}
@media(prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececf0;--mut:#9a9aa2;--line:#34343a;--card:#202024;--acc:#7aa2ff}}
*{box-sizing:border-box}body{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC","Noto Sans SC",sans-serif;margin:0}
main{max-width:720px;margin:0 auto;padding:20px 16px 48px}h1{font-size:20px;margin:0 0 4px}.mut{color:var(--mut);font-size:13px}
.card{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:10px 14px;margin:10px 0}
.row{padding:6px 0;border-top:1px solid var(--line)}.row b{display:block;font-size:12px;color:var(--mut)}
textarea{width:100%;font:inherit;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
button{font:inherit;padding:4px 10px;margin:2px 4px 2px 0}svg{color:var(--fg)}"""


def page(body: str, title: str = "识人系统") -> str:
    return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{E(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>")


def render_settlement_page(screen: dict) -> str:
    """首屏只有这四项；没有导航、侧栏、判断卡、任何预测或类别名。"""
    return page(f"<h1>{E(screen['matter'])}</h1><p class='mut'>{E(screen['meeting_time'])}</p>"
                f"<p><b>{E(screen['open_question'])}</b></p><textarea rows='4' aria-label='你的回答'></textarea>", "会后")


def card_html(c: dict) -> str:
    h = c["header"]
    rows = "".join(f"<div class='row'><b>{E(r['label'])}{'' if r.get('counts') is None else '（计入累计）' if r['counts'] else '（不计入）'}</b>{E(r['text'])}</div>"
                   for r in c["rows"])
    note = f" · {E(h['note'])}" if h["note"] else ""
    return (f"<div class='card'><b>{E(h['statement'])}</b><div class='mut'>{E(h['by'])}{note}</div>{rows}"
            f"<div class='mut'>{E(c['badge'])}</div>" + ("<div class='mut'>仍待区分：" + E("；".join(c["pending"])) + "</div>" if c["pending"] else "")
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


def render_matter_page(v: dict, graph: Optional[dict] = None) -> str:
    h = [f"<h1>{E(v['title'])} <button>切换议题</button></h1>", f"<p>当前目的：{E(v['purpose'])} <button>修改</button></p>",
         "<h2>我的判断</h2>" + "".join(card_html(c) for c in v["my_judgments"]),
         "<h2>模型提出的另一种可能</h2>" + "".join(card_html(c) for c in v["model_alternatives"])]
    if v["fork"]:
        f = v["fork"]
        h.append(f"<h2>值得问你的一件事</h2><div class='card'><b>{E(f['question'])}</b><ul>"
                 + "".join(f"<li>若{E(b['condition'])}，建议{E(b['prep'])}</li>" for b in f["branches"])
                 + f"</ul><button>{E(f['unsure_option'])}</button></div>")
    if v["actions"]:
        h.append("<h2>行动建议</h2>")
        for a in v["actions"]:
            if "request_line" in a:
                h.append(f"<div class='card'>会上可用的一句话：{E(a['request_line']['text'])}<div class='mut'>理由：{E(a['request_line']['reason'])}</div></div>")
            else:
                h.append(f"<div class='card'>{E(a['text'])}<br><button>记为行动</button>"
                         + (f"<button>锁定相关预测</button><span class='mut'>{E(a['prediction'])}</span>" if a["buttons"]["lock_prediction"] else "")
                         + "</div>")
    if graph:
        h.append("<h2>本议题关系小图</h2>" + render_svg(graph))
    h.append(f"<textarea rows='2' placeholder='{E(v['placeholder'])}'></textarea>")
    return page("".join(h), v["title"])
