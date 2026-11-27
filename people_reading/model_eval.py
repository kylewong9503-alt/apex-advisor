"""本地模型选型测试：python -m people_reading.model_eval --model deepseek-r1:14b --repeats 5

在你自己的机器上跑（需要先 `ollama serve` 并拉取模型）。走的是真实的录入流水线（Intake），
所以“schema 合格、原话可定位、不编造、不猜未说的事实”都是按系统实际使用方式检验的。
通过线：每项检查的通过率 ≥ 0.9（可用 --bar 调整）。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .intake import Intake
from .llm import DEFAULT_BASE_URL, DEFAULT_MODEL, LocalModel, make_model
from .store import Store
from .world import Person, World

TEXT1 = "今天会上，X 连问三次谁负责。散会后甲说，X 很不满。"
PENDING_Q = "三次都是针对同一份提案吗？"


def _setup(model):
    d = tempfile.mkdtemp()
    st = Store(str(Path(d) / "eval.db"), "eval-pass")
    it = Intake(st, World(), model)
    for pid in ("X", "甲", "乙"):
        it.register_person(Person(pid, pid))
    st.new_conversation("c")
    return st, it


def _drafts(st):
    return [d["proposed"] for d in st.drafts()]


def case_record_two(model):
    st, it = _setup(model)
    r = it.handle("c", TEXT1)
    ds = _drafts(st)
    beh = [d for d in ds if d["kind"] == "behavior"]
    hs = [d for d in ds if d["kind"] == "hearsay"]
    return {
        "schema_ok": not r.fallback,
        "quotes_verbatim": not r.fallback and r.dropped == 0,
        "two_drafts": len(ds) == 2,
        "behavior_counted_once": len(beh) == 1 and beh[0]["subject_name"] == "X",
        "hearsay_reporter_and_interpretation": len(hs) == 1 and hs[0]["reporter_name"] == "甲" and hs[0]["is_interpretation"] is True,
        "no_guessed_facts": all(v is None for v in it.facts.get("INT-c-1", {}).values()),
        "reply_one_line": r.text == "已记下 · 2 条待确认",
    }


def case_answer(model):
    st, it = _setup(model)
    st.set_pending("c", {"interaction_id": "INT-c-1", "question": PENDING_Q})
    r = it.handle("c", "是，都是我提交的跨部门提案")
    f = it.facts.get("INT-c-1", {})
    return {
        "schema_ok": not r.fallback,
        "cross_dept_true": f.get("cross_dept") is True,
        "submitter_is_me": f.get("submitter") == "user",
        "responsible_left_unknown": f.get("responsible_named") is None,
    }


def case_question(model):
    st, it = _setup(model)
    r = it.handle("c", "X 最近老是追问我，我该怎么办？")
    return {"schema_ok": not r.fallback, "answers_directly": r.intent == "question" and bool(r.text.strip())}


def case_no_fabrication(model):
    st, it = _setup(model)
    r = it.handle("c", "今天有点累，下午开了个会。")
    return {"schema_ok": not r.fallback, "no_invented_drafts": len(_drafts(st)) == 0}


CHECKS = {
    "record_two": ("schema_ok", "quotes_verbatim", "two_drafts", "behavior_counted_once",
                   "hearsay_reporter_and_interpretation", "no_guessed_facts", "reply_one_line"),
    "answer": ("schema_ok", "cross_dept_true", "submitter_is_me", "responsible_left_unknown"),
    "question": ("schema_ok", "answers_directly"),
    "no_fabrication": ("schema_ok", "no_invented_drafts"),
}
CASES = {"record_two": case_record_two, "answer": case_answer, "question": case_question,
         "no_fabrication": case_no_fabrication}


class Recorder:
    """包一层，记录每次模型调用的提示与原始输出，评测失败时用来看模型到底说了什么。只含合成用例，不含真实数据。"""

    def __init__(self, inner) -> None:
        self.inner, self.log = inner, []
        self.structured = LocalModel.structured.__get__(self)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def chat(self, system: str, user: str) -> str:
        raw = self.inner.chat(system, user)
        self.log.append({"user": user, "raw": raw})
        return raw


def evaluate(model, repeats: int = 3) -> dict:
    """返回 {"rates": {检查名: 通过率}, "errors": {异常类型: 次数}}。异常（如连接失败）如实记录，不当作通过。"""
    passes: dict = {}
    errors: dict = {}
    for name, fn in CASES.items():
        for _ in range(repeats):
            try:
                res = fn(model)
            except Exception as e:
                key = f"{name}:{type(e).__name__}"
                errors[key] = errors.get(key, 0) + 1
                for k in CHECKS[name]:
                    passes.setdefault(f"{name}.{k}", []).append(False)
                continue
            for k, v in res.items():
                passes.setdefault(f"{name}.{k}", []).append(bool(v))
    return {"rates": {k: round(sum(v) / len(v), 3) for k, v in passes.items()}, "errors": errors}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--provider", choices=("local", "deepseek"), default="local",
                    help="deepseek 会把评测用例发往外部服务，需 PR_ALLOW_EXTERNAL=1 与 DEEPSEEK_API_KEY")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--dump", help="把每次调用的提示与模型原始输出写到这个 JSON 文件（贴回来用于调提示词）")
    ap.add_argument("--bar", type=float, default=0.9)
    a = ap.parse_args(argv)
    if a.provider == "deepseek":
        model = make_model({**__import__("os").environ, "PR_PROVIDER": "deepseek",
                                                       "PR_MODEL": "deepseek-chat" if a.model == DEFAULT_MODEL else a.model})
    else:
        model = LocalModel(a.base_url, a.model)  # 非本机地址在这里就会被拒绝
    rec = Recorder(model)
    res = evaluate(rec, a.repeats)
    if a.dump:
        Path(a.dump).write_text(json.dumps({"model": a.model, "provider": a.provider, "result": res, "calls": rec.log},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    bad = {k: v for k, v in res["rates"].items() if v < a.bar}
    bad.update({k: v for k, v in res["errors"].items()})
    print("\n模型调用次数:", model.calls)
    print("结论:", "通过" if not bad else f"未通过 {len(bad)} 项: {sorted(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
