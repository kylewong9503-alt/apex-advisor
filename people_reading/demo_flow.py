"""一问一答主界面演示：python -m people_reading.demo_flow
写出 demo/flow_prep.html（会前：选择 → 会上那一句）与 demo/flow_settle.html（会后：开放题 → 核对 → 结果），及各自的遮挡版。
模型输出为脚本化演示数据；其余全部由程序算出。
"""
from __future__ import annotations

import pathlib
import tempfile

from .flow import build_prep_steps, build_settle_steps, render_flow
from .privacy import Redactor
from .settle_flow import SettlementSession
from .tests import test_step4 as T
from .workbench import workbench_view
from .judgment import judgment_card

DIR = pathlib.Path(__file__).parent / "demo"


def build():
    w, _st, a, b = build_store(full=True)
    return w, a, b


def build_store(full: bool = False):
    w, st, it, pm, mid, iid = T.c2(pathlib.Path(tempfile.mkdtemp()))
    it.model.outputs = [T.PREP_OUT, T.C3_OUT, {"branch": "A"}, T.REVISED, T.report_out(T.REPORT_DRAFTS), T.proposal(), T.REVIEW]
    prep = pm.prepare(); pm.confirm_categories(); pm.formalize(T.DIST_H)
    prep_steps = build_prep_steps(workbench_view(w, mid, st, prep=prep))
    pm.answer_fork("c1", T.C3_TEXT); pm.revise_after_fork("A", T.C3_TEXT)
    pm.record_action("会谈结束时 F 答应引荐一位具体的集团专家", ["分工说明", "专家介入案例"])
    pm.lock_prediction(T.DIST_P, T.S.PREMISE_F1)
    sess = SettlementSession(it, mid, iid, it.model, "第二天 · 与 F 的会谈")
    screen = sess.first_screen()
    sess.submit_report("c1", T.REPORT)
    pqs = sess.premise_questions()
    sess.answer_premise(pqs[0]["premise_text"], "都没有", "分工说明是他问谁牵头时才拿出来的")
    prop = sess.propose_outcome()
    sess.confirm_outcome()
    rev = sess.review(confirm_intervention="partial")
    cards = [c for c in rev["cards"] if c["id"] == "H1"]
    out = (prep_steps, build_settle_steps(screen, pqs, prop, cards))
    return (w, st, *out) if full else (w, st)


def main() -> None:
    w, prep, settle = build()
    for name, steps, lib in (("flow_prep", prep, ""), ("flow_settle", settle, "step5.html")):
        (DIR / f"{name}.html").write_text(render_flow(steps, rd=Redactor(w, "code"), title="识人", library_href=lib), encoding="utf-8")
        (DIR / f"{name}_masked.html").write_text(render_flow(steps, rd=Redactor(w, "masked"), library_href=lib), encoding="utf-8")
    print("已写入", DIR / "flow_prep.html", "和", DIR / "flow_settle.html")


if __name__ == "__main__":
    main()
