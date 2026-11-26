"""模型输出 schema（手写校验，无第三方依赖）。返回错误列表；空表示合格。"""
from __future__ import annotations

INTENTS = ("record", "question", "unclear")
KINDS = ("behavior", "quote", "hearsay")


def _opt_bool(v) -> bool:
    return v is None or isinstance(v, bool)


def validate_extraction(d) -> list:
    errs: list = []
    if not isinstance(d, dict):
        return ["顶层必须是对象"]
    if d.get("intent") not in INTENTS:
        errs.append("intent 必须是 record/question/unclear")
    for k in ("reply", "clarifying_question"):
        if d.get(k) is not None and not isinstance(d[k], str):
            errs.append(f"{k} 必须是字符串或 null")
    it = d.get("interaction")
    if it is not None:
        if not isinstance(it, dict):
            errs.append("interaction 必须是对象或 null")
        else:
            for k in ("cross_dept", "responsible_named"):
                if not _opt_bool(it.get(k)):
                    errs.append(f"interaction.{k} 必须是 true/false/null")
            if it.get("submitter") is not None and not isinstance(it["submitter"], str):
                errs.append("interaction.submitter 必须是字符串或 null")
    drafts = d.get("drafts")
    if not isinstance(drafts, list):
        return errs + ["drafts 必须是数组"]
    for i, x in enumerate(drafts):
        p = f"drafts[{i}]"
        if not isinstance(x, dict):
            errs.append(p + " 必须是对象")
            continue
        if x.get("kind") not in KINDS:
            errs.append(p + ".kind 非法")
        for k in ("reporter", "subject", "content", "source_quote"):
            if not isinstance(x.get(k), str) or not x[k].strip():
                errs.append(f"{p}.{k} 必须是非空字符串")
        if not isinstance(x.get("is_interpretation"), bool):
            errs.append(p + ".is_interpretation 必须是布尔")
        if not _opt_bool(x.get("occurred")):
            errs.append(p + ".occurred 必须是 true/false/null")
        if x.get("behavior_key") is not None and not isinstance(x["behavior_key"], str):
            errs.append(p + ".behavior_key 必须是字符串或 null")
    return errs


SYSTEM_EXTRACT = """你是中文职场记录助手，只做结构化抽取，不做评价，不推断性格或情绪。
只输出一个 JSON 对象，字段：
intent: "record"(纯记录) | "question"(用户在提问) | "unclear"
reply: 用户在提问时的直接回答，否则 null
clarifying_question: 为补全事实最多追问一个问题，没有则 null
interaction: {"cross_dept": true/false/null, "submitter": 提案提交人姓名或"我"或 null, "responsible_named": 材料是否写明负责人 true/false/null}；用户没说的一律 null，不得猜
drafts: 数组，每项 {"kind":"behavior|quote|hearsay","reporter":说话/看到的人("我"或姓名),"subject":行为的主体姓名,"content":一句话概括,"is_interpretation":转述中的看法或情绪为 true,"source_quote":必须逐字摘自用户原话的片段,"behavior_key":"ask_owner"或null,"occurred":true/false/null}
亲眼所见的行为 kind=behavior；别人转述 kind=hearsay；原话直接引语 kind=quote。
同一行为被重复描述（如“连问三次”）只写一条。source_quote 必须是原文的连续片段，不得改写。"""
