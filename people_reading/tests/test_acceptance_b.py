"""验收 B：三层关系模型（职务 / 关系 / 议题角色）。"""
import pytest

from people_reading import scenarios as S
from people_reading.world import SELF, Person, WorldError


def test_b1_org_path_is_not_direct_report():
    w = S.build_world_b()
    v = w.relationship_view("F")
    assert v["org_path"]["path"] == ["我", "战略组", "Finance 线（F 负责）"]
    assert v["org_path"]["label"] == "团队在其线下" and not v["org_path"]["is_direct_report"]
    assert v["structural"] == []  # 不存在 我 → F 的 direct_report
    m = w.relationship_view("M")
    assert [e.type for e in m["structural"]] == ["direct_report"] and m["org_path"] is None


def test_b2_global_dependency_draft():
    w = S.build_world_b()
    d = w.propose(("我需要 F 支持我争取集团资源",), scope="global", from_p=SELF, to_p="F", type_="depends_on")
    assert d.target_table == "edge" and d.proposed["type"] == "depends_on" and d.proposed["proposed_by"] == "user"
    assert d.proposed["kind"] == "interactional" and d.proposed["status"] == "proposed"
    with pytest.raises(WorldError):
        w.propose((), scope="global", from_p=SELF, to_p="F", type_="depends_on")  # 必须回指原话


def test_b3_matter_scoped_is_role_not_global_edge():
    w = S.build_world_b()
    w.add_matter("MT1", "争取集团专家支持")
    n_edges = len(w.edges)
    d = w.propose(("这次专家支持我需要 F 帮忙引荐",), scope="matter", matter_id="MT1", person_id="F", role="referrer")
    assert d.target_table == "matter_role" and d.proposed["dimension"] is None
    w.confirm_draft(d.id)
    assert len(w.edges) == n_edges and len(w.roles) == 1
    with pytest.raises(WorldError):  # R9：议题内关系不得存成全局 edge
        w.add_edge(SELF, "F", "interactional", "depends_on", matter_id="MT1")


def test_b4_upgrade_needs_two_matters_and_user_confirmation():
    w = S.build_world_b()
    w.add_matter("MT1", "专家支持"); w.add_matter("MT2", "预算")
    w.add_matter_edge("MT1", "F", "M", "can_refer", status="confirmed")
    assert w.upgrade_suggestions() == []
    w.add_matter_edge("MT2", "F", "M", "can_refer", status="proposed")  # 未 confirmed 不算
    assert w.upgrade_suggestions() == []
    w.add_matter_edge("MT2", "F", "M", "can_refer", status="confirmed")
    sug = w.upgrade_suggestions()
    assert len(sug) == 1 and sug[0]["matters"] == ["MT1", "MT2"]
    assert not [e for e in w.edges.values() if e.kind == "interactional"]  # 不自动升级
    with pytest.raises(WorldError):
        w.confirm_upgrade(sug[0], confirmed_by_user=False)
    e = w.confirm_upgrade(sug[0], confirmed_by_user=True)
    assert e.kind == "interactional" and w.upgrade_suggestions() == []


def test_b5_position_change_not_inherited_and_history_traceable():
    w = S.build_world_b()
    w.add_matter("MT1", "专家支持")
    w.add_edge(SELF, "F", "interactional", "depends_on")
    w.add_matter_role("MT1", "F", "referrer", dimension="will")
    w.engine.define_behavior(S.TOPIC)
    w.engine.add_opportunity("MTG", "first_q_topic", {})
    p = w.engine.lock_prediction(id="P", person_id="F", matter_id="MT1", behavior_key="first_q_topic",
                                 bind_mode="specific_item", opportunity_id="MTG", rules=[S.H1, S.H2],
                                 prob_dist={"职责": .6, "专家需求": .25, "其他": .15}, quadrant_at_lock="近-浅")
    t_lock = p.pre.locked_at
    old = w.end_position("F")
    assert old.valid_to is not None and w.current_position("F") is None
    w.add_person(Person("F2", "新任 CFO"))
    w.add_position("F2", "CFO", "FIN", 9, head=True)
    assert w.unit_head("FIN") == "F2"
    v = w.relationship_view("F2")
    assert v["interactional"] == [] and v["structural"] == []
    assert not [r for r in w.roles.values() if r.person_id == "F2"]
    assert [e for e in w.edges.values() if e.to_person_id == "F"]  # 前任的边仍在前任名下
    assert w.org_state_at(t_lock)["FIN:CFO"] == "F"  # 历史组织状态可追溯
    assert w.engine.preds["P"].pre.quadrant_at_lock == "近-浅"


def test_b6_placeholder_then_merge():
    w = S.build_world_b()
    ph = w.create_placeholder("集团同事（未具名）")
    assert ph.is_placeholder and ph.display_name == "集团同事（未具名）"
    o = w.add_observation("I1", ("F 帮我联系了集团同事",), "behavior", SELF, "F", "F 联系了集团同事", involved=(ph.id,))
    w.add_matter("MT1", "专家支持")
    w.add_matter_edge("MT1", "F", ph.id, "can_refer")
    w.add_person(Person("G", "G"))
    rev = w.merge_person(ph.id, "G")
    assert o.involved == ["G"] and w.matter_edges["ME1"].to_person_id == "G"
    assert w.persons[ph.id].merged_into_id == "G"
    assert rev.kind == "person_merge" and rev.detail["observations"] == 1 and rev.detail["matter_edges"] == 1
    with pytest.raises(WorldError):
        w.merge_person("F", "G")  # 只能合并占位人物


def test_r8_layers_do_not_infer_each_other():
    w = S.build_world_b()
    # F 是 CFO（职务）并不产生任何关系边或议题角色
    assert not [e for e in w.edges.values() if "F" in (e.from_person_id, e.to_person_id)]
    assert not w.roles and w.relationship_view("F")["interactional"] == []
