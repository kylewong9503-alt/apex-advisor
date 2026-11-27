"""第 5 步演示：python -m people_reading.demo_step5 → demo/step5.html（代号）与 demo/step5_masked.html（一键遮挡）

用第 4 步的 CFO 会谈数据。模型输出为脚本化演示数据；其余全部由程序算出。
真实应用里“一键遮挡”由后端重新渲染；这里用两个静态页面互相链接来演示效果。
"""
from __future__ import annotations

import pathlib
import tempfile

from .shell import render_app
from .tests import test_step5 as T5
from .world import Person

DIR = pathlib.Path(__file__).parent / "demo"


def build():
    w, st, mid = T5.cfo(pathlib.Path(tempfile.mkdtemp()))
    w.add_person(Person("G", "高峰", code="G", influence=5))
    w.add_position("G", "COO", "FIN", 9)
    w.set_quadrant("G", "远-深", influence=5)
    return w, st


def main() -> None:
    w, st = build()
    (DIR / "step5.html").write_text(render_app(w, st, mode="code", toggle_href="step5_masked.html"), encoding="utf-8")
    (DIR / "step5_masked.html").write_text(render_app(w, st, mode="masked", toggle_href="step5.html"), encoding="utf-8")
    print("已写入", DIR / "step5.html", "和", DIR / "step5_masked.html")


if __name__ == "__main__":
    main()
