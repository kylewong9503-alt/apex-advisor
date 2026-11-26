"""第 3 步演示：python -m people_reading.demo_step3 → people_reading/demo/step3.html

第 9 节 ④–⑤ 的完整对话流程（模型输出为脚本化的演示数据，其余全部由程序算出）+ 人物个人页。
"""
from __future__ import annotations

import html
import pathlib
import tempfile

from . import scenarios as S
from .assistant import Assistant
from .intake import Intake
from .llm import ScriptedModel
from .profile import build_profile, render_html
from .skills import SkillRegistry
from .store import Store

OUT = pathlib.Path(__file__).parent / "demo" / "step3.html"
TEXT1 = "今天会上，X 连问三次谁负责。散会后甲说，X 很不满。"
E = html.escape
D_BEH = {"kind": "behavior", "reporter": "我", "subject": "X", "content": "X 连问三次谁负责", "is_interpretation": False,
         "source_quote": "X 连问三次谁负责", "behavior_key": "ask_owner", "occurred": True}
D_HEAR = {"kind": "hearsay", "reporter": "甲", "subject": "X", "content": "X 很不满", "is_interpretation": True,
          "source_quote": "X 很不满", "behavior_key": None, "occurred": None}
OUT1 = {"intent": "record", "reply": None, "clarifying_question": "三次都是针对同一份提案吗？",
        "interaction": None, "drafts": [D_BEH, D_HEAR]}
OUT2 = {"intent": "record", "reply": None, "clarifying_question": None,
        "interaction": {"cross_dept": True, "submitter": "我", "responsible_named": None}, "drafts": []}


def run() -> str:
    w = S.build_world_a(through="E3")
    st = Store(str(pathlib.Path(tempfile.mkdtemp()) / "d.db"), "demo")
    st.snapshot_world(w)
    it = Intake(st, w, ScriptedModel([OUT1, OUT2]))
    it.adopt_world()
    st.new_conversation("c1")
    a = Assistant(it, SkillRegistry(st))
    chat = []

    def say(who, text, kind=""):
        chat.append((who, text, kind))
    for msg in (TEXT1, "是，都是我提交的跨部门提案"):
        say("我", msg); say("系统", a.handle("c1", msg).text)
    it.confirm_all()
    say("系统", "（两条草稿已确认；亲见行为只计一次发生；甲的话记为转述+解读）", "note")
    q = "用竞争解释分析一下，他是不是只对我这样？"
    say("我", q); r4 = a.handle("c1", q); say("系统", r4.text, "interp")
    say("我", "上次还差什么信息？"); say("系统", a.handle("c1", "上次还差什么信息？").text)
    say("我", "那份提案是丁提交的"); pv = a.handle("c1", "那份提案是丁提交的"); say("系统", pv.text, "preview")
    before = render_html(build_profile(w, "X", st))
    say("我", "（确认更正）"); say("系统", a.confirm_correction("c1", pv.preview_id).text)
    after = render_html(build_profile(w, "X", st))
    wb = S.build_world_b()
    css = """:root{--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;--line:#d8d8de;--card:#f5f5f8;--acc:#2a5bd7}
@media(prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececf0;--mut:#9a9aa2;--line:#34343a;--card:#202024;--acc:#7aa2ff}}
body{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang SC","Noto Sans SC",sans-serif;margin:0}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px}h2{font-size:18px;border-top:1px solid var(--line);padding-top:18px;margin-top:36px}
h3{font-size:15px;color:var(--acc);margin:16px 0 4px}.m{padding:8px 12px;margin:6px 0;border-radius:6px;background:var(--card);white-space:pre-wrap}
.me{background:transparent;border:1px solid var(--line)}.mut{color:var(--mut);font-size:13px}button{font:inherit;padding:2px 10px}
section{border:1px solid var(--line);border-radius:8px;padding:4px 16px 12px;margin:12px 0}ul{padding-left:20px}"""
    h = [f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>识人系统 第 3 步演示</title><style>{css}</style></head><body><main>",
         "<h1>识人系统 v4.6 · 第 3 步演示</h1><p class='mut'>第 9 节 ④–⑤：竞争解释 Skill、回忆、事实更正。录入阶段的模型输出是脚本化演示数据（未接真实模型），其余数字、状态、句子均由程序算出。</p>",
         "<h2>对话</h2>"]
    for who, text, kind in chat:
        h.append(f"<div class='m {'me' if who == '我' else ''}'><b>{who}</b>　{E(text)}</div>")
    h.append("<h2>人物个人页 · X（更正前）</h2>" + before)
    h.append("<h2>人物个人页 · X（更正后）：解读标“依据已变化”</h2>" + after)
    h.append("<h2>人物个人页 · F（三个底层问题分别回答）</h2>" + render_html(build_profile(wb, "F")))
    h.append("</main></body></html>")
    return "".join(h)


if __name__ == "__main__":
    OUT.write_text(run(), encoding="utf-8")
    print("已写入", OUT)
