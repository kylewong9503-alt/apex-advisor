"""DeepSeek Skills 套件（设计文档《识人系统 DeepSeek Skills 套件设计》）：6 个 skill，2 条闭环。

  “听懂你”   ① 需求澄清  ② 发言复述  ③ 理解回测   —— 你的确认/修改/不记 是唯一的标签来源
  “帮你判断” ④ 档案补全  ⑤ 建议与理由 ⑥ 规律提炼  —— 只有变成锁定预测、结算后才算数（engine.calibration）

分工不变：数字与打分全部由程序算；模型只写草稿与叙述，且都要过程序校验：
  - 原话片段由程序定位（R3），定位不到就丢；复述不能出现原话里没有的人名、数字。
  - 叙述里的数字必须在证据包里（R7 numbers_ok）；不得出现 FORBIDDEN / OVERCLAIMS（R12）。
  - 建议的理由只能引用你确认过的观察，不能引用模型自己写的东西。
  - 任何写入（词典、草稿）都要你确认。
标签与词典存在加密的 entity 表里，和原话同等对待。
"""
from __future__ import annotations

from typing import Optional

from .cards import FORBIDDEN
from .intake import find_span
from .ranking import discriminating_signals
from .schema import SYSTEM_EXTRACT, validate_extraction
from .settle_flow import OVERCLAIMS
from .skills import BUILTIN, NUM, SkillVersion, build_packet, numbers_ok
from .stats import MIN_KNOWN
from .world import SELF, World

# ───────────────────────── 六个 skill 的定义（五项 + 版本） ─────────────────────────
CLARIFY = SkillVersion(
    "需求澄清", 1, "判断你这句话是记录、提问、要建议、要更正还是要回顾；找出缺的事实；最多追问一个问题。",
    applicable_questions=(), inputs=("你的原话", "当前议题", "你的说法词典（只含你确认过的条目）"),
    steps=("判断意图", "列出已填 / 未填槽位，未说的一律 null", "至多一个追问", "提出词典条目草稿"),
    output_format="意图 + 槽位 + 至多一个追问 + 词典条目草稿", allowed_actions=("词典条目草稿",))
RESTATE = SkillVersion(
    "发言复述", 1, "把你一段话复述成不超过 3 句“我理解你说的是……”，每句挂一段原话，并分清来源。",
    applicable_questions=("总结我的发言", "复述"), inputs=("原话", "需求澄清的输出", "词典"),
    steps=("挑出至多 3 个要点", "每个要点挂一段原话", "标出：亲眼所见 / 别人转述 / 你的看法 / 你对某人的预期"),
    output_format="复述句（每句：文字、原话、来源类别）；你对每句点 对 / 不对", allowed_actions=())
BACKTEST = SkillVersion(
    "理解回测", 1, "用你历史上的确认 / 修改 / 不记当标准答案，给理解类 skill 打分并列出错例。",
    applicable_questions=("回测", "听得准不准"), inputs=("标签集", "待测版本", "当时的词典与人物表"),
    steps=("冻结回测集，留最近 20% 只验收", "按时间切分重放", "程序算指标、列错例", "给出能否启用新版本的建议"),
    output_format="指标报告 + 错例 + 启用建议（启用由你决定）", allowed_actions=())
PROFILE_GAPS = SkillVersion(
    "档案补全", 1, "列出一个人三层（职务 / 和我的关系 / 议题角色）里的空缺和待确认项，按“补哪条最值”排序。",
    applicable_questions=("还缺什么", "完善档案", "补全"), inputs=("人物档案", "未完成的待办", "三维对照", "观察排序"),
    steps=("逐层找空缺", "待确认项单列", "按值得程度排序（零代价的观察优先）"),
    output_format="补全清单：看什么 / 能分清什么 / 代价", allowed_actions=("关系边、议题角色、watch 草稿",))
ADVICE = SkillVersion(
    "建议与理由", 1, "就一个议题或一个人，给不超过 3 条建议，每条五栏，且可变成锁定预测接受检验。",
    applicable_questions=("给点建议", "我该怎么做", "有什么建议"), inputs=("议题目的", "档案补全", "规则对累计倍数", "规律卡"),
    steps=("读证据包", "每条写：做什么 / 理由（引用观察）/ 依赖哪个尚未排除的解释 / 何时失效 / 可锁定的预测草稿"),
    output_format="至多 3 条，每条五栏", allowed_actions=("预测草稿",))
PATTERNS = SkillVersion(
    "规律提炼", 1, "把程序算出的条件发生率和两两比较，写成可检验的规律卡。",
    applicable_questions=("有什么规律", "总结规律"), inputs=("条件发生率", "规则对累计倍数与次数"),
    steps=("按人、行为、条件取发生率", "标出是否达到最少已知机会数", "按结算结果给状态：候选 → 检验中 → 有支持"),
    output_format="规律卡：陈述 / 条件 / a/b / 是否够数 / 状态", allowed_actions=("规则草稿",))
SUITE = (CLARIFY, RESTATE, BACKTEST, PROFILE_GAPS, ADVICE, PATTERNS)
for _sv in SUITE:
    BUILTIN[_sv.skill] = _sv  # 注册进 SkillRegistry（点名或按适用问题自选）

EXTRACT_ID = "录入抽取@v1"  # 现有录入抽取的版本号：标签按它记


def _s(x) -> bool:
    return isinstance(x, str) and bool(x.strip())


def _clean(text: str) -> bool:
    return not any(w in text for w in FORBIDDEN)


# ───────────────────────── 标签（闭环 A 的唯一数据来源） ─────────────────────────
def record_label(store, draft: dict, action: str, final: Optional[dict], skill: str = EXTRACT_ID) -> None:
    """你每次确认 / 修改 / 不记 一条草稿，记一条标签：模型原版、你的最终版、动作、版本。加密存储。"""
    lid = f"L{len(store.entities('label')) + 1}"
    store.save_entity("label", lid, {"id": lid, "draft_id": draft["id"], "message_id": draft["message_id"],
                                     "spans": draft["source_spans"], "proposed": draft["proposed"],
                                     "final": final, "action": action, "skill": skill})
    store.db.commit()


def labels(store) -> list:
    return [d for _, d in store.entities("label")]


# ───────────────────────── ② 发言复述 ─────────────────────────
SYSTEM_RESTATE = """把用户这段话复述成至多 3 句“我理解你说的是……”。只输出 JSON：
{"sentences":[{"text": 复述句, "source_quote": 原话里的一段（逐字）, "category": "seen"|"hearsay"|"opinion"|"expectation"}]}
seen=用户亲眼所见；hearsay=别人转述；opinion=用户的看法；expectation=用户对某人的预期。
不得出现原话里没有的人名、数字、时间；不做评价，不推断性格或情绪。"""
CATEGORY = {"seen": "你亲眼所见", "hearsay": "别人转述", "opinion": "你的看法", "expectation": "你对他的预期"}


def validate_restate(d) -> list:
    s = d.get("sentences") if isinstance(d, dict) else None
    if not isinstance(s, list) or not 1 <= len(s) <= 3:
        return ["sentences 必须是 1–3 项"]
    return [f"sentences[{i}] 需要 text、source_quote 与合法 category" for i, x in enumerate(s)
            if not (isinstance(x, dict) and _s(x.get("text")) and _s(x.get("source_quote")) and x.get("category") in CATEGORY)]


def _names(w: Optional[World]) -> set:
    if w is None:
        return set()
    out = set()
    for p in w.persons.values():
        if not p.is_self:
            out |= {n for n in (p.display_name, p.code) if n and len(n) >= 1}
    return out


def restate(model, text: str, *, world: Optional[World] = None, lexicon: Optional[list] = None) -> Optional[list]:
    """返回 [{text, quote, span, category, label}]；模型不可用或全部被筛掉时返回 None（不编造）。"""
    if model is None:
        return None
    lex = "；".join(f"{e['term']}={e['means']}" for e in (lexicon or []))
    try:
        d = model.structured(SYSTEM_RESTATE, (f"说法词典：{lex}\n" if lex else "") + f"用户原话：{text}", validate_restate)
    except Exception:
        return None
    nums, names, out = set(NUM.findall(text)), _names(world), []
    for x in d["sentences"]:
        span = find_span(text, x["source_quote"])
        if span is None:
            continue  # 原话定位不到：丢
        t = x["text"]
        if any(n not in nums for n in NUM.findall(t)) or any(n in t and n not in text for n in names) or not _clean(t):
            continue  # 原话里没有的数字或人名、越界措辞：丢
        out.append({"text": t, "quote": text[span[0]:span[1]], "span": span, "category": x["category"],
                    "label": CATEGORY[x["category"]]})
    return out or None


def record_restate_label(store, message_id: int, sentence: dict, verdict: str) -> None:
    """verdict：right | wrong。复述本身不是观察（R3），只作为理解是否准确的标签。"""
    assert verdict in ("right", "wrong")
    record_label(store, {"id": f"R{message_id}", "message_id": message_id, "source_spans": [], "proposed": sentence},
                 verdict, None, RESTATE.id)


# ───────────────────────── ① 需求澄清 + 说法词典 ─────────────────────────
INTENTS = ("record", "ask", "advice", "correct", "review")
SYSTEM_CLARIFY = """判断用户这句话想做什么，只输出 JSON：
{"intent": "record"|"ask"|"advice"|"correct"|"review", "slots": {"person": 原话里的人或 null, "matter": 原话里的事或 null, "when": 原话里的时间或 null},
 "question": 至多一个追问（缺关键事实时）或 null, "lexicon_drafts": [{"term": 用户的说法, "means": 指什么, "source_quote": 原话里的一段}]}
用户没说的槽位一律 null，不猜。"""


def validate_clarify(d) -> list:
    if not isinstance(d, dict) or d.get("intent") not in INTENTS or not isinstance(d.get("slots"), dict):
        return ["需要 intent（五选一）与 slots"]
    if d.get("question") is not None and not _s(d["question"]):
        return ["question 必须是字符串或 null"]
    ld = d.get("lexicon_drafts", [])
    if not isinstance(ld, list) or not all(isinstance(x, dict) and _s(x.get("term")) and _s(x.get("means")) and _s(x.get("source_quote")) for x in ld):
        return ["lexicon_drafts 每项需要 term、means、source_quote"]
    return []


def lexicon(store, before_message: Optional[int] = None) -> list:
    """你确认过的说法词典；before_message 给定时只取当时已有的条目（回测时避免用未来信息）。"""
    es = [d for _, d in store.entities("lexicon")]
    return [e for e in es if before_message is None or e.get("since_message", 0) < before_message]


def clarify(model, text: str, *, store=None, message_id: int = 0) -> Optional[dict]:
    if model is None:
        return None
    lex = lexicon(store) if store is not None else []
    try:
        d = model.structured(SYSTEM_CLARIFY, (f"说法词典：{'；'.join(e['term'] + '=' + e['means'] for e in lex)}\n" if lex else "")
                             + f"用户原话：{text}", validate_clarify)
    except Exception:
        return None
    allowed = {e["means"] for e in lex} | {e["term"] for e in lex}
    # 槽位只能是原话里的字，或词典里你确认过的说法；否则置 null（臆补不进来）
    slots = {k: (v if isinstance(v, str) and v and (v in text or v in allowed) else None) for k, v in d["slots"].items()}
    drafts = [{"term": x["term"], "means": x["means"], "quote": x["source_quote"], "message_id": message_id}
              for x in d.get("lexicon_drafts", []) if find_span(text, x["source_quote"]) and x["term"] in text]
    return {"intent": d["intent"], "slots": slots, "question": d.get("question"), "lexicon_drafts": drafts,
            "skill": CLARIFY.id}


def confirm_lexicon(store, draft: dict) -> str:
    """词典只经你确认写入，不自动扩充。"""
    eid = f"X{len(store.entities('lexicon')) + 1}"
    store.save_entity("lexicon", eid, {"id": eid, "term": draft["term"], "means": draft["means"],
                                       "since_message": draft.get("message_id", 0)})
    store.db.commit()
    return eid


# ───────────────────────── ③ 理解回测 ─────────────────────────
FIELDS = ("kind", "reporter_name", "subject_name", "is_interpretation", "occurred", "behavior_key")


def metrics(lbls: list) -> dict:
    """全部由程序算。字段准确率按字段分别算；臆补率 = 模型填了非空、被你改回空的字段 ÷ 全部字段。"""
    obs = [x for x in lbls if x["skill"] == EXTRACT_ID and (x["proposed"].get("target_table", "observation") == "observation")]
    kept = [x for x in obs if x["action"] in ("confirmed", "edited") and x["final"] is not None]
    per = {}
    filled_wrong = total = 0
    for f in FIELDS:
        pairs = [(x["proposed"].get(f), x["final"].get(f)) for x in kept]
        per[f] = {"right": sum(1 for a, b in pairs if a == b), "n": len(pairs)}
        filled_wrong += sum(1 for a, b in pairs if a not in (None, "") and b in (None, ""))
        total += len(pairs)
    rs = [x for x in lbls if x["skill"] == RESTATE.id]
    right = sum(v["right"] for v in per.values())
    return {"labels": len(lbls), "drafts": len(obs), "rejected": sum(1 for x in obs if x["action"] == "rejected"),
            "edited": sum(1 for x in obs if x["action"] == "edited"),
            "field_accuracy": (right / total) if total else None, "fields": per,
            "hallucination_rate": (filled_wrong / total) if total else None,
            "restate_pass": (sum(1 for x in rs if x["action"] == "right") / len(rs)) if rs else None, "restate_n": len(rs)}


def errors(lbls: list) -> list:
    """错例清单（程序归类）：可点回原话（message_id + spans）。"""
    out = []
    for x in lbls:
        if x["skill"] != EXTRACT_ID:
            continue
        if x["action"] == "rejected":
            out.append({"label": x["id"], "message_id": x["message_id"], "spans": x["spans"], "why": "整条不该记"})
            continue
        if x["final"] is None:
            continue
        p, f = x["proposed"], x["final"]
        if p.get("kind") != f.get("kind"):
            why = "把转述当亲见" if f.get("kind") == "hearsay" else "记录类型不对"
        elif p.get("subject_name") != f.get("subject_name") or p.get("reporter_name") != f.get("reporter_name"):
            why = "人指错了"
        elif p.get("is_interpretation") != f.get("is_interpretation"):
            why = "把看法当事实（或反过来）"
        elif p.get("occurred") != f.get("occurred") or p.get("behavior_key") != f.get("behavior_key"):
            why = "行为或是否发生判错"
        else:
            continue
        out.append({"label": x["id"], "message_id": x["message_id"], "spans": x["spans"], "why": why})
    return out


def split(lbls: list, holdout: float = 0.2) -> tuple:
    """按原话（message_id）整组切分：同一条原话的标签只落在一边，验收集检验的是没见过的发言。
    按时间排序，最近约 holdout 比例的原话只用来验收，不参与改进。"""
    mids = sorted({x["message_id"] for x in lbls})
    k = int(round(len(mids) * (1 - holdout)))
    test_m = set(mids[k:])
    s = sorted(lbls, key=lambda x: (x["message_id"], x["id"]))
    return [x for x in s if x["message_id"] not in test_m], [x for x in s if x["message_id"] in test_m]


def replay(store, model, lbls: list, world: Optional[World] = None) -> list:
    """用待测模型/版本重放原话，得到新一版的“模型原版”，再与你当时的最终版对比。
    每条只给当时已有的词典（按 message_id 切分），避免用未来信息作弊。重放默认只该用本地模型。"""
    out, cache = [], {}
    for x in lbls:
        if x["skill"] != EXTRACT_ID or x["action"] == "rejected" or x["final"] is None:
            continue
        mid = x["message_id"]
        if mid not in cache:
            text = store.get_message(mid)["text"]
            lex = lexicon(store, before_message=mid)
            prompt = (f"说法词典：{'；'.join(e['term'] + '=' + e['means'] for e in lex)}\n" if lex else "") + f"用户原话：{text}"
            try:
                cache[mid] = (text, model.structured(SYSTEM_EXTRACT, prompt, validate_extraction)["drafts"])
            except Exception:
                cache[mid] = (text, [])
        text, drafts = cache[mid]
        sp = x["spans"][0] if x["spans"] else None
        hit = None
        for dft in drafts:
            s = find_span(text, dft.get("source_quote", ""))
            if s and sp and s[0] < sp["end"] and sp["start"] < s[1]:
                hit = dft
                break
        prop = {"kind": hit.get("kind"), "reporter_name": hit.get("reporter"), "subject_name": hit.get("subject"),
                "is_interpretation": hit.get("is_interpretation"), "occurred": hit.get("occurred"),
                "behavior_key": hit.get("behavior_key")} if hit else {f: None for f in FIELDS}
        out.append({**x, "proposed": {**x["proposed"], **prop}})
    return out


def enable_advice(old: dict, new: dict) -> dict:
    """启用新版本的门槛：臆补率不升（硬门槛）且字段准确率升。只给建议，启用由你决定。"""
    ok_h = (new["hallucination_rate"] or 0) <= (old["hallucination_rate"] or 0)
    ok_a = (new["field_accuracy"] or 0) > (old["field_accuracy"] or 0)
    return {"recommend": ok_h and ok_a, "hallucination_ok": ok_h, "accuracy_up": ok_a}


def backtest(store, model=None, *, holdout: float = 0.2) -> dict:
    """当前版本的指标（来自你的确认），以及（给了模型时）重放后在验收集上的指标与启用建议。"""
    lbls = labels(store)
    train, test = split(lbls, holdout)
    rep = {"skill": BACKTEST.id, "current": metrics(lbls), "holdout_n": len(test), "errors": errors(lbls)}
    if model is not None and test:
        new = metrics(replay(store, model, test))
        old = metrics(test)
        rep.update({"replay_holdout": new, "baseline_holdout": old, "advice": enable_advice(old, new)})
    return rep


# ───────────────────────── ④ 档案补全（程序算） ─────────────────────────
def profile_gaps(w: World, person_id: str) -> list:
    """三层逐层找空缺（R8：三层互不推导，不由职务推意愿）；can 与 will 分开；零代价的观察优先。"""
    gaps = []
    if not any(p.person_id == person_id and p.valid_to is None for p in w.positions.values()):
        gaps.append({"layer": "职务", "text": "现任职务未知", "separates": "他的正式权限", "cost": 0, "kind": "查组织资料"})
    edges = [e for e in w.edges.values() if person_id in (e.from_person_id, e.to_person_id) and SELF in (e.from_person_id, e.to_person_id)
             and e.status != "refuted"]
    if not edges:
        gaps.append({"layer": "关系", "text": "他和我的关系还没记录", "separates": "汇报线 / 协作 / 无交集", "cost": 0, "kind": "你直接补"})
    for e in edges:
        if e.status == "proposed":
            gaps.append({"layer": "关系", "text": f"关系“{e.type}”待确认", "separates": "这条关系是否成立", "cost": 0, "kind": "你确认"})
    for r in w.roles.values():
        if r.person_id != person_id or r.status == "refuted":
            continue
        m = w.matters[r.matter_id].title if r.matter_id in w.matters else r.matter_id
        dims = {x.dimension for x in w.roles.values() if x.person_id == person_id and x.matter_id == r.matter_id and x.role == r.role and x.status != "refuted"}
        for d, label in (("can", "有没有能力"), ("will", "愿不愿意")):
            if d not in dims and not any(g["text"] == f"{m}：{r.role} · {label}未知" for g in gaps):
                gaps.append({"layer": "议题角色", "text": f"{m}：{r.role} · {label}未知", "separates": "can 与 will 分开", "cost": None, "kind": "观察或询问"})
        if r.status == "proposed":
            gaps.append({"layer": "议题角色", "text": f"{m}：{r.role}（{r.dimension or '未定'}）待确认", "separates": "这个角色是否成立", "cost": 0, "kind": "你确认"})
    for s in discriminating_signals(w, person_id):
        if s.get("status") == "ranked":
            gaps.append({"layer": "判断", "text": s["text"], "separates": "、".join(s["separates"]), "cost": s["cost"], "kind": s["kind"]})
    for f in w.followups:
        if f.status == "open" and person_id in f.target_ref.split(":"):
            gaps.append({"layer": "待办", "text": f.text, "separates": "能否计入统计", "cost": 0, "kind": "补充条件"})
    order = {0: 0, None: 2}
    return sorted(gaps, key=lambda g: order.get(g["cost"], 1))  # 零代价优先；代价待你评的排最后


# ───────────────────────── ⑥ 规律提炼（程序算） ─────────────────────────
def pattern_cards(w: World, person_id: str) -> list:
    """只说“在 X 条件下发生 a/b 次”，不跨人泛化；回忆材料与更正重算不计（沿用 build_packet 的口径）。"""
    pk = build_packet(w, person_id)
    cards = []
    for key, label in (("dim_a", "对我 vs 对同级"), ("dim_b", "我提交 vs 别人提交"), ("dim_c", "近期 vs 过去")):
        d = pk[key]
        cards.append({"kind": "条件发生率", "condition": label, "text": d.get("text", ""),
                      "enough": d.get("state") != "待区分", "state": "候选" if d.get("state") == "待区分" else "检验中"})
    for p in pk["pairs"]:
        state = "有支持" if p["n"] >= MIN_KNOWN and p["multiple"] > 1 else "检验中" if p["n"] else "候选"
        cards.append({"kind": "规则对", "condition": f"“{p['hi_name']}”对“{p['lo_name']}”",
                      "text": f"累计 {p['multiple']:.3g} 倍，来自 {p['n']} 次有区分力的互动",
                      "enough": p["n"] >= MIN_KNOWN, "state": state})
    for c in cards:
        assert _clean(c["text"])  # 不写“证实”之类
    return cards


# ───────────────────────── ⑤ 建议与理由 ─────────────────────────
SYSTEM_ADVICE = """根据证据包，给至多 3 条建议。只输出 JSON：
{"items":[{"do": 做什么, "reason": 理由, "cites": [支持理由的观察 id], "depends_on": 依赖的尚未排除的解释 id,
           "fails_if": 什么情况下这条失效, "prediction": {"text": 采纳后可锁定的预测, "settlement_criterion": 怎么判定} 或 null}]}
理由只能引用证据包里列出的观察；叙述里的数字必须来自证据包；不下“他就是针对你”一类结论，不推断性格或情绪；样本不足就说分不清。"""


def validate_advice(d) -> list:
    it = d.get("items") if isinstance(d, dict) else None
    if not isinstance(it, list) or not 1 <= len(it) <= 3:
        return ["items 必须是 1–3 项"]
    errs = []
    for i, x in enumerate(it):
        if not (isinstance(x, dict) and all(_s(x.get(k)) for k in ("do", "reason", "depends_on", "fails_if")) and isinstance(x.get("cites"), list)):
            errs.append(f"items[{i}] 需要 do、reason、cites、depends_on、fails_if")
        elif x.get("prediction") is not None and not (isinstance(x["prediction"], dict) and _s(x["prediction"].get("text")) and _s(x["prediction"].get("settlement_criterion"))):
            errs.append(f"items[{i}].prediction 需要 text 与 settlement_criterion")
    return errs


def advice_packet(w: World, person_id: str, store=None) -> dict:
    pk = build_packet(w, person_id)
    obs = [o for o in w.observations.values() if o.subject_person_id == person_id and not o.prompted_by_model]
    pk["observations"] = [{"id": o.id, "content": o.content, "kind": o.kind, "mode": o.source_mode} for o in obs]
    pk["hypotheses"] = [{"id": h.id, "statement": h.statement, "by": h.proposed_by} for h in w.hypotheses.values()
                        if h.person_id == person_id and h.status != "retired"]
    pk["gaps"] = profile_gaps(w, person_id)
    pk["patterns"] = pattern_cards(w, person_id)
    return pk


def advise(model, w: World, person_id: str, store=None) -> Optional[dict]:
    """返回 {items, dropped, skill}。每条都过程序校验；不合格的条目丢弃；全部不合格返回 None（不编造）。"""
    if model is None:
        return None
    pk = advice_packet(w, person_id, store)
    import json
    try:
        d = model.structured(SYSTEM_ADVICE, json.dumps(pk, ensure_ascii=False, default=str), validate_advice)
    except Exception:
        return None
    oids = {o["id"]: o for o in pk["observations"]}
    hids = {h["id"] for h in pk["hypotheses"]}
    keep, dropped = [], 0
    for x in d["items"]:
        pred = x.get("prediction") or {}
        texts = " ".join([x["do"], x["reason"], x["fails_if"], pred.get("text", ""), pred.get("settlement_criterion", "")])
        ok = (x["cites"] and all(c in oids for c in x["cites"])  # 只能引用你确认过的观察，不能引用模型自己写的东西
              and x["depends_on"] in hids and numbers_ok(texts, pk) and _clean(texts)
              and not any(o in texts for o in OVERCLAIMS) and "针对你" not in texts)
        if not ok:
            dropped += 1
            continue
        keep.append({**x, "quotes": [oids[c]["content"] for c in x["cites"]]})  # 原话由程序附上
    return {"items": keep, "dropped": dropped, "skill": ADVICE.id} if keep else None




# ───────────────────────── 解读：记下一件事之后，给出几种都说得通的解释（⑦ 事件解读）─────────────────────────
READ_EVENT = SkillVersion(
    "事件解读", 1, "你记下一件事后，给出 2–3 种都说得通的解释，每种附上原话里的依据和以后能分清它的现象。",
    applicable_questions=("怎么看这件事", "怎么理解"), inputs=("你刚确认记下的原话", "相关人物"),
    steps=("读原话", "提出互不相同的解释，每个挂原话里的依据", "给出能分清它们的、不必问对方的观察"),
    output_format="解释 + 原话依据 + 分清它的现象；解释本身不是观察，不计入任何比较", allowed_actions=("假设草稿（模型提出）", "watch 待办（你确认）"))
BUILTIN[READ_EVENT.skill] = READ_EVENT

SYSTEM_READ = """用户刚记下一件事。给出 2–3 种都说得通、而且互相不同的解释，只输出 JSON：
{"explanations":[{"statement": 一句话的解释（不推断性格或情绪，不下“他就是……”的结论，不出现原话里没有的人名和数字）,
                  "evidence": 原话里支持它的一段（逐字摘抄）,
                  "signal": 以后能观察到、能分清它与其他解释的一个具体现象（不必问对方）}],
 "missing_fact": 可选。如果缺一个事实就很难分清这些解释，问一个短问题：{"question": 一句话, "options": 2–3 个简短选项（不含原话里没有的数字）}；不缺就省略}"""


def validate_read(d) -> list:
    e = d.get("explanations") if isinstance(d, dict) else None
    if not isinstance(e, list) or not 2 <= len(e) <= 3:
        return ["explanations 必须是 2–3 项"]
    return [f"explanations[{i}] 需要 statement、evidence、signal" for i, x in enumerate(e)
            if not (isinstance(x, dict) and _s(x.get("statement")) and _s(x.get("evidence")) and _s(x.get("signal")))]


def read_event(model, text: str, *, person_name: str = "", diag: Optional[list] = None, meta: Optional[dict] = None) -> Optional[list]:
    """返回 [{statement, evidence, signal}]；模型不可用、输出不合格、或可用的少于 2 条时返回 None（不编造）。
    diag：给了就把“为什么没有解读”的原因追加进去，页面上会告诉你，而不是悄悄跳过。
    校验：不得出现原话里没有的数字；不得用禁用词、越界说法、性格标签；解释不能重复。
    依据：模型摘的那段若能在原话里逐字找到就用它；找不到（模型改写了）就用整句原话——依据由程序取自你的原话，不采信模型的转述。"""
    why = diag if diag is not None else []
    if model is None:
        why.append("没有可用的模型")
        return None
    try:
        d = model.structured(SYSTEM_READ, f"当事人：{person_name}\n用户原话：{text}", validate_read)
    except Exception as e:
        why.append(f"模型没给出合格的解读（{type(e).__name__}）")
        return None
    nums, out, seen, dropped = set(NUM.findall(text)), [], set(), 0
    for x in d["explanations"]:
        s, ev, sg = x["statement"].strip(), x["evidence"].strip(), x["signal"].strip()
        blob = s + sg
        if any(n not in nums for n in NUM.findall(blob)) or not _clean(blob) or any(o in blob for o in OVERCLAIMS):
            dropped += 1
            continue
        if any(w in blob for w in ("性格", "天生", "就是针对", "一贯如此")) or s in seen:
            dropped += 1
            continue
        seen.add(s)
        span = find_span(text, ev)
        out.append({"statement": s, "evidence": text[span[0]:span[1]] if span else text, "signal": sg})
    mf = d.get("missing_fact") if meta is not None else None
    if isinstance(mf, dict) and isinstance(mf.get("question"), str) and isinstance(mf.get("options"), list):
        q, ops = mf["question"].strip(), [o.strip() for o in mf["options"] if isinstance(o, str) and o.strip()]
        ok = 0 < len(q) <= 60 and 2 <= len(ops) <= 3 and all(len(o) <= 30 for o in ops) and _clean(q + "".join(ops)) \
            and not any(n not in nums for n in NUM.findall(q + "".join(ops)))
        if ok:
            meta["missing_fact"] = {"question": q, "options": ops}
    if len(out) < 2:
        why.append(f"模型给的解释里可用的不足 2 条（{dropped} 条含原话里没有的数字、性格标签或越界说法）")
        return None
    return out
