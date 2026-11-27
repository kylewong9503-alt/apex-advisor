"""模型输出 schema（手写校验，无第三方依赖）。返回错误列表；空表示合格。"""
from __future__ import annotations

INTENTS = ("record", "question", "unclear")
KINDS = ("behavior", "quote", "hearsay")
ROLES = ("approver", "referrer", "influencer", "blocker", "informant", "other")
GLOBAL_TYPES = ("depends_on", "trusts", "info_channel", "owes_commitment")


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
    m = d.get("matter")
    if m is not None and (not isinstance(m, dict) or not isinstance(m.get("title"), str) or not m["title"].strip()
                          or (m.get("purpose") is not None and not isinstance(m["purpose"], str))):
        errs.append("matter 必须是 {title, purpose} 或 null")
    rels = d.get("relations", [])
    if not isinstance(rels, list):
        errs.append("relations 必须是数组")
        rels = []
    for i, r in enumerate(rels):
        p = f"relations[{i}]"
        if not isinstance(r, dict):
            errs.append(p + " 必须是对象")
            continue
        if r.get("scope") not in ("global", "matter"):
            errs.append(p + ".scope 必须是 global 或 matter")
        if not isinstance(r.get("from"), str) or not r["from"].strip():
            errs.append(p + ".from 必须是非空字符串")
        if r.get("to") is not None and not isinstance(r["to"], str):
            errs.append(p + ".to 必须是字符串或 null")
        if r.get("role") is not None and r["role"] not in ROLES:
            errs.append(p + ".role 非法")
        if r.get("dimension") not in (None, "can", "will"):
            errs.append(p + ".dimension 必须是 can/will/null")
        if not isinstance(r.get("source_quote"), str) or not r["source_quote"].strip():
            errs.append(p + ".source_quote 必须是非空字符串")
        if r.get("scope") == "matter" and not (isinstance(r.get("matter"), str) and r["matter"].strip()):
            errs.append(p + " 议题内关系必须写 matter 标题")
        if r.get("scope") == "global" and (r.get("role") is not None or r.get("to") is None
                                           or r.get("type") not in GLOBAL_TYPES):
            errs.append(p + f" 全局关系需要 to，且 type 属于 {GLOBAL_TYPES}，不得带 role")
        if r.get("scope") == "matter" and r.get("role") is None and (r.get("to") is None or not r.get("type")):
            errs.append(p + " 议题内关系要么写 role，要么写 to + type")
    pm = d.get("planned_meeting")
    if pm is not None and not (isinstance(pm, dict) and isinstance(pm.get("with"), str) and pm["with"].strip()
                               and isinstance(pm.get("when", ""), (str, type(None)))):
        errs.append("planned_meeting 必须是 {with, when} 或 null")
    hy = d.get("hypotheses", [])
    if not isinstance(hy, list):
        errs.append("hypotheses 必须是数组")
        hy = []
    for i, x in enumerate(hy):
        if not (isinstance(x, dict) and all(isinstance(x.get(k), str) and x[k].strip() for k in ("subject", "statement", "source_quote"))):
            errs.append(f"hypotheses[{i}] 需要非空的 subject/statement/source_quote")
    for key, need in (("commitments", ("from", "to", "content", "source_quote")), ("commitment_updates", ("id", "status", "source_quote"))):
        arr = d.get(key, [])
        if not isinstance(arr, list):
            errs.append(f"{key} 必须是数组")
            continue
        for i, x in enumerate(arr):
            if not (isinstance(x, dict) and all(isinstance(x.get(k), str) and x[k].strip() for k in need)):
                errs.append(f"{key}[{i}] 需要非空的 {'/'.join(need)}")
            elif key == "commitment_updates" and x["status"] not in ("fulfilled", "broken"):
                errs.append(f"{key}[{i}].status 只能是 fulfilled 或 broken")
            elif key == "commitments" and x.get("due_text") is not None and not isinstance(x["due_text"], str):
                errs.append(f"{key}[{i}].due_text 必须是字符串或 null")
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
可选字段：
matter: 用户提到要办的一件事时 {"title":议题名,"purpose":目的一句话}，否则 null
relations: 用户明确说出的人际关系或某人在某议题里的作用，每项 {"scope":"global"(跨议题普遍成立)|"matter"(只限这件事),"matter":议题名(scope=matter 时必填),"from":姓名或"我","to":姓名或null,"type":depends_on|trusts|info_channel|owes_commitment(global)或 can_refer|needs_approval_of|influences(matter),"role":approver|referrer|influencer|blocker|informant|other 或 null,"dimension":"can"(有无权限渠道)|"will"(愿不愿意出手)|null,"source_quote":原话片段}
只记用户明确说的；不得由职务推断意愿或关系；限定于这件事的关系一律 scope=matter。
planned_meeting: 用户提到即将发生的会谈 {"with":对方姓名,"when":时间描述}，否则 null
hypotheses: 用户随口说出的看法（对某人将会怎样的预期）[{"subject":姓名,"statement":一句话,"source_quote":原话片段}]；只记用户说的，不要替用户补充
commitments: 用户转述的明确承诺 [{"from":欠的人(姓名或"我"),"to":被欠的人,"content":承诺内容,"due_text":到期时间的原话描述或null,"source_quote":原话片段}]；只记对方明确说出口的承诺，不要把“可能”“看看”当成承诺
commitment_updates: 用户说某项已记录的承诺兑现或没兑现 [{"id":提示里给出的承诺编号,"status":"fulfilled"|"broken","source_quote":原话片段}]，编号只能用提示里列出的
同一行为被重复描述（如“连问三次”）只写一条。source_quote 必须是原文的连续片段，不得改写。"""
