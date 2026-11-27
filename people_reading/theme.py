"""视觉主题：Kagami。从标志（圆相镜）出发：藍与浅藍、白底、一笔圆相、镜中青海波；令牌与图案沿用青海波设计系统的结构。全部内联，页面不请求任何外部资源（字体、图片、脚本都不外链）。

令牌取自设计系统 tokens.json（白昼 / 夜海两套主题，随系统 prefers-color-scheme 切换）。
旧代码里用到的 --bg / --fg / --mut / --line / --card / --acc / --warn 保留为别名，指向新令牌，渲染器无需逐个改。
规则（来自设计系统）：蓝系为主、朱只出现一处（焦点环与“出错/待决定”）；方正（按钮 2px、卡片 6px）；线而不是影；
图案只在实底区，文字不压图案；每页最多一条 WaveBand。字体只用系统自带的明朝/黑体回退，不加载网络字体。
"""
from __future__ import annotations

import math

# Inter（可变字体，拉丁子集，48KB，SIL OFL 1.1，见 static/LICENSE-Inter.txt）：英文与数字走 Inter，汉字回退 Noto Sans SC。
# 字体文件由服务自己提供（/inter.woff2），页面不请求任何外部字体；演示页把它放在同目录。
FONT_FACE = ("@font-face{font-family:'Inter';font-style:normal;font-weight:100 900;font-display:swap;src:url('inter.woff2') format('woff2');"
             "unicode-range:U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+2000-206F,U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD}\n")
FONT_FILE = __import__("pathlib").Path(__file__).parent / "static" / "inter.woff2"


def copy_font(dest_dir) -> None:
    """把 Inter 放到演示页所在目录（静态演示用相对路径加载）。"""
    import shutil
    shutil.copyfile(FONT_FILE, __import__("pathlib").Path(dest_dir) / "inter.woff2")


TOKENS_CSS = FONT_FACE + """:root{
--surface:#f6f8fb;--surface-raised:#ffffff;--surface-sunk:#edf2f8;--line:#dbe3ec;--line-strong:#7b8696;
--ink:#14213a;--ink-muted:#546072;--ai:#1f3b61;--on-ai:#ffffff;--kon:#1f3b61;--on-kon:#ffffff;
--hanada:#5b8fc7;--asagi:#8fb5dc;--mizu:#e4eef8;--shu:#a63a23;--on-shu:#fbf9f4;--shu-soft:#f3dfd6;--kin:#7f5c1a;--kin-soft:#efe3c8;
--focus:var(--ai);
--space-1:4px;--space-2:8px;--space-3:12px;--space-4:16px;--space-6:24px;--space-8:32px;--space-12:48px;--space-16:64px;
--radius-sm:8px;--radius-md:14px;
--font-display:"Inter","Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
--font-sans:"Inter","Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;
--font-mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
--bg:var(--surface);--fg:var(--ink);--mut:var(--ink-muted);--card:var(--surface-raised);--acc:var(--ai);--warn:var(--shu)}
@media(prefers-color-scheme:dark){:root{
--surface:#0e1622;--surface-raised:#152131;--surface-sunk:#0a111b;--line:#263447;--line-strong:#6b7a8f;
--ink:#e8eef6;--ink-muted:#a1adbd;--ai:#8fb5dc;--on-ai:#0e1622;--kon:#1f3b61;--on-kon:#ffffff;
--hanada:#5b8fc7;--asagi:#7aa6d6;--mizu:#17263a;--shu:#f08262;--on-shu:#0f151e;--shu-soft:#3a1d16;--kin:#d4a659;--kin-soft:#33270f}}
"""

# 基础排版与组件：方正、细线、不用投影；.card 退化为“一条发丝线 + 内容”，不再是一个个盒子（对应“卡片太多”的反馈）
BASE_CSS = TOKENS_CSS + """
*{box-sizing:border-box}
body{background:var(--surface);color:var(--ink);font:15px/26px var(--font-sans);font-feature-settings:'tnum' 1,'cv11' 1;margin:0;-webkit-font-smoothing:antialiased}
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
def enso_path(cx: float = 32, cy: float = 32, R: float = 19, start: float = -70, sweep: float = 330,
              wmax: float = 5.0, n: int = 200) -> str:
    """一笔圆相的轮廓：沿圆周密集采样，起笔重、收笔轻，手写的微小起伏。确定性：同参数同输出。"""
    import math
    outer, inner = [], []
    for i in range(n + 1):
        t = i / n
        a = math.radians(start + sweep * t)
        w = wmax * min(1, t / 0.05) ** 0.5 * min(1, (1 - t) / 0.3) ** 0.9 * (1 - 0.25 * t)
        w = max(w, 0.15 * wmax / 5)
        rr = R + 0.5 * (R / 19) * math.sin(2 * a + 0.6)
        outer.append((cx + (rr + w / 2) * math.cos(a), cy + (rr + w / 2) * math.sin(a)))
        inner.append((cx + (rr - w / 2) * math.cos(a), cy + (rr - w / 2) * math.sin(a)))
    pts = outer + inner[::-1]
    return "M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in pts) + "Z"


ENSO_PATH = enso_path()


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
