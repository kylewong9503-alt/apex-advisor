"""一问一答的主界面：每屏一个问题、几个选项、一个按钮；答完直接给出结论。取代“工作台 + 多张卡片”的主入口。

分工：
  build_*_steps  把已有的视图模型（workbench_view、结算屏、前提问题、判断卡）变成“步骤”列表，不做任何新计算；
  render_flow    把步骤渲染成一个静态页（hash 路由 #/1 #/2 …；选项、继续、跳过都在浏览器里完成）。

规则：
  - 结算首屏（会后第一问）必须是开放题，不给候选答案（避免引导）；之后的前提核对、结果确认才是选择题。
  - 选项旁的小记号取代文字标签：实心圆 = 你的判断，虚线圆 = 模型提出，点线 = 事后回忆（不计入）。
  - 所有动态文字经 Redactor.t：一键遮挡时不输出原文；页面标题遮挡时为常量。
  - 图案是纯装饰（aria-hidden，不含文字）；文字只落在纯色区，不压图案。页面不请求任何外部资源。
"""
from __future__ import annotations

import html
import re
from typing import Optional

from .privacy import NO_CAPTURE_META, PRINT_GUARD_CSS, Redactor
from .theme import BRAND, TOKENS_CSS, enso_path, mark_data_uri, mark_svg

E = html.escape
MARKS = {"you": "mk-you", "model": "mk-model", "rec": "mk-rec", "": ""}
KEYS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MAX_JUDGMENTS = 5  # 会前“他最可能先问什么”最多列 5 条看法 + “说不准”


# ───────────────────────── 步骤构造 ─────────────────────────
def ask(q: str, options: list, *, cue: str = "", selected: Optional[int] = 0, act: Optional[dict] = None) -> dict:
    """options: [(文字, 记号, 说明)]；记号 ∈ you / model / rec / ''。"""
    return {"kind": "ask", "q": q, "cue": cue, "options": [dict(text=t, mark=m, sub=s) for t, m, s in options], "selected": selected, "act": act}


def check(q: str, items: list, *, cue: str = "", act: Optional[dict] = None) -> dict:
    """多选：items = [(id, 文字, 说明)]，默认全选；你取消勾选的就是“不记”。一屏处理一条原话产生的所有草稿。"""
    return {"kind": "check", "q": q, "cue": cue, "items": [dict(id=i, text=t, sub=s) for i, t, s in items], "act": act}


def open_ask(q: str, *, cue: str = "", act: Optional[dict] = None) -> dict:
    return {"kind": "open", "q": q, "cue": cue, "act": act}


def build_prep_steps(v: dict) -> list:
    """会前：判断 →（分叉问题）→ 会上那一句话。v 是 workbench_view(...) 的 matter 模式结果。"""
    steps = []
    mine, model = v.get("my_judgments", []), v.get("model_alternatives", [])
    cue = v.get("title", "")
    if mine or model:
        opts = ([(c["header"]["statement"], "you", "你的判断") for c in mine] +
                [(c["header"]["statement"], "model", "另一种可能") for c in model])[:MAX_JUDGMENTS] + [("说不准", "", "")]
        steps.append(ask("他最可能先问什么？", opts, cue=cue, selected=0))
    f = v.get("fork")
    if f:
        opts = [(f"若{b['condition']}，{b['prep']}", "", "") for b in f["branches"]] + [(f["unsure_option"], "", "")]
        steps.append(ask(f["question"], opts, cue="先问你一件事", selected=None))
    line = next((a["request_line"] for a in v.get("actions", []) if "request_line" in a), None)
    if line:
        lock = any(a.get("buttons", {}).get("lock_prediction") for a in v.get("actions", []) if "request_line" not in a)
        steps.append({"kind": "answer", "cue": "会上，先说这一句", "line": line["text"], "reason": line["reason"],
                      "buttons": ["记为行动"] + (["锁定预测"] if lock else [])})
    return steps


_FOLD = re.compile(r"(\d+(?:\.\d+)?)\s*倍")


def build_settle_steps(screen: dict, premise_qs: list = (), outcome: Optional[dict] = None, cards: list = ()) -> list:
    """会后：开放题（不给候选）→ 前提核对（选择题）→ 结果确认 → 结果。"""
    steps = [open_ask(screen["open_question"], cue=f"{screen['matter']} · {screen['meeting_time']}")]
    for pq in premise_qs:
        steps.append(ask(pq["question"], [(o, "", "") for o in pq["options"]], cue="先核对前提", selected=None))
    if outcome and outcome.get("ask_user") and not outcome.get("reused"):
        ch = outcome.get("choices") or ([outcome["outcome"]] if outcome.get("outcome") is not None else [])
        if ch:
            sel = ch.index(outcome["outcome"]) if outcome.get("outcome") in ch else None
            basis = outcome.get("basis")
            steps.append(ask("这次结果算哪一种？", [(str(c), "", "") for c in ch] + [("无法判断", "", "")],
                             cue=f"依据：{basis}" if basis else "", selected=sel))
    if cards:
        c = cards[0]
        pre = next((r["text"] for r in c["rows"] if r["label"] == "事前预测"), "")
        act = next((r["text"] for r in c["rows"] if r["label"] == "实际发生"), "")
        m = _FOLD.search(pre)
        steps.append({"kind": "result", "cue": "结果", "title": act or c["header"]["statement"],
                      "big": f"{m.group(1)}×" if m else "", "big_note": pre if m else "", "note": c["badge"],
                      "buttons": ["完成"]})
    return steps


# ───────────────────────── 图案（青海波，纯装饰） ─────────────────────────
def _scale_defs() -> str:
    """每种“鳞”一个 <g>，之后用 <use> 摆放：页面体积小，颜色全走令牌，夜海主题自动变色。"""
    def rings(n, R, stroke, fill_outer, dash=""):
        out = []
        for i in range(n, 0, -1):
            r = round(R * i / n, 2)
            out.append(f"<circle r='{r}' fill='{fill_outer if i == n else 'none'}' stroke='{stroke}'{dash}/>")
        return "".join(out)
    R = 26
    g = {
        "classic": rings(4, R, "var(--hanada)", "var(--g)"),
        "ripple_hi": rings(7, R, "var(--hanada)", "var(--g)"), "ripple_lo": rings(2, R, "var(--hanada)", "var(--g)"),
        "dotted": rings(4, R, "var(--hanada)", "var(--g)", " stroke-dasharray='.1 5' stroke-linecap='round' stroke-width='2.2'"),
        "yoru": "".join(f"<circle r='{round(R * i / 4, 2)}' fill='{'var(--g)' if i == 4 else 'none'}' stroke='{'var(--on-kon)' if i % 2 else 'var(--asagi)'}'/>" for i in (4, 3, 2, 1)),
    }
    cols = ["var(--ai)", "var(--hanada)", "var(--asagi)", "var(--mizu)"]
    g["banded"] = "".join(f"<circle r='{round(R * (4 - k) / 4, 2)}' fill='{cols[k]}' stroke='var(--g)'/>" for k in range(4))
    for t, c in enumerate(["var(--mizu)", "var(--asagi)", "var(--hanada)", "var(--ai)"]):
        g[f"tide{t}"] = "".join(f"<circle r='{round(R * i / 4, 2)}' fill='{c if (4 - i) % 2 == 0 else 'var(--g)'}' stroke='var(--g)'/>" for i in (4, 3, 2, 1))
    return "".join(f"<g id='s-{k}' stroke-width='1.2'>{v}</g>" for k, v in g.items())


def pattern(variant: str, w: int, h: int, *, uid: str, R: int = 26, ground: str = "var(--surface)") -> str:
    """确定性（无随机）：同参数同输出。几何：横距 2R、行距 R/2、奇数行错位 R，自上而下叠压。"""
    use = []
    rows, cols = int(h / (R / 2)) + 3, int(w / (2 * R)) + 3
    focus = (w * 0.55, h * 0.5)
    for r in range(-1, rows):
        cy = r * R / 2
        for c in range(-1, cols):
            cx = c * 2 * R + (R if r % 2 else 0)
            sym = variant
            if variant == "tide":
                sym = f"tide{min(3, max(0, int(cy / h * 4)))}"
            elif variant == "ripple":
                d = ((cx - focus[0]) ** 2 + (cy - focus[1]) ** 2) ** .5 / max(w, h)
                sym = "ripple_hi" if d < .16 else "classic" if d < .38 else "ripple_lo"
            elif variant == "fade" and ((c * 7 + r * 13) % 10) / 10 > max(0, 1 - cy / h * 1.15):
                continue
            use.append(f"<use href='#s-{sym}' x='{cx}' y='{round(cy, 2)}'/>")
    return (f"<svg viewBox='0 0 {w} {h}' preserveAspectRatio='xMidYMin slice' width='100%' height='100%' aria-hidden='true' focusable='false' "
            f"style='--g:{ground};display:block'><rect width='{w}' height='{h}' fill='{ground}'/>{''.join(use)}</svg>")


def steps_dots(n: int, total: int) -> str:
    """进度：一笔圆相随步骤画满（不写“第几步”）。底下是一道很淡的整圈。"""
    sweep = max(24, 330 * n / max(total, 1))
    return (f"<span class='dots' role='img' aria-label='第 {n} 步，共 {total} 步'>"
            f"<svg viewBox='0 0 64 64'><circle cx='32' cy='32' r='19' fill='none' stroke='var(--line)' stroke-width='2'/>"
            f"<path d='{enso_path(sweep=sweep)}' fill='var(--ai)'/></svg></span>")


# ───────────────────────── 渲染 ─────────────────────────
FLOW_CSS = TOKENS_CSS + """
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;background:var(--surface);color:var(--ink);font:15px/26px var(--font-sans);font-feature-settings:'tnum' 1,'cv11' 1}
.step{display:none;height:100vh;min-height:560px}.step.on{display:flex}
.pane{flex:none;width:38%;max-width:560px;overflow:hidden;background:var(--surface)}.pane.dark{background:var(--kon)}
.step.top{flex-direction:column}.step.top .pane{width:100%;max-width:none;height:230px}
.step.right{flex-direction:row-reverse}
.body{flex:1;min-width:0;padding:56px clamp(24px,6vw,96px);display:flex;flex-direction:column;justify-content:space-between;max-width:880px;margin:0 auto;width:100%}
.hd{display:flex;justify-content:space-between;align-items:center}
.brand{font:600 18px/1 var(--font-sans);letter-spacing:.06em;display:inline-flex;align-items:center;gap:10px}.brand svg{border-radius:9px;box-shadow:0 0 0 1px var(--line)}
.hr{display:flex;align-items:center;gap:var(--space-3)}.say{height:34px;font-size:14px}.dots{display:flex}.dots svg{width:34px;height:34px}
.cue{font:500 11px/16px var(--font-sans);letter-spacing:.16em;color:var(--ink-muted);margin-bottom:16px}
h1{font:600 clamp(26px,3.2vw,44px)/1.4 var(--font-sans);letter-spacing:.02em;margin:0 0 40px}
.opts{display:flex;flex-direction:column}
.opt{display:flex;align-items:center;gap:16px;width:100%;text-align:left;padding:18px 0;border:0;border-top:1px solid var(--line);background:none;color:var(--ink);font:inherit;font-size:20px;line-height:1.5;cursor:pointer;min-height:44px}
.opt:last-child{border-bottom:1px solid var(--line)}
.opt .k{width:26px;height:26px;border:1px solid var(--line-strong);border-radius:var(--radius-sm);font-size:12px;display:flex;align-items:center;justify-content:center;color:var(--ink-muted);flex:none}
.opt small{display:block;font-size:13px;color:var(--ink-muted)}
.opt[role=checkbox] .k{color:transparent}.opt[role=checkbox][aria-checked=true] .k{background:var(--ai);border-color:var(--ai);color:var(--on-ai)}
.opt[role=checkbox][aria-checked=true]{background:none;color:var(--ink);margin:0;padding:18px 0;width:100%;border-color:var(--line);border-radius:0}
.opt[role=checkbox][aria-checked=true] small{color:var(--ink-muted);opacity:1}
.opt[aria-checked=true]:not([role=checkbox]){border-radius:var(--radius-md);background:var(--ai);color:var(--on-ai);margin:0 -16px;padding:18px 16px;width:calc(100% + 32px);border-color:var(--ai)}
.opt[aria-checked=true] .k{border-color:var(--on-ai);color:var(--on-ai)}.opt[aria-checked=true] small{color:var(--on-ai);opacity:.85}
.mk-you,.mk-model,.mk-rec{display:inline-block;margin-right:8px;flex:none}
.mk-you{width:10px;height:10px;border-radius:50%;background:var(--ai)}.mk-model{width:10px;height:10px;border-radius:50%;border:1.5px dashed var(--hanada)}
.mk-rec{width:14px;border-top:2px dotted var(--kin);vertical-align:middle}
[aria-checked=true] .mk-you{background:var(--on-ai)}[aria-checked=true] .mk-model{border-color:var(--on-ai)}
textarea{width:100%;font:inherit;font-size:20px;line-height:1.7;padding:16px;border:1px solid var(--line-strong);border-radius:var(--radius-sm);background:var(--surface-raised);color:var(--ink);resize:none}
.ft{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}.ft div{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.btn{height:44px;padding:0 24px;border-radius:var(--radius-sm);font:500 15px var(--font-sans);letter-spacing:.04em;display:inline-flex;align-items:center;cursor:pointer;text-decoration:none;border:1px solid var(--line-strong);background:var(--surface-raised);color:var(--ink)}
.btn.p{background:var(--ai);border-color:var(--ai);color:var(--on-ai)}.btn.o{border-color:var(--ai);color:var(--ai)}
.btn.q{border-color:transparent;background:none;color:var(--ai);text-decoration:underline;text-underline-offset:4px;padding:0 8px}
.btn:disabled{opacity:.45;cursor:not-allowed}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.bn{position:fixed;top:0;left:0;right:0;z-index:5;background:var(--kin-soft);color:var(--kin);font-size:12px;line-height:20px;text-align:center;padding:2px 8px}
.srs{border-bottom:1px solid var(--line);max-width:620px}.sr{display:flex;gap:20px;padding:12px 0;border-top:1px solid var(--line);font-size:16px;line-height:1.6}.sr span:first-child{width:56px;flex:none;color:var(--ink-muted)}
.nt{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:6;max-width:min(560px,92vw);background:var(--ink);color:var(--surface);padding:12px 18px;border-radius:var(--radius-md);font-size:14px;line-height:22px}
.qt{margin:0 0 20px;padding:0 0 0 16px;border-left:2px solid var(--line-strong);color:var(--ink-muted);font-size:15px;line-height:26px}.rd{list-style:none;margin:0;padding:0}.rd li{display:flex;gap:12px;align-items:flex-start;padding:14px 0;border-top:1px solid var(--line);font-size:18px;line-height:1.5}.rd li:last-child{border-bottom:1px solid var(--line)}.rd small{display:block;margin-top:2px;font-size:13px;color:var(--ink-muted)}.rd .mk-model{margin-top:9px}\n.line{font:600 clamp(24px,3vw,44px)/1.45 var(--font-sans);margin:0 0 28px}
.why{margin:0;font-size:17px;line-height:1.9;color:var(--ink-muted);max-width:560px}
.big{font:600 clamp(56px,7vw,88px)/1 var(--font-sans);color:var(--ai);font-variant-numeric:tabular-nums}
.bign{display:flex;align-items:baseline;gap:16px;margin-bottom:12px;flex-wrap:wrap}.bign span+span{font-size:16px;color:var(--ink-muted);line-height:1.7;max-width:480px}
@media(max-width:720px){.step.on{flex-direction:column}.step.right{flex-direction:column}.pane,.step.top .pane{width:100%;max-width:none;height:150px}.body{padding:24px 22px}h1{margin-bottom:24px}.opt{font-size:17px}.opt[aria-checked=true]{margin:0 -12px;padding:18px 12px;width:calc(100% + 24px)}}
@media(prefers-reduced-motion:no-preference){.step.on .body{animation:in .2s ease-out}@keyframes in{from{opacity:.6;transform:translateY(4px)}to{opacity:1;transform:none}}}
"""

FLOW_JS = """(function(){
var S=[].slice.call(document.querySelectorAll('.step')),n=S.length;
function go(i){i=Math.max(1,Math.min(n,i));S.forEach(function(s,k){s.classList.toggle('on',k===i-1)});var f=S[i-1].querySelector('.opt[aria-checked=true],textarea,.btn.p');f&&f.focus({preventScroll:true})}
function cur(){var m=(location.hash||'#/1').match(/(\\d+)/);return m?+m[1]:1}
function next(){location.hash='#/'+Math.min(n,cur()+1)}
addEventListener('hashchange',function(){go(cur())});go(cur());
document.addEventListener('click',function(e){var o=e.target.closest('.opt');
 if(o&&o.getAttribute('role')==='checkbox'){o.setAttribute('aria-checked',o.getAttribute('aria-checked')==='true'?'false':'true');return}
 if(o){[].forEach.call(o.parentNode.children,function(x){x.setAttribute('aria-checked',x===o?'true':'false')});var b=o.closest('.step').querySelector('[data-next]');b&&(b.disabled=false);return}
 var a=e.target.closest('[data-act]');
 if(a){e.preventDefault();var d=JSON.parse(a.dataset.fields||'{}'),st=a.closest('.step'),ch=st.querySelector('.opt[aria-checked=true]'),ta=st.querySelector('textarea');
  if(ch)d.choice=[].indexOf.call(ch.parentNode.children,ch);var cks=st.querySelectorAll('.opt[role=checkbox]');if(cks.length)d.ids=[].filter.call(cks,function(x){return x.getAttribute('aria-checked')==='true'}).map(function(x){return x.dataset.id}).join(',');if(ta)d.text=ta.value;if(a.hasAttribute('data-once')&&a.disabled)return;a.disabled=true;a.dataset.t=a.textContent;a.textContent='处理中…';a.setAttribute('aria-busy','true');
  fetch(a.dataset.act,{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(d)}).then(function(r){
   if(!r.ok)throw 0;if(a.hasAttribute('data-once')){a.textContent='已记下'}else{location.href='/'}}).catch(function(){a.disabled=false;a.removeAttribute('aria-busy');a.textContent='没成功，再试一次'});return}
 if(e.target.closest('[data-next]')){e.preventDefault();next()}});
document.addEventListener('keydown',function(e){var s=S[cur()-1];if(!s||e.target.tagName==='TEXTAREA')return;
 var k='ABCDEF'.indexOf(e.key.toUpperCase());var os=s.querySelectorAll('.opt');if(e.key.length===1&&k>=0&&os[k]){os[k].click();return}
 if(e.key==='Enter'){var b=s.querySelector('[data-next]');b&&!b.disabled&&next()}});
})();"""

VARIANTS = [("classic", "l"), ("banded", "r"), ("dotted", "t"), ("ripple", "l"), ("tide", "r"), ("fade", "t")]


def render_flow(steps: list, *, rd: Optional[Redactor] = None, title: str = BRAND, library_href: str = "", head_extra: str = "", banner: str = "", say_href: str = "", notice: str = "") -> str:
    T = rd.t if rd is not None else (lambda x: x)
    e = lambda x: E(T(str(x)))  # noqa: E731  动态内容：遮挡时不输出原文
    masked = bool(rd is not None and rd.masked)
    total = len(steps)
    secs = []
    vi = 0
    for i, s in enumerate(steps):
        last = i == total - 1
        if s["kind"] == "answer":
            variant, side, dark = "yoru", "l", True
        else:
            variant, side = VARIANTS[vi % len(VARIANTS)]
            dark = False
            vi += 1
        art = pattern(variant, 560 if side != "t" else 1440, 900 if side != "t" else 230, uid=str(i),
                      ground="var(--kon)" if dark else "var(--surface)")
        say = f"<a class='btn q say' href='{E(say_href)}'>记一句</a>" if say_href else ""
        hd = f"<div class='hd'><span class='brand'>{mark_svg(32)}{BRAND}</span><span class='hr'>{say}{steps_dots(i + 1, total)}</span></div>"
        cue = f"<div class='cue'>{e(s['cue'])}</div>" if s.get("cue") else ""
        def actattr(a):  # 这一步的“继续”要先把选择提交给服务：{"url": "/confirm", "fields": {...}}
            if not a:
                return ""
            import json
            return f" data-act='{E(a['url'])}' data-fields='{E(json.dumps(a.get('fields', {}), ensure_ascii=False), quote=True)}'"
        nxt_idx = i + 2
        skip = "<a class='btn q' href='#/%d' data-skip>跳过</a>" % nxt_idx
        if s["kind"] == "ask":
            os_ = "".join(
                f"<button type='button' class='opt' role='radio' aria-checked='{'true' if k == s['selected'] else 'false'}'>"
                f"<span class='k' aria-hidden='true'>{KEYS[k] if k < len(KEYS) else k + 1}</span><span>"
                + (f"<i class='{MARKS[o['mark']]}' aria-hidden='true'></i>" if o["mark"] else "") + f"{e(o['text'])}"
                + (f"<small>{e(o['sub'])}</small>" if o["sub"] else "") + "</span></button>" for k, o in enumerate(s["options"]))
            core = f"{cue}<h1>{e(s['q'])}</h1><div class='opts' role='radiogroup' aria-label='选项'>{os_}</div>"
            ft = f"<div class='ft'>{skip}<button type='button' class='btn p' data-next{actattr(s.get('act'))}{'' if s['selected'] is not None else ' disabled'}>继续　↵</button></div>"
        elif s["kind"] == "check":
            os_ = "".join(
                f"<button type='button' class='opt' role='checkbox' aria-checked='true' data-id='{E(it['id'])}'>"
                f"<span class='k' aria-hidden='true'>✓</span><span>{e(it['text'])}" + (f"<small>{e(it['sub'])}</small>" if it["sub"] else "")
                + "</span></button>" for it in s["items"])
            core = f"{cue}<h1>{e(s['q'])}</h1><div class='opts' role='group' aria-label='选项'>{os_}</div>"
            ft = f"<div class='ft'><span class='mut'>取消勾选的不记</span><button type='button' class='btn p' data-next{actattr(s.get('act'))}>记下所选</button></div>"
        elif s["kind"] == "reading":
            lis = "".join(f"<li><i class='mk-model' aria-hidden='true'></i><span>{e(x['statement'])}<small>{e(x['evidence'])}</small></span></li>"
                          for x in s["items"])
            core = (f"{cue}<h1>{e(s['title'])}</h1><blockquote class='qt'>{e(s['quote'])}</blockquote>"
                    f"<ul class='rd'>{lis}</ul><p class='why' style='margin-top:16px;font-size:14px'>{E(s['note'])}</p>")
            ft = f"<div class='ft'><span></span><button type='button' class='btn p'{actattr(s.get('act'))}>继续</button></div>"
        elif s["kind"] == "open":
            core = f"{cue}<h1>{e(s['q'])}</h1><textarea rows='5' aria-label='你的回答' placeholder='按你记得的说就行，原话越接近越好'></textarea>"
            ft = f"<div class='ft'><span></span><button type='button' class='btn p' data-next{actattr(s.get('act'))}>提交</button></div>"
        elif s["kind"] == "answer":
            core = (f"{cue}<p class='line'>“{e(s['line'])}”</p><p class='why'>{e(s['reason'])}</p>")
            def one(k, b):
                label, a = (b, None) if isinstance(b, str) else (b["label"], b.get("act"))
                if not isinstance(b, str) and b.get("disabled"):
                    return f"<button type='button' class='btn o' disabled>{E(label)}</button>"
                return f"<button type='button' class='btn {'p' if k == 0 else 'o'}'{(actattr(a) + ('' if a.get('reload') else ' data-once') + (' data-reload' if a.get('reload') else '')) if a else ''}>{E(label)}</button>"
            btns = "".join(one(k, b) for k, b in enumerate(s["buttons"]))
            ft = f"<div class='ft'><div>{btns}</div><button type='button' class='btn q' data-next>会后</button></div>" if not last else f"<div class='ft'><div>{btns}</div></div>"
        elif s["kind"] == "summary":  # 锁定前看一眼：几行事实 + 一句提醒 + 锁定 / 返回修改
            rows = "".join(f"<div class='sr'><span>{E(k)}</span><span>{e(v)}</span></div>" for k, v in s["rows"])
            mk = f"<i class='{MARKS[s.get('mark', '')]}' aria-hidden='true'></i>" if s.get("mark") else ""
            core = f"{cue}<h1>{mk}{e(s['title'])}</h1><div class='srs'>{rows}</div><p class='why' style='margin-top:20px;font-size:14px'>{E(s['note'])}</p>"
            ft = (f"<div class='ft'><div><button type='button' class='btn p'{actattr(s.get('act'))}>锁定预测</button>"
                  f"<button type='button' class='btn q'{actattr(s.get('back'))}>返回修改</button></div></div>")
        else:  # result
            big = (f"<div class='bign'><span class='big'>{e(s['big'])}</span><span>{e(s['big_note'])}</span></div>" if s.get("big") else "")
            core = f"{cue}<h1>{e(s['title'])}</h1>{big}<p class='why'>{e(s['note'])}</p>"
            lib = f"<a class='btn q' href='{E(library_href)}'>人物与关系图</a>" if library_href else ""
            ft = f"<div class='ft'><div><button type='button' class='btn p'{actattr(s.get('act'))}>完成</button>{lib}</div></div>"
        secs.append(f"<section class='step {side}' aria-label='第 {i + 1} 步'><div class='pane{' dark' if dark else ''}' aria-hidden='true'>{art}</div>"
                    f"<div class='body'>{hd}<div>{core}</div>{ft}</div></section>")
    defs = f"<svg width='0' height='0' style='position:absolute' aria-hidden='true'><defs>{_scale_defs()}</defs></svg>"
    ttl = BRAND if masked else title
    return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{E(ttl)}</title>{NO_CAPTURE_META}<link rel='icon' href='{mark_data_uri()}'>{head_extra}<style>{FLOW_CSS}{PRINT_GUARD_CSS}</style></head><body>{defs}{(f"<div class='bn' role='status'>{E(banner)}</div>" if banner else '')}{(f"<div class='nt' role='alert'>{E(notice)}</div>" if notice else '')}{''.join(secs)}<script>{FLOW_JS}</script></body></html>")
