"""DeepSeek Skills 套件：标签、复述、澄清与词典、回测、档案补全、规律卡、建议。模型输出全部脚本化。"""
import pytest

from people_reading import skillset as K
from people_reading.llm import ScriptedModel
from people_reading.skills import SkillRegistry
from people_reading.store import Store
from people_reading.intake import Intake
from people_reading.world import Person, World
from people_reading.tests.test_step2 import D_BEH, D_HEAR, PASS, extraction

TEXT = "今天会上 X 连问三次谁负责。散会后甲说 X 很不满。"


def mk(tmp_path, outputs):
    st = Store(str(tmp_path / "k.db"), PASS)
    it = Intake(st, World(), ScriptedModel(outputs))
    for pid in ("X", "甲"):
        it.register_person(Person(pid, pid))
    st.new_conversation("c1", "测试")
    return st, it


# ───────── 标签：确认 / 修改 / 不记 都会记，且只记一次 ─────────
def test_labels_recorded_on_confirm_edit_reject(tmp_path):
    st, it = mk(tmp_path, [extraction(drafts=[D_BEH, D_HEAR])])
    r = it.handle("c1", TEXT)
    d1, d2 = r.draft_ids
    it.confirm(d1, {"kind": "behavior", "occurred": None})  # 你把“是否发生”改回空 → 臆补
    it.reject(d2)
    it.reject(d2)  # 重复不记
    ls = K.labels(st)
    assert [x["action"] for x in ls] == ["edited", "rejected"]
    m = K.metrics(ls)
    assert m["drafts"] == 2 and m["rejected"] == 1 and m["edited"] == 1
    assert m["hallucination_rate"] == pytest.approx(1 / len(K.FIELDS))  # occurred 被你改回空
    assert m["fields"]["occurred"] == {"right": 0, "n": 1}
    assert any(e["why"] == "整条不该记" for e in K.errors(ls))


def test_labels_are_encrypted_at_rest(tmp_path):
    st, it = mk(tmp_path, [extraction(drafts=[D_BEH])])
    it.confirm(it.handle("c1", TEXT).draft_ids[0])
    raw = open(st.path, "rb").read()
    assert "连问三次".encode() not in raw  # 标签里有模型原版的内容：同原话一样加密


# ───────── ② 发言复述 ─────────
def test_restate_drops_unlocatable_new_numbers_and_new_names(tmp_path):
    w = World(); w.add_person(Person("Y", "张伟", code="Y"))
    out = {"sentences": [
        {"text": "X 在会上连问了三次谁负责", "source_quote": "X 连问三次谁负责", "category": "seen"},
        {"text": "甲转述 X 不满，已持续 5 天", "source_quote": "甲说 X 很不满", "category": "hearsay"},  # 原话没有“5”
        {"text": "张伟也在场", "source_quote": "今天会上", "category": "seen"}]}  # 原话没有“张伟”
    res = K.restate(ScriptedModel([out]), TEXT, world=w)
    assert [x["category"] for x in res] == ["seen"] and res[0]["quote"] == "X 连问三次谁负责"
    bad = {"sentences": [{"text": "他说过这句", "source_quote": "原话里没有", "category": "seen"}]}
    assert K.restate(ScriptedModel([bad]), TEXT) is None  # 全部被筛掉：不编造
    assert K.restate(None, TEXT) is None


def test_restate_verdict_is_a_label_not_an_observation(tmp_path):
    st, it = mk(tmp_path, [])
    mid = st.add_message("c1", "user", TEXT)
    K.record_restate_label(st, mid, {"text": "x"}, "right")
    K.record_restate_label(st, mid, {"text": "y"}, "wrong")
    assert K.metrics(K.labels(st))["restate_pass"] == 0.5 and not it.world.observations


# ───────── ① 需求澄清 + 说法词典 ─────────
def test_clarify_nulls_unsaid_slots_and_lexicon_only_after_confirm(tmp_path):
    st, _ = mk(tmp_path, [])
    out = {"intent": "record", "slots": {"person": "X", "matter": "预算复核", "when": "今天"}, "question": None,
           "lexicon_drafts": [{"term": "老张", "means": "张伟", "source_quote": "老张"},
                              {"term": "上面", "means": "审批人", "source_quote": "上面"}]}
    r = K.clarify(ScriptedModel([out]), "今天老张在会上 X 连问三次", store=st, message_id=5)
    assert r["slots"] == {"person": "X", "matter": None, "when": "今天"}  # “预算复核”原话里没有 → null
    assert [d["term"] for d in r["lexicon_drafts"]] == ["老张"]  # 原话里没有“上面” → 丢
    assert K.lexicon(st) == []  # 不自动写入
    K.confirm_lexicon(st, r["lexicon_drafts"][0])
    assert [e["term"] for e in K.lexicon(st)] == ["老张"]
    assert K.lexicon(st, before_message=5) == [] and len(K.lexicon(st, before_message=6)) == 1  # 回测按时间切分


# ───────── ③ 理解回测 ─────────
def test_backtest_holdout_replay_and_enable_gate(tmp_path):
    outs = [extraction(drafts=[D_BEH]) for _ in range(5)]
    st, it = mk(tmp_path, outs)
    for i in range(5):
        r = it.handle("c1", f"第{i}次：X 连问三次谁负责")
        it.confirm(r.draft_ids[0], {"occurred": None} if i < 2 else None)
    train, test = K.split(K.labels(st))
    assert len(test) == 1 and test[0]["message_id"] == max(x["message_id"] for x in K.labels(st))  # 最近 20% 只验收
    better = dict(D_BEH, occurred=True)
    rep = K.backtest(st, ScriptedModel([extraction(drafts=[better])]))
    assert rep["holdout_n"] == 1 and "advice" in rep
    assert rep["replay_holdout"]["field_accuracy"] == 1.0
    assert K.enable_advice({"hallucination_rate": 0.1, "field_accuracy": 0.8}, {"hallucination_rate": 0.2, "field_accuracy": 0.95})["recommend"] is False
    assert K.enable_advice({"hallucination_rate": 0.1, "field_accuracy": 0.8}, {"hallucination_rate": 0.1, "field_accuracy": 0.9})["recommend"] is True


# ───────── ④ 档案补全 / ⑥ 规律卡（程序算） ─────────
def test_profile_gaps_and_pattern_cards():
    from people_reading import scenarios as S
    w = S.build_world_b()
    gaps = K.profile_gaps(w, "F")
    assert gaps and all(g["layer"] in ("职务", "关系", "议题角色", "判断", "待办") for g in gaps)
    costs = [g["cost"] for g in gaps]
    assert costs == sorted(costs, key=lambda c: {0: 0, None: 2}.get(c, 1))  # 零代价优先
    wa = S.build_world_a()
    cards = K.pattern_cards(wa, "X")
    assert cards and all(c["state"] in ("候选", "检验中", "有支持") for c in cards)
    assert not any(wd in c["text"] for c in cards for wd in ("证实", "猜中"))


# ───────── ⑤ 建议与理由 ─────────
def test_advice_only_cites_confirmed_observations_and_known_numbers(tmp_path):
    st, it = mk(tmp_path, [extraction(drafts=[D_BEH])])
    oid = it.confirm(it.handle("c1", TEXT).draft_ids[0])
    w = it.world
    h = w.add_hypothesis("X", "只对跨部门提案追问")
    good = {"do": "下次提交前写明负责人", "reason": "他在会上追问负责人", "cites": [oid], "depends_on": h.id,
            "fails_if": "写明负责人后他仍追问", "prediction": {"text": "写明负责人后不再追问", "settlement_criterion": "会上是否追问负责人"}}
    bad_cite = dict(good, cites=["O-不存在"])
    bad_num = dict(good, reason="他已经追问了 17 次")
    bad_word = dict(good, do="这证实他就是针对你")
    res = K.advise(ScriptedModel([{"items": [good, bad_cite, bad_num]}]), w, "X", st)
    assert len(res["items"]) == 1 and res["dropped"] == 2 and res["items"][0]["quotes"] == ["X 连问三次谁负责"]
    assert K.advise(ScriptedModel([{"items": [bad_word]}]), w, "X", st) is None
    assert K.advise(None, w, "X", st) is None


def test_suite_registered_and_routed(tmp_path):
    reg = SkillRegistry()
    for sv in K.SUITE:
        assert reg.get(sv.skill) is sv and sv.id.endswith("@v1")
    assert reg.select("有什么建议") is K.ADVICE and reg.select("还缺什么") is K.PROFILE_GAPS
    from people_reading.assistant import Assistant
    st, it = mk(tmp_path, [extraction(drafts=[D_BEH])])
    it.confirm(it.handle("c1", TEXT).draft_ids[0])
    a = Assistant(it, reg)
    assert "理解回测@v1" in a.handle("c1", "回测一下").text
    assert "档案补全@v1" in a.handle("c1", "X 还缺什么").text
    assert "暂时给不出" in a.handle("c1", "对 X 有什么建议").text  # 脚本模型已用完：不编造
