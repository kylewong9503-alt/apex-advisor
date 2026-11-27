"""应用外壳：电脑端左栏导航 + 中间内容 + 右侧证据抽屉；手机端底部导航「工作台 / 人物 / 关系图」+ 证据从底部拉起。
侧栏：最近对话、校准面板入口。隐私：顶部“一键遮挡”。静态演示用 hash 路由；真实应用里遮挡由后端重新渲染。
"""
from __future__ import annotations

import html
from typing import Optional

from . import quadrants
from .graph import build_matter_graph, render_graph_browser
from .panels import calibration_panel, render_calibration
from .privacy import NO_CAPTURE_META, PRINT_GUARD_CSS, Redactor
from .profile import build_profile, render_html as render_profile
from .workbench import CSS, matter_body, workbench_view

E = html.escape

SHELL_CSS = """
body{margin:0}.app{display:flex;min-height:100vh}
aside{width:220px;flex:none;border-right:1px solid var(--line);padding:16px 12px;position:sticky;top:0;height:100vh;overflow:auto}
aside h4{margin:18px 0 6px;font-size:12px;color:var(--mut);font-weight:600}
aside a{display:block;padding:6px 8px;border-radius:6px;color:var(--fg);text-decoration:none}aside a.on{background:var(--card);font-weight:600}
.content{flex:1;min-width:0}.top{display:flex;gap:12px;justify-content:space-between;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line)}
.top a{color:var(--acc);text-decoration:none;font-size:14px}[data-view]{display:none}[data-view].on{display:block}
.quads{display:grid;grid-template-columns:1fr 1fr;gap:10px}.quad{border:1px solid var(--line);border-radius:8px;padding:6px 12px;background:var(--card)}
.quad.wide{margin-top:10px}.quad h3{margin:6px 0;font-size:14px}
.pcard{display:block;padding:8px 0;border-top:1px solid var(--line);color:var(--fg);text-decoration:none;font-size:14px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}.tab.on{background:var(--acc);color:#fff}.gpanel{display:none}.gpanel.on{display:block}
table{border-collapse:collapse;width:100%;font-size:13px;display:block;overflow-x:auto}th,td{border:1px solid var(--line);padding:4px 8px;text-align:left}
nav.bottom{display:none}#drawer{position:fixed;right:0;top:0;bottom:0;width:320px;background:var(--card);border-left:1px solid var(--line);padding:16px;transform:translateX(100%);transition:transform .15s;overflow:auto;z-index:20}
#drawer.open{transform:none}details{margin:4px 0}
@media(max-width:720px){aside{display:none}nav.bottom{display:flex;position:fixed;bottom:0;left:0;right:0;border-top:1px solid var(--line);background:var(--bg);z-index:10}
nav.bottom a{flex:1;text-align:center;padding:12px 4px;color:var(--fg);text-decoration:none}nav.bottom a.on{font-weight:700;color:var(--acc)}
main{padding-bottom:72px}.quads{grid-template-columns:1fr}
#drawer{top:auto;left:0;width:auto;height:45vh;border-left:0;border-top:1px solid var(--line);transform:translateY(100%)}#drawer.open{transform:none}}
"""

SHELL_JS = """
(function(){
 var views=[].slice.call(document.querySelectorAll('[data-view]'));
 function route(){
  var h=(location.hash||'#/workbench').replace(/^#\\/?/,'').split('/');var v=h[0]||'workbench',id=h[1]||'';
  var tab=(v==='person')?'people':v;
  views.forEach(function(el){var on=el.dataset.view===v&&(!el.dataset.id||el.dataset.id===id);el.classList.toggle('on',on)});
  if(!views.some(function(el){return el.classList.contains('on')})){var d=document.querySelector('[data-view=workbench]');d&&d.classList.add('on')}
  [].forEach.call(document.querySelectorAll('[data-nav]'),function(a){a.classList.toggle('on',a.dataset.nav===tab)});
  var d=document.getElementById('drawer');d&&d.classList.remove('open');window.scrollTo(0,0);
 }
 addEventListener('hashchange',route);route();
 document.addEventListener('click',function(ev){
  var t=ev.target.closest&&ev.target.closest('.tab');
  if(t){var box=t.closest('[data-no-capture]');[].forEach.call(box.querySelectorAll('.tab'),function(x){x.classList.toggle('on',x===t)});
   [].forEach.call(box.querySelectorAll('.gpanel'),function(p){p.classList.toggle('on',p.dataset.sub===t.dataset.tab)});}
  var q=ev.target.closest&&ev.target.closest('[data-evidence]');
  if(q){var d=document.getElementById('drawer');d.querySelector('div').textContent=q.dataset.evidence;d.classList.add('open')}
  else if(!ev.target.closest('#drawer')){var d2=document.getElementById('drawer');d2&&d2.classList.remove('open')}
 });
 var first=document.querySelector('.gpanel');if(first){var tabs=document.querySelectorAll('.tab');tabs[0]&&tabs[0].click()}
})();
"""


def _plain_workbench(rd: Redactor) -> str:
    return ("<h1>工作台</h1><p class='mut'>还没有议题。直接说一件事就行，不用先选人、建事件或填表。</p>"
            "<textarea rows='3' placeholder='继续说，或随手记一件事……'></textarea><p class='mut'>已记下 · 0 条待确认</p>")


def render_app(w, store=None, *, mode: str = "code", prep=None, conversations: Optional[list] = None,
               toggle_href: str = "", title: str = "识人系统", auto_quadrants: bool = False) -> str:
    rd = Redactor(w, mode)
    title = "识人系统" if rd.masked else title  # 页面标题常常带议题名：遮挡时不输出
    convs = conversations if conversations is not None else (store.conversations() if store is not None else [])
    # 工作台：最近的议题（没有议题时只有对话与输入框）
    if w.matters:
        mid = list(w.matters)[-1]
        wb = matter_body(workbench_view(w, mid, store, prep=prep), build_matter_graph(w, mid, store, rd), rd)
    else:
        wb = _plain_workbench(rd)
    q = quadrants.render_quadrants(quadrants.build_quadrants(w, rd, auto=auto_quadrants))
    people = [p for p in w.persons.values() if not p.is_self and not p.is_placeholder and not p.merged_into_id]
    person_pages = "".join(
        f"<section data-view='person' data-id='{E(rd.ref(p.id))}'><p><a href='#/people'>← 人物</a></p>"
        f"{render_profile(build_profile(w, p.id, store), rd)}</section>" for p in people)
    graph = render_graph_browser(w, store, rd)
    cal = render_calibration(calibration_panel(w, rd=rd))
    masked = rd.masked
    side_convs = "".join(f"<a href='#/workbench'>{E(rd.t(t or c))}</a>" for c, t, _ in convs[:8]) or "<span class='mut'>暂无</span>"
    nav = [("workbench", "工作台"), ("people", "人物"), ("graph", "关系图")]
    left = "".join(f"<a href='#/{k}' data-nav='{k}'>{n}</a>" for k, n in nav)
    bottom = "".join(f"<a href='#/{k}' data-nav='{k}'>{n}</a>" for k, n in nav)
    # 遮挡时切换链接是常量：调用方给的 URL 常常带着议题名或人名，不能写进遮挡后的页面；真实应用由后端处理这个参数
    href = "?mask=off" if masked else toggle_href
    toggle = (f"<a href='{E(href)}'>{'取消遮挡' if masked else '一键遮挡'}</a>" if (toggle_href or masked) else "")
    return (f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{E(title)}</title><meta name='privacy-capture' content='graph'><style>{CSS}{SHELL_CSS}{PRINT_GUARD_CSS}</style></head><body>"
            f"<div class='app'><aside><b>识人系统</b><h4>导航</h4>{left}<h4>最近对话</h4>{side_convs}"
            f"<h4>校准</h4><a href='#/calibration' data-nav='calibration'>校准面板</a></aside>"
            f"<div class='content'><div class='top'><span class='mut'>显示：{'已遮挡' if masked else ('代号' if mode == 'code' else '姓名')}</span>"
            f"<span><a href='#/calibration'>校准面板</a>　{toggle}</span></div><main>"
            f"<div data-view='workbench'>{wb}</div>"
            f"<div data-view='people'><h1>人物</h1>{q}</div>{person_pages}"
            f"<div data-view='graph'><h1>关系图</h1>{graph}</div>"
            f"<div data-view='calibration'><h1>校准面板</h1>{cal}</div></main></div></div>"
            f"<nav class='bottom'>{bottom}</nav><div id='drawer'><b>原话</b><div></div></div><script>{SHELL_JS}</script></body></html>")
