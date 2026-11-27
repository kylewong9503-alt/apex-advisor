"""验收 A 的数据面：机会统计、三维对照、观察排序、回忆判定、徽标、解读失效。"""
import pytest

from people_reading import scenarios as S
from people_reading.stats import dim_a, dim_b, dim_c, known_opps, peers_of_user, rank_observation_scenes, rate
from people_reading.world import SELF, WorldError, classify_source_mode


def cross_rate(w):
    return rate(w, [s for s in known_opps(w, "X") if s.cross_dept])["text"]


def test_initial_16_known_opportunities():
    w = S.build_world_a(through="E1")
    assert len([s for s in w.stat_opps.values() if w.occurred(s.id) is not None]) == 16
    assert sum(1 for s in w.stat_opps.values() if w.occurred(s.id) is None) == 0


def test_e1_observations_dont_change_counts():
    w = S.build_world_a(through="E1")
    obs = [o for o in w.observations.values() if o.interaction_id == "E1"]
    assert len(obs) == 3 and len(w.stat_opps) == 16
    o2 = next(o for o in obs if o.reporter_id == "甲")
    assert o2.kind == "hearsay" and o2.is_interpretation and o2.reporter_id != SELF
    assert w.evidence_badge("X")["interactions"] == 1  # 三条观察只算一次互动
    assert cross_rate(w) == "5/6"  # 跨部门：我 4/4 + 丁 1/1 + 下属 0/1


def test_known_counts_after_events():
    w2 = S.build_world_a(through="E2")
    assert rate(w2, [s for s in known_opps(w2, "X") if s.cross_dept])["text"] == "5/7"
    w3 = S.build_world_a(through="E3")
    assert rate(w3, [s for s in known_opps(w3, "X") if not s.cross_dept])["text"] == "3/10"  # E3 已写负责人，不进条件统计
    w4 = S.build_world_a()
    assert rate(w4, [s for s in known_opps(w4, "X") if not s.cross_dept])["text"] == "3/11"


def test_a4_dimensions_before_e4():
    w = S.build_world_a(through="E3")
    a = dim_a(w, "X")
    assert (a["me"]["text"], a["peers"]["text"], a["state"]) == ("5/6", "1/2", "待区分")
    assert a["text"] == "待区分：同级一侧仅 2 次已知机会"


def test_a4_dimensions_after_e4():
    w = S.build_world_a()
    a = dim_a(w, "X")
    assert (a["me"]["text"], a["peers"]["text"], a["state"]) == ("5/6", "1/3", "开放比较")
    assert a["unmatched"]["跨部门占比"] == {"我": "4/6", "同级": "1/3"}
    assert "不自动归因" in a["text"]
    assert "S" not in peers_of_user(w)  # 级别高于我，不计入同级
    b = dim_b(w, "X")
    assert b["text"] == "X 对我 5/6；其他人对我 0 次已知机会；待区分"
    c = dim_c(w, "X", recent_from_day=18)
    assert c["state"] == "待区分" and "近期一侧仅 2 次已知机会" in c["text"]


def test_a5_6_dimension_after_correction():
    w = S.build_world_a()
    S.correct(w, "E4", "submitter", "user", source_spans=("E4 其实是我提交的",))
    a = dim_a(w, "X")
    assert (a["me"]["text"], a["peers"]["text"], a["state"]) == ("5/7", "1/2", "待区分")


def test_a5_6_interpretation_marked_changed():
    w = S.build_world_a()
    iid = w.save_interpretation("他是不是只对我这样？", "三维对照缺同级一侧", cited_opps=("E4",),
                                cited_pairs=(("E4", "H_A", "H_C"),), open_questions=("同级一侧样本不足",))
    other = w.save_interpretation("E2 的情况", "…", cited_opps=("E2",), cited_pairs=(("E2", "H_A", "H_C"),))
    assert not w.interpretation_changed(iid)
    S.correct(w, "E4", "submitter", "user")
    assert w.interpretation_changed(iid) and not w.interpretation_changed(other)


def test_a5_7_recalled_badge():
    w = S.build_world_a()
    for o in w.observations.values():
        if o.interaction_id == "E4":
            o.source_mode = "recalled"
    badge = w.evidence_badge("X")
    assert badge["recalled"] == 1 and "其中 1 次为事后回忆" in badge["text"]


def test_a5_9_observation_ranking():
    scenes = [
        {"name": "为测试刻意不写负责人", "facts": {"cross_dept": False, "submitter": "user"}, "cost": 1},
        {"name": "我本来就要提交的部门内未写负责人提案", "facts": {"cross_dept": False, "submitter": "user"}, "cost": 0},
        {"name": "他人提交的跨部门提案", "facts": {"cross_dept": True, "submitter": "丁"}, "cost": 0},
        {"name": "我提交的跨部门提案", "facts": {"cross_dept": True, "submitter": "user"}, "cost": 0},
    ]
    out = rank_observation_scenes(scenes, S.ABC)
    names = [s["name"] for s in out]
    assert set(names[:2]) == {"他人提交的跨部门提案", "我本来就要提交的部门内未写负责人提案"}
    assert names[2] == "为测试刻意不写负责人" and len(names) == 3  # 区分力 0 的场景被筛掉
    assert [s["power"] for s in out[:2]] == [0.6, 0.6] and out[0]["cost"] == out[1]["cost"] == 0
    assert out[0]["separates"]  # 每项写明能分清哪两种解释


@pytest.mark.parametrize("hours,kw,expect", [
    (5, {}, ("contemporaneous", False)),
    (30, {"prompt_kind": "settlement_reminder"}, ("contemporaneous", True)),  # C5
    (72, {}, ("recalled", False)),
    (2000, {"prompt_kind": "judgment_support_question"}, ("recalled", True)),  # C3
    (1, {"prompt_kind": "judgment_support_question"}, ("recalled", True)),  # 无论距事件多久
    (60, {"first_report_mode": "contemporaneous"}, ("recalled", False)),  # 补充报告超过 48 小时
    (10, {"first_report_mode": "contemporaneous", "prompted_by_model": True}, ("contemporaneous", True)),
])
def test_source_mode_rules(hours, kw, expect):
    assert classify_source_mode(hours, **kw) == expect


def test_r3_observation_needs_original_words_and_never_from_ai():
    w = S.build_world_a(through="E1")
    with pytest.raises(WorldError):
        w.add_observation("E1", (), "behavior", SELF, "X", "无原话")
    with pytest.raises(WorldError):
        w.add_observation("E1", ("原话",), "behavior", SELF, "X", "模型生成", from_model=True)


def test_null_dependency_goes_to_followup_not_stats():
    w = S.build_world_a(through="E1")
    w.add_opportunity("N1", actor="X", target=SELF, cross_dept=True, responsible_named=None, occurred=True, day=3,
                      interaction_id="N1")
    assert w.followups[-1].kind == "fill_unknown" and w.followups[-1].text == "补充材料是否写了负责人"
    assert "N1" not in [s.id for s in known_opps(w, "X")]
    assert rate(w, [s for s in known_opps(w, "X") if s.target_id == SELF])["text"] == "5/6"
    with pytest.raises(WorldError):  # 唯一约束
        w.add_opportunity("N2", actor="X", target=SELF, cross_dept=True, responsible_named=None, interaction_id="N1")
