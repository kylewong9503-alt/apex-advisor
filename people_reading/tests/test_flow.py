"""一问一答主界面：会前选择 → 会上那一句；会后开放题（不给候选）→ 核对 → 结果。遮挡、确定性、图案纯装饰。"""
import xml.dom.minidom as md

from people_reading import demo_flow
from people_reading.flow import build_prep_steps, build_settle_steps, pattern, render_flow
from people_reading.privacy import Redactor


def _built():
    return demo_flow.build()


def test_prep_flow_has_choices_then_answer_with_provenance_marks():
    w, prep, settle = _built()
    kinds = [s["kind"] for s in prep]
    assert kinds[0] == "ask" and kinds[-1] == "answer"
    marks = [o["mark"] for o in prep[0]["options"]]
    assert marks[0] == "you" and "model" in marks  # 你的判断 / 模型提出 用记号区分
    ans = prep[-1]
    assert ans["buttons"][0] == "记为行动"  # 行动与预测是两个独立按钮
    assert all(len(s["options"]) <= 4 for s in prep if s["kind"] == "ask")


def test_settlement_first_step_is_open_without_candidates():
    w, prep, settle = _built()
    assert settle[0]["kind"] == "open" and "options" not in settle[0]
    page = render_flow(settle[:1], rd=Redactor(w, "code"))
    assert "<textarea" in page and "role='radio'" not in page  # 首屏不出现任何候选答案
    assert settle[-1]["kind"] == "result" and settle[-1]["big"].endswith("×")


def test_masked_flow_has_no_names_quotes_or_title():
    w, prep, settle = _built()
    secret = ["方明", "专家", "职责", "集团", "F 的", "先问"]
    for steps in (prep, settle):
        page = render_flow(steps, rd=Redactor(w, "masked"), title="争取集团专家支持")
        for s in secret:
            assert s not in page.split("<script>")[0], s
        assert "<title>识人</title>" in page


def test_flow_is_deterministic_and_has_no_external_resources():
    w, prep, settle = _built()
    a = render_flow(prep, rd=Redactor(w, "code"))
    assert a == render_flow(prep, rd=Redactor(w, "code"))
    for bad in ("http://", "https://", "@import", "<link", "src="):
        assert bad not in a.replace("http://www.w3.org", "")


def test_pattern_svg_is_well_formed_and_decorative():
    for v in ("classic", "banded", "dotted", "ripple", "tide", "fade", "yoru"):
        svg = pattern(v, 560, 300, uid="t")
        md.parseString(svg)  # 必须是合法 XML（早先一个没引号的属性吞掉过元素）
        assert "aria-hidden='true'" in svg and "<text" not in svg


def test_html_escaping_in_options():
    steps = [{"kind": "ask", "q": "<b>x</b>", "cue": "", "selected": None,
              "options": [{"text": "<script>alert(1)</script>", "mark": "", "sub": ""}]}]
    page = render_flow(steps)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
