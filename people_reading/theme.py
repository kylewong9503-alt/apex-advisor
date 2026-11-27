"""视觉主题：青海波（Seigaiha）设计系统的令牌与图案。全部内联，页面不请求任何外部资源（字体、图片、脚本都不外链）。

令牌取自设计系统 tokens.json（白昼 / 夜海两套主题，随系统 prefers-color-scheme 切换）。
旧代码里用到的 --bg / --fg / --mut / --line / --card / --acc / --warn 保留为别名，指向新令牌，渲染器无需逐个改。
规则（来自设计系统）：蓝系为主、朱只出现一处（焦点环与“出错/待决定”）；方正（按钮 2px、卡片 6px）；线而不是影；
图案只在实底区，文字不压图案；每页最多一条 WaveBand。字体只用系统自带的明朝/黑体回退，不加载网络字体。
"""
from __future__ import annotations

import math

TOKENS_CSS = """:root{
--surface:#f4f1e9;--surface-raised:#fbf9f4;--surface-sunk:#e9e4d8;--line:#d6cfbf;--line-strong:#7a808c;
--ink:#1a2130;--ink-muted:#555d6c;--ai:#1f3b61;--on-ai:#f4f1e9;--kon:#14243d;--on-kon:#f4f1e9;
--hanada:#35668f;--asagi:#3f8c96;--mizu:#d5e2ea;--shu:#a63a23;--on-shu:#fbf9f4;--shu-soft:#f3dfd6;--kin:#7f5c1a;--kin-soft:#efe3c8;
--focus:var(--ai);
--space-1:4px;--space-2:8px;--space-3:12px;--space-4:16px;--space-6:24px;--space-8:32px;--space-12:48px;--space-16:64px;
--radius-sm:2px;--radius-md:6px;
--font-display:"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
--font-sans:"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
--font-mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
--bg:var(--surface);--fg:var(--ink);--mut:var(--ink-muted);--card:var(--surface-raised);--acc:var(--ai);--warn:var(--shu)}
@media(prefers-color-scheme:dark){:root{
--surface:#0f151e;--surface-raised:#17202c;--surface-sunk:#0a0f16;--line:#2a3544;--line-strong:#6a7586;
--ink:#ebe6db;--ink-muted:#a3abb7;--ai:#93b6dc;--on-ai:#0f151e;--kon:#27466e;--on-kon:#f4f1e9;
--hanada:#4f82b0;--asagi:#4fa3ab;--mizu:#1a2b3f;--shu:#f08262;--on-shu:#0f151e;--shu-soft:#3a1d16;--kin:#d4a659;--kin-soft:#33270f}}
"""

# 基础排版与组件：方正、细线、不用投影；.card 退化为“一条发丝线 + 内容”，不再是一个个盒子（对应“卡片太多”的反馈）
BASE_CSS = TOKENS_CSS + """
*{box-sizing:border-box}
body{background:var(--surface);color:var(--ink);font:15px/26px var(--font-sans);margin:0;-webkit-font-smoothing:antialiased}
main{max-width:720px;margin:0 auto;padding:var(--space-6) var(--space-4) var(--space-12)}
h1{font:600 26px/36px var(--font-display);letter-spacing:.04em;margin:0 0 var(--space-1)}
h2{font:700 18px/28px var(--font-sans);margin:var(--space-8) 0 var(--space-2)}
h3{font:700 15px/24px var(--font-sans);margin:var(--space-4) 0 var(--space-1)}
.mut{color:var(--ink-muted);font-size:13px;line-height:22px}
a{color:var(--ai);text-underline-offset:4px}
.card{border:0;border-top:1px solid var(--line);background:transparent;border-radius:0;padding:var(--space-3) 0;margin:0}
.card+.card{margin-top:0}
.row{padding:var(--space-1) 0;border-top:1px solid var(--line)}
.row b{display:block;font:500 11px/16px var(--font-sans);letter-spacing:.16em;color:var(--ink-muted)}
textarea,input[type=text]{width:100%;font:inherit;padding:var(--space-2) var(--space-3);border:1px solid var(--line-strong);border-radius:var(--radius-sm);background:var(--surface-raised);color:var(--ink)}
button{font:500 13px/1 var(--font-sans);letter-spacing:.04em;min-height:32px;padding:0 var(--space-4);margin:2px var(--space-1) 2px 0;border:1px solid var(--line-strong);border-radius:var(--radius-sm);background:var(--surface-raised);color:var(--ink);cursor:pointer}
button:hover{border-color:var(--ai);color:var(--ai)}
button:disabled{opacity:.45;cursor:not-allowed}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
svg{color:var(--ink)}
code{font:13px/20px var(--font-mono)}
.mk{display:inline-block;vertical-align:middle;margin:-2px 8px 0 0;flex:none}
.mk-user{width:10px;height:10px;border-radius:50%;background:var(--ai)}
.mk-model{width:10px;height:10px;border-radius:50%;border:1.5px dashed var(--hanada)}
.row.recall{border-top-style:dotted;border-top-color:var(--kin)}.row.recall b{color:var(--kin)}
.wave{display:block;width:100%;height:20px;margin:0 0 var(--space-6);overflow:hidden}
"""


def wave_band(r: int = 10, rings: int = 3, width: int = 960) -> str:
    """一条横向青海波窄带（线描 classic，单色 ai，地色 surface）。纯装饰：aria-hidden，不含任何文字。

    几何：等半径 r、横距 2r、行距 r/2、隔行错位 r，自上而下逐行叠压（后一行遮住前一行的下半）。
    颜色只写令牌 var(--…)，夜海主题自动变色。固定参数 → 同输入同输出，不含随机。
    """
    rows = 3
    h = int(r * 2)
    cols = int(math.ceil(width / (2 * r))) + 2
    parts = [f"<svg class='wave' viewBox='0 0 {width} {h}' preserveAspectRatio='xMidYMin slice' aria-hidden='true' focusable='false'>"]
    sw = round(max(r / 22, 0.6), 2)
    for row in range(rows):
        cy = row * r / 2
        off = r if row % 2 else 0
        for c in range(-1, cols):
            cx = c * 2 * r + off
            for i in range(rings, 0, -1):
                rad = round(r * i / rings, 2)
                fill = "var(--surface)" if i == rings else "none"
                # 外环填地色以遮住上一行；内环只画线
                parts.append(f"<circle cx='{cx}' cy='{round(cy, 2)}' r='{rad}' fill='{fill}' stroke='var(--ai)' stroke-width='{sw}'/>")
    parts.append("</svg>")
    return "".join(parts)


# 标志：一面柄镜（Kagami = 鏡）。极简几何：藍底、白色镜面与镜柄、两道斜向反光（实心镜面，避免读成放大镜）。只用两种颜色。
def mark_svg(size: int = 64, *, title: str = "") -> str:
    lab = f"<title>{title}</title>" if title else ""
    aria = "role='img' aria-label='Kagami'" if title else "aria-hidden='true' focusable='false'"
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64' width='{size}' height='{size}' {aria}>{lab}"
            "<rect width='64' height='64' rx='14' fill='#1f3b61'/>"
            "<circle cx='32' cy='27' r='16' fill='#ffffff'/>"
            "<rect x='29' y='41' width='6' height='13' rx='3' fill='#ffffff'/>"
            "<path d='M23 27l9-9M27 31l9-9' stroke='#1f3b61' stroke-width='2.6' stroke-linecap='round' opacity='.35'/></svg>")


def mark_data_uri() -> str:
    import base64
    return "data:image/svg+xml;base64," + base64.b64encode(mark_svg().encode()).decode()


BRAND = "Kagami"
