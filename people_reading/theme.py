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


# 标志：圆相为镜（Kagami = 鏡）。一笔圆相做镜框（起笔重、收笔轻，留一道缺口），
# 镜中下部映着两行错位的青海波。藍与白两色，几何由程序采样生成，边缘平滑。
AI, AI_LIGHT = "#1f3b61", "#5b8fc7"
ENSO_PATH = "M38.36 14.53L39.12 14.06L39.76 13.98L40.38 13.99L40.99 14.05L41.59 14.16L42.18 14.30L42.77 14.46L43.35 14.66L43.92 14.88L44.49 15.13L44.97 15.49L45.45 15.86L45.91 16.25L46.37 16.65L46.81 17.06L47.24 17.48L47.67 17.92L48.08 18.36L48.48 18.82L48.87 19.29L49.24 19.77L49.60 20.26L49.95 20.76L50.29 21.27L50.61 21.79L50.92 22.32L51.21 22.86L51.49 23.41L51.75 23.96L52.00 24.52L52.23 25.09L52.44 25.67L52.64 26.26L52.82 26.85L52.99 27.44L53.13 28.04L53.26 28.65L53.38 29.26L53.47 29.88L53.54 30.49L53.60 31.11L53.64 31.74L53.66 32.36L53.66 32.98L53.64 33.61L53.60 34.23L53.54 34.86L53.47 35.48L53.37 36.10L53.26 36.71L53.12 37.33L52.97 37.93L52.80 38.54L52.61 39.14L52.40 39.73L52.17 40.31L51.92 40.89L51.66 41.46L51.38 42.02L51.08 42.57L50.76 43.12L50.43 43.65L50.08 44.17L49.71 44.68L49.33 45.18L48.93 45.66L48.52 46.13L48.09 46.59L47.65 47.04L47.20 47.47L46.74 47.89L46.26 48.29L45.77 48.67L45.27 49.04L44.76 49.40L44.24 49.74L43.71 50.06L43.17 50.36L42.62 50.65L42.06 50.93L41.50 51.18L40.93 51.42L40.36 51.64L39.78 51.84L39.19 52.03L38.60 52.20L38.01 52.35L37.41 52.48L36.81 52.60L36.21 52.70L35.61 52.78L35.00 52.84L34.40 52.89L33.79 52.92L33.19 52.94L32.58 52.93L31.98 52.91L31.38 52.88L30.78 52.83L30.18 52.76L29.59 52.67L29.00 52.58L28.41 52.46L27.83 52.33L27.25 52.18L26.67 52.02L26.10 51.85L25.54 51.66L24.98 51.45L24.43 51.23L23.88 51.00L23.34 50.75L22.80 50.49L22.28 50.21L21.76 49.92L21.25 49.62L20.74 49.30L20.25 48.97L19.76 48.63L19.28 48.28L18.81 47.91L18.36 47.53L17.91 47.14L17.47 46.74L17.04 46.32L16.62 45.89L16.22 45.46L15.82 45.01L15.44 44.55L15.07 44.08L14.71 43.59L14.37 43.10L14.04 42.60L13.72 42.09L13.41 41.57L13.12 41.04L12.85 40.51L12.59 39.96L12.34 39.41L12.11 38.85L11.93 38.27L11.76 37.69L11.61 37.10L11.48 36.51L11.36 35.92L11.27 35.32L11.19 34.72L11.12 34.12L11.08 33.52L11.05 32.91L11.04 32.31L11.05 31.71L11.08 31.10L11.13 30.50L11.19 29.90L11.27 29.31L11.37 28.71L11.49 28.13L11.63 27.54L11.79 26.96L11.96 26.39L12.15 25.82L12.36 25.26L12.59 24.70L12.83 24.16L13.09 23.62L13.37 23.09L13.66 22.57L13.97 22.07L14.29 21.57L14.63 21.08L14.98 20.61L15.35 20.15L15.73 19.70L16.13 19.26L16.54 18.84L16.96 18.43L17.39 18.04L17.83 17.66L18.28 17.29L18.75 16.94L19.22 16.61L19.70 16.29L20.19 15.99L20.69 15.70L21.20 15.43L21.71 15.17L22.23 14.94L22.75 14.72L23.28 14.51L23.81 14.33L24.35 14.16L24.89 14.00L25.44 13.87L25.98 13.75L26.53 13.64L27.08 13.56L27.62 13.49L28.17 13.41L28.71 13.34L28.74 13.48L28.20 13.56L27.66 13.66L27.14 13.80L26.62 13.96L26.11 14.13L25.60 14.31L25.09 14.51L24.60 14.73L24.10 14.95L23.62 15.19L23.14 15.45L22.67 15.72L22.21 16.00L21.76 16.30L21.32 16.61L20.89 16.93L20.47 17.26L20.05 17.61L19.65 17.97L19.27 18.34L18.89 18.73L18.53 19.12L18.17 19.53L17.84 19.95L17.51 20.37L17.20 20.81L16.91 21.25L16.63 21.71L16.36 22.17L16.11 22.64L15.87 23.12L15.65 23.60L15.45 24.09L15.26 24.58L15.09 25.08L14.94 25.59L14.80 26.09L14.68 26.60L14.57 27.12L14.48 27.63L14.41 28.15L14.35 28.67L14.32 29.18L14.29 29.70L14.29 30.22L14.30 30.73L14.32 31.24L14.36 31.75L14.42 32.26L14.50 32.76L14.58 33.26L14.69 33.76L14.80 34.25L14.94 34.73L15.08 35.21L15.24 35.68L15.42 36.15L15.60 36.61L15.80 37.06L16.02 37.50L16.21 37.95L16.42 38.39L16.64 38.82L16.87 39.25L17.11 39.67L17.36 40.08L17.63 40.48L17.90 40.88L18.18 41.27L18.48 41.64L18.78 42.01L19.10 42.38L19.42 42.73L19.75 43.07L20.09 43.40L20.43 43.73L20.79 44.04L21.15 44.35L21.52 44.64L21.90 44.93L22.28 45.20L22.67 45.47L23.07 45.72L23.47 45.97L23.88 46.20L24.30 46.43L24.72 46.64L25.14 46.84L25.57 47.04L26.01 47.22L26.44 47.39L26.89 47.55L27.34 47.69L27.79 47.83L28.24 47.95L28.70 48.07L29.16 48.17L29.63 48.26L30.10 48.34L30.57 48.40L31.04 48.45L31.51 48.49L31.99 48.52L32.46 48.53L32.94 48.54L33.42 48.53L33.89 48.50L34.37 48.46L34.85 48.41L35.33 48.35L35.80 48.27L36.27 48.18L36.75 48.07L37.21 47.95L37.68 47.82L38.14 47.68L38.60 47.51L39.06 47.34L39.51 47.15L39.95 46.95L40.39 46.74L40.82 46.51L41.25 46.27L41.67 46.01L42.08 45.74L42.48 45.46L42.88 45.17L43.26 44.86L43.64 44.55L44.00 44.22L44.36 43.87L44.71 43.52L45.04 43.16L45.36 42.78L45.67 42.40L45.97 42.00L46.26 41.60L46.53 41.18L46.79 40.76L47.03 40.33L47.26 39.89L47.48 39.45L47.68 39.00L47.87 38.54L48.04 38.08L48.20 37.61L48.34 37.14L48.47 36.66L48.58 36.18L48.68 35.70L48.76 35.21L48.83 34.73L48.88 34.24L48.91 33.75L48.93 33.26L48.94 32.77L48.93 32.28L48.90 31.79L48.86 31.31L48.81 30.82L48.74 30.34L48.65 29.87L48.55 29.39L48.44 28.92L48.32 28.46L48.18 28.00L48.02 27.54L47.86 27.09L47.68 26.65L47.49 26.21L47.29 25.78L47.07 25.35L46.85 24.93L46.61 24.52L46.36 24.12L46.10 23.73L45.84 23.34L45.56 22.96L45.27 22.59L44.97 22.22L44.67 21.87L44.35 21.53L44.03 21.19L43.70 20.86L43.36 20.54L43.01 20.23L42.66 19.94L42.29 19.65L41.92 19.37L41.55 19.09L41.24 18.73L40.92 18.36L40.60 18.00L40.26 17.63L39.92 17.25L39.58 16.86L39.23 16.46L38.88 16.03L38.54 15.53L38.31 14.67Z"


def mark_svg(size: int = 64, *, title: str = "") -> str:
    lab = f"<title>{title}</title>" if title else ""
    aria = "role='img' aria-label='Kagami'" if title else "aria-hidden='true' focusable='false'"
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64' width='{size}' height='{size}' {aria}>{lab}"
            "<rect width='64' height='64' rx='14' fill='#ffffff'/>"
            f"<path d='{ENSO_PATH}' fill='{AI}'/>"
            "<defs><clipPath id='kg-in'><circle cx='32' cy='32' r='15.6'/></clipPath></defs>"
            f"<g clip-path='url(#kg-in)' fill='#ffffff' stroke='{AI_LIGHT}' stroke-width='1.5'>"
            "<path d='M18.5 46a6.5 6.5 0 0 1 13.0 0M20.8 46a4.2 4.2 0 0 1 8.4 0M23.1 46a1.9 1.9 0 0 1 3.8 0M32.5 46a6.5 6.5 0 0 1 13.0 0M34.8 46a4.2 4.2 0 0 1 8.4 0M37.1 46a1.9 1.9 0 0 1 3.8 0'/>"
            "<rect x='10' y='49.5' width='44' height='10' fill='#ffffff' stroke='none'/>"
            "<path d='M11.5 49.5a6.5 6.5 0 0 1 13.0 0M13.8 49.5a4.2 4.2 0 0 1 8.4 0M16.1 49.5a1.9 1.9 0 0 1 3.8 0M25.5 49.5a6.5 6.5 0 0 1 13.0 0M27.8 49.5a4.2 4.2 0 0 1 8.4 0M30.1 49.5a1.9 1.9 0 0 1 3.8 0M39.5 49.5a6.5 6.5 0 0 1 13.0 0M41.8 49.5a4.2 4.2 0 0 1 8.4 0M44.1 49.5a1.9 1.9 0 0 1 3.8 0'/></g>"
            "</svg>")


def mark_data_uri() -> str:
    import base64
    return "data:image/svg+xml;base64," + base64.b64encode(mark_svg().encode()).decode()


BRAND = "Kagami"
