"""第 3 步：议题/角色/关系边录入、竞争解释 Skill、人物个人页、interpretation 持久化、第 9 节 ④–⑤。"""
import pytest

from people_reading import scenarios as S
from people_reading.assistant import Assistant
from people_reading.intake import Intake
from people_reading.llm import ScriptedModel
from people_reading.profile import build_profile, render_html
from people_reading.schema import validate_extraction
from people_reading.skills import COMPETING, SkillRegistry, numbers_ok, build_packet, run_competing_explanation
from people_reading.store import Store
from people_reading.world import SELF, Person

from .test_step2 import D_BEH, D_HEAR, OUT1, OUT2, PASS, TEXT1, extraction

Q = "用竞争解释分析一下，他是不是只对我这样？"


def make(tmp_path, outputs=(), through="E3", name="s3.db"):
    w = S.build_world_a(through=through)
    st = Store(str(tmp_path / name), PASS)
    st.snapshot_world(w)
    it = Intake(st, w, ScriptedModel(list(outputs)))
    it.adopt_world()
    st.new_conversation("c1", "测试")
    return w, st, it, Assistant(it, SkillRegistry(st))


# ───────── 议题 / 角色 / 关系边 录入 ─────────
def rel(**kw):
    base = {"scope": "matter", "matter": "争取集团专家支持", "from": "F", "to": None, "type": None, "role": "referrer",
            "dimension": None, "source_quote": "我需要 F 帮忙引荐"}
    base.update(kw)
    return base


def test_schema_relations():
    assert validate_extraction(extraction(relations=[rel()])) == []
    assert validate_extraction(extraction(relations=[rel(matter=None)]))  # 议题内关系必须写议题
    assert validate_extraction(extraction(relations=[rel(scope="global", role="referrer")]))  # 全局关系不带 role
    assert validate_extraction(extraction(relations=[rel(scope="global", role=None, to="我", type="can_refer")]))
    assert validate_extraction(extraction(relations=[rel(role="boss")]))
    assert validate_extraction(extraction(relations=[rel(dimension="maybe")]))
    assert validate_extraction(extraction(matter={"title": ""}))


def test_global_dependency_vs_matter_role(tmp_path):
    t1, t2 = "我需要 F 支持我争取集团资源", "这次专家支持我需要 F 帮忙引荐"
    out = [extraction(relations=[{"scope": "global", "from": "我", "to": "F", "type": "depends_on", "role": None,
                                  "dimension": None, "matter": None, "source_quote": "我需要 F 支持我争取集团资源"}]),
           extraction(relations=[rel()])]
    w, st, it, _ = make(tmp_path, out)
    it.register_person(Person("F", "F"))
    r1 = it.handle("c1", t1)
    d = st.drafts()[0]["proposed"]
    assert d["target_table"] == "edge" and d["proposed_by"] == "user" and not w.edges
    e = w.edges[it.confirm(r1.draft_ids[0])]
    assert (e.from_person_id, e.to_person_id, e.type, e.kind, e.status) == (SELF, "F", "depends_on", "interactional", "confirmed")
    assert e.attributes["source_tier"] == "self_reported"  # 自述，不是被核实的事实
    n_edges = len(w.edges)
    r2 = it.handle("c1", t2)
    assert st.drafts()[-1]["proposed"]["target_table"] == "matter_role"
    role = w.roles[it.confirm(r2.draft_ids[0])]
    assert (role.person_id, role.role, role.dimension) == ("F", "referrer", None) and len(w.edges) == n_edges  # R9
    assert w.matters[role.matter_id].title == "争取集团专家支持"
    assert it.quote  # 草稿的原话可回溯
    assert st.quote(st.drafts()[-1]["source_spans"][0]) == "我需要 F 帮忙引荐"


def test_will_role_stays_proposed_can_is_confirmed(tmp_path):
    out = [extraction(relations=[rel(dimension="will", source_quote="F 愿意帮我"), rel(dimension="can", source_quote="F 有渠道")])]
    w, st, it, _ = make(tmp_path, out)
    it.register_person(Person("F", "F"))
    r = it.handle("c1", "F 愿意帮我，F 有渠道")
    roles = [w.roles[it.confirm(d)] for d in r.draft_ids]
    assert {x.dimension: x.status for x in roles} == {"will": "proposed", "can": "confirmed"}  # will 需要行为证据


def test_matter_draft_relation_quote_must_exist_and_names_not_guessed(tmp_path):
    out = [extraction(matter={"title": "争取集团专家支持", "purpose": "拿到一次具体引荐"},
                      relations=[rel(source_quote="编造的话"), rel(from_="老张") if False else rel(**{"from": "老张"})])]
    w, st, it, _ = make(tmp_path, out)
    r = it.handle("c1", "明天要和 F 谈，我需要 F 帮忙引荐，老张也说他能帮忙")
    assert r.dropped == 1  # 编造的 source_quote 被丢弃
    tables = [st.drafts()[i]["proposed"]["target_table"] for i in range(len(st.drafts()))]
    assert sorted(tables) == ["matter", "matter_role"]
    for d in r.draft_ids:
        it.confirm(d)
    assert [m.purpose for m in w.matters.values()] == ["拿到一次具体引荐"]
    assert any(p.is_placeholder and p.display_name == "老张" for p in w.persons.values())  # 未识别 → 占位，不猜


# ───────── Skill ─────────
def test_skill_registry_select_and_five_items():
    r = SkillRegistry()
    assert r.select(Q) is COMPETING and r.select("他是不是只对我这样") is COMPETING
    assert r.select("今天天气不错") is None
    r.enabled["竞争解释"] = False
    assert r.select(Q) is None
    sv = COMPETING
    assert sv.id == "竞争解释@v1" and all([sv.applicable_questions, sv.inputs, sv.steps, sv.output_format, sv.allowed_actions])


def test_competing_explanation_output_and_persistence(tmp_path):
    w, st, it, _ = make(tmp_path)
    res = run_competing_explanation(w, "X", "他是不是只对我这样？", conversation_id="c1", store=st)
    t = res["text"]
    assert "【竞争解释@v1】" in t and "同级一侧仅 2 次已知机会" in t and "依据：" in t
    assert "5/6" in t and "1/2" in t and "4 倍" in t and "来自 1 次有区分力的互动" in t
    assert "不自动归因" not in t and "猜中" not in t and "证实" not in t
    iid = res["interpretation_id"]
    d = w.interpretations[iid]
    assert d["skill_version_id"] == "竞争解释@v1" and "E2" in d["cited_opps"] and d["open_questions"]
    assert any(s["kind"] == "观察信号" and s["cost"] == 0 for s in res["signals"])
    # 重新打开：解读仍在，且依据没变
    st.close()
    w2, _ = Store(str(tmp_path / "s3.db"), PASS).load_world()
    assert iid in w2.interpretations and not w2.interpretation_changed(iid)
    assert w2.interpretations[iid]["open_questions"] == d["open_questions"]


def test_narrative_guard_r7():
    w = S.build_world_a(through="E3")
    pk = build_packet(w, "X")
    assert numbers_ok("对我 5/6，对同级 1/2，样本偏少。", pk)
    assert not numbers_ok("对我 97%，所以他就是针对你。", pk)  # 数字不在证据包
    assert not numbers_ok("这证实了他只对我这样。", pk)  # R12 红线


@pytest.mark.parametrize("narr,by", [
    ({"summary": "对我 5/6，对同级 1/2，同级样本偏少，暂时分不清。"}, "model"),
    ({"summary": "对我 99/100，明显针对。"}, "template"),
    ("不是 JSON", "template"),
])
def test_narrative_model_or_template(narr, by):
    w = S.build_world_a(through="E3")
    m = ScriptedModel([narr, narr])
    res = run_competing_explanation(w, "X", "q", model=m)
    assert res["narrated_by"] == by
    assert "99/100" not in res["text"]
    assert "5/6" in res["text"] and "同级一侧仅 2 次" in res["text"] or by == "model"


def test_skill_works_when_model_down(tmp_path):
    class Down:
        def structured(self, *a, **k):
            raise ConnectionRefusedError()
    w = S.build_world_a(through="E3")
    assert run_competing_explanation(w, "X", "q", model=Down())["narrated_by"] == "template"


# ───────── 第 9 节 ④–⑤ ─────────
def test_9_4_and_9_5_end_to_end(tmp_path):
    w, st, it, a = make(tmp_path, [OUT1, OUT2])
    assert a.handle("c1", TEXT1).kind == "record" and a.handle("c1", "是，都是我提交的跨部门提案").kind == "record"
    it.confirm_all()
    opp = "OPP-" + it.last_iid["c1"]
    assert w.stat_opps[opp].target_id == SELF

    # ④ 点名调用 Skill
    r4 = a.handle("c1", Q)
    assert r4.kind == "interpretation" and "竞争解释@v1" in r4.text
    assert "同级一侧仅 2 次已知机会" in r4.text  # 还分不清的地方：三维对照缺同级一侧
    iid = r4.interpretation_id
    assert w.interpretations[iid]["conversation_id"] == "c1" and opp in w.interpretations[iid]["cited_opps"]
    assert [m["role"] for m in st.messages("c1")][-1] == "card"

    # ⑤a 下次问“上次还差什么信息？”
    r5 = a.handle("c1", "上次还差什么信息？")
    assert r5.kind == "recall" and "同级一侧仅 2 次已知机会" in r5.text and "补充材料是否写了负责人" in r5.text
    assert "依据已变化" not in r5.text

    # ⑤b 纠正：先预览，不写入
    r6 = a.handle("c1", "那份提案是丁提交的")
    assert r6.kind == "correction_preview" and iid in r6.text and "确认后才写入" in r6.text
    assert w.stat_opps[opp].target_id == SELF and not w.interpretation_changed(iid)
    # 确认后重算
    r7 = a.confirm_correction("c1", r6.preview_id)
    assert w.stat_opps[opp].target_id == "丁" and w.engine.opps[opp].corrected
    assert w.interpretation_changed(iid) and "依据已变化" in r7.text
    assert [r.kind for r in w.engine.revisions if r.kind == "fact_correction"]
    assert "依据已变化" in a.handle("c1", "上次还差什么信息？").text
    # 重新打开，依据已变化仍然成立；更正与修订都在
    st.close()
    w2, _ = Store(str(tmp_path / "s3.db"), PASS).load_world()
    assert w2.interpretation_changed(iid) and w2.stat_opps[opp].target_id == "丁"
    assert any(r.kind == "fact_correction" for r in w2.engine.revisions)


def test_correction_without_target_or_unknown_person_asks(tmp_path):
    w, st, it, a = make(tmp_path)
    assert a.handle("c1", "那份提案是丁提交的").kind == "ask"  # 没有“那份提案”
    it.last_iid["c1"] = "E2"
    w.stat_opps["OPP-E2"] = w.stat_opps["E2"]
    assert a.handle("c1", "那份提案是路人乙乙提交的").kind == "ask"


def test_skill_asks_who_when_no_focus(tmp_path):
    w, st, it, a = make(tmp_path)
    assert a.handle("c1", "用竞争解释分析一下").kind == "ask"
    assert a.handle("c1", "用竞争解释分析一下 X").kind == "interpretation"  # 点名人物


def test_recall_without_interpretation(tmp_path):
    w, st, it, a = make(tmp_path)
    assert "还没有做过解读" in a.handle("c1", "上次还差什么信息？").text


# ───────── 人物个人页 ─────────
def test_profile_x_sections(tmp_path):
    w, st, it, a = make(tmp_path, [OUT1, OUT2])
    a.handle("c1", TEXT1); a.handle("c1", "是，都是我提交的跨部门提案"); it.confirm_all()
    a.handle("c1", Q)
    pf = build_profile(w, "X", st)
    assert pf["header"]["action"] == "继续聊 X" and pf["positions"][0]["current"]
    quotes = [q for t in pf["timeline"] for o in t["observations"] for q in o["quotes"]]
    assert "X 连问三次谁负责" in quotes and "X 很不满" in quotes  # 点回原话
    hearsay = next(o for t in pf["timeline"] for o in t["observations"] if o["kind"] == "hearsay")
    assert hearsay["reporter"] == "甲" and hearsay["interpretation"] and hearsay["mode"] == "当时记录"
    assert any("事前概率" in c["text"] for c in pf["judgment_cards"]) and any("未达到" in c["text"] for c in pf["judgment_cards"])
    assert "待区分" in pf["dimensions"]["A"]
    assert [f["text"] for f in pf["followups"]] == ["补充材料是否写了负责人"]
    assert pf["interpretations"][0]["skill"] == "竞争解释@v1" and not pf["interpretations"][0]["stale"]
    page = render_html(pf)
    assert "继续聊 X" in page and "原话：X 连问三次谁负责" in page


def test_profile_three_layers_for_cfo():
    w = S.build_world_b()
    pf = build_profile(w, "F")
    assert pf["positions"][0]["title"] == "CFO"
    assert pf["relation"]["org_path"]["label"] == "团队在其线下" and pf["relation"]["structural"] == []
    page = render_html(pf)
    assert "团队在其线下，不是直属上级" in page and "直属汇报" not in page
    m = build_profile(w, "M")
    assert [x["type"] for x in m["relation"]["structural"]] == ["直属汇报"] and m["relation"]["org_path"] is None
    # can / will 分开
    w.add_matter("MT1", "争取集团专家支持")
    w.add_matter_role("MT1", "F", "referrer", dimension="will", statement="表示愿意打听", status="proposed")
    role = build_profile(w, "F")["matter_roles"][0]["roles"]["引荐人"]
    assert role["will"].startswith("表示愿意打听") and role["can"] == "未知"
    assert "can：未知" in render_html(build_profile(w, "F"))


def test_profile_marks_stale_interpretation(tmp_path):
    w, st, it, a = make(tmp_path)
    res = run_competing_explanation(w, "X", "q", store=st)
    S.correct(w, "E2", "submitter", "user")
    pf = build_profile(w, "X", st)
    assert pf["interpretations"][0]["stale"] and "依据已变化" in render_html(pf)
