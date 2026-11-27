"""第 5 步：四象限首页、关系图全局浏览、跨议题校准面板、隐私遮挡与解锁、应用外壳。"""
import json
import shutil
import subprocess

import pytest

from people_reading import crypto, privacy
from people_reading import scenarios as S
from people_reading.graph import (build_matter_graph, build_org_graph, graph_views, render_graph_browser,
                                  render_org_svg)
from people_reading.panels import calibration_panel, render_calibration
from people_reading.privacy import AppLock, Locked, Redactor, TooManyAttempts
from people_reading.profile import build_profile, render_html
from people_reading.quadrants import build_quadrants, render_quadrants
from people_reading.shell import render_app
from people_reading.store import Store
from people_reading.workbench import render_matter_page, workbench_view
from people_reading.world import SELF, Person, WorldError

from . import test_step4 as T
from .test_step2 import PASS

SENSITIVE = ("争取集团专家支持", "表示愿意打听", "可以帮我问问集团那边", "CFO", "Finance", "战略组", "先问职责", "专家需求",
             "团队在其线下", "需其引荐", "两周内", "方明", "马力", "分工说明", "F 可能需要", "他先问", "这次会谈希望")


def cfo(tmp_path):
    w, st, it, mid, iid = T.full_c(tmp_path)
    w.persons["F"].display_name, w.persons["F"].code = "方明", "F"
    w.persons["M"].display_name, w.persons["M"].code = "马力", "M"
    w.set_quadrant("F", "近-浅", influence=4)
    w.set_quadrant("M", "近-深", influence=3)
    st.sync_world(w)
    return w, st, mid


# ───────── 四象限 ─────────
def test_set_quadrant_validation_and_revision():
    w = S.build_world_b()
    for bad in (lambda: w.set_quadrant("F", "中-中"), lambda: w.set_quadrant(SELF, "近-深"),
                lambda: w.set_quadrant("F", "近-深", influence=9)):
        with pytest.raises(WorldError):
            bad()
    ph = w.create_placeholder("集团同事（未具名）")
    with pytest.raises(WorldError):
        w.set_quadrant(ph.id, "近-深")
    n = len(w.revisions)
    w.set_quadrant("F", "近-深", influence=5)
    assert w.persons["F"].quadrant_manual == "近-深" and w.persons["F"].influence == 5 and len(w.revisions) == n + 1
    w.set_quadrant("F", "近-深")  # 没变化不记修订
    assert len(w.revisions) == n + 1


def test_quadrant_cards_content_and_ordering(tmp_path):
    w, st, mid = cfo(tmp_path)
    w.add_person(Person("G", "高峰", code="G", influence=5)); w.add_position("G", "COO", "FIN", 9)
    w.set_quadrant("G", "近-浅"); w.set_quadrant("G", "近-浅", influence=5)
    q = build_quadrants(w)
    f = next(c for c in q["cards"]["近-浅"] if c["id"] == "F")
    assert f["code"] == "F" and f["title"] == "CFO"
    assert f["role"] == "争取集团专家支持：表示愿意打听（渠道未知）"  # can 与 will 分开；渠道未知
    assert f["note"] == "两周内 F 是否给出具体人选"
    assert [c["id"] for c in q["cards"]["近-浅"]] == ["G", "F"]  # 象限内按影响力排序
    assert [c["id"] for c in q["cards"]["近-深"]] == ["M"] and q["cards"]["近-深"][0]["role"] == "暂无议题角色"
    html = render_quadrants(q)
    assert "象限由你手动设置" in html and html.index("近-深") < html.index("近-浅")  # 上行了解深，下行了解浅


def test_quadrant_note_threshold_and_unclassified():
    w = S.build_world_a()
    w.add_matter("M1", "资源讨论"); w.add_matter_role("M1", "X", "approver", status="confirmed")
    w.set_quadrant("X", "远-深", influence=4)
    q = build_quadrants(w)
    x = q["cards"]["远-深"][0]
    assert x["note"] == "对同级的观察刚达到 3 次" and x["role"] == "资源讨论：审批人"
    assert {c["id"] for c in q["cards"]["未分类"]} >= {"丁", "戊", "丙"}
    assert "未分类" in render_quadrants(q)


def test_person_fields_survive_reopen(tmp_path):
    w, st, mid = cfo(tmp_path)
    st.close()
    w2, _ = Store(str(tmp_path / "s4.db"), PASS).load_world()
    f = w2.persons["F"]
    assert (f.code, f.display_name, f.influence, f.quadrant_manual) == ("F", "方明", 4, "近-浅")
    assert build_quadrants(w2)["cards"]["近-浅"][0]["note"] == "两周内 F 是否给出具体人选"


# ───────── 关系图全局浏览 ─────────
def test_org_view_shows_only_position_unitlink_structural(tmp_path):
    w = S.build_world_b()
    w.add_matter("MT1", "争取集团专家支持")
    w.add_matter_role("MT1", "F", "referrer", dimension="will", statement="表示愿意打听")
    w.add_edge(SELF, "F", "interactional", "depends_on")
    w.add_matter_edge("MT1", "F", "M", "can_refer", status="confirmed")
    g = build_org_graph(w)
    names = {u["name"]: [p["label"] for p in u["persons"]] for u in g["units"]}
    assert names["Finance 线"] == ["F · CFO"] and set(names["战略组"]) == {"我 · 战略组成员", "M · 战略组负责人"}
    assert {(l["label"], l["style"]) for l in g["unit_links"]} == {("实线汇报", "solid"), ("支持", "dotted")}
    assert [(s["label"], s["style"]) for s in g["structural"]] == [("直属汇报", "solid")]  # 只有 structural 边
    svg = render_org_svg(g)
    assert "实线汇报" in svg and "支持" in svg
    assert "我 → M：直属汇报（实线）" in render_graph_browser(w)  # 结构关系列在图下方
    assert "表示愿意打听" not in svg and "依赖" not in svg and "可能引荐" not in svg  # 议题角色与互动关系不在此图


def test_graph_browser_switches_matters_and_marks_no_capture(tmp_path):
    w, st, mid = cfo(tmp_path)
    w.add_matter("MT9", "预算讨论")
    views = graph_views(w)
    assert [v["title"] for v in views] == ["组织结构", "争取集团专家支持", "预算讨论"]
    html = render_graph_browser(w, st)
    assert "data-no-capture='graph'" in html and html.count("class='gpanel'") == 3 and html.count("class='tab'") == 3
    assert "方明 · 表示愿意打听 · 渠道未知" in html and "需其引荐" in html and "stroke-dasharray" in html  # 默认 plain：姓名
    assert "F · 表示愿意打听 · 渠道未知" in render_graph_browser(w, st, Redactor(w, "code"))  # 全局默认：代号
    assert "你" not in build_matter_graph(w, "MT9")["nodes"][0]["label"]


# ───────── 跨议题校准面板 ─────────
def two_type_world():
    w = S.build_world_a()
    e = w.engine
    e.define_behavior(S.TOPIC)
    e.add_opportunity("MTG", "first_q_topic", {"with": "F"}, "MTG")
    e.lock_prediction(id="P-F1", person_id="X", matter_id="M2", behavior_key="first_q_topic", bind_mode="specific_item",
                      opportunity_id="MTG", rules=[S.H1, S.H2], prob_dist={"职责": 0.6, "专家需求": 0.25, "其他": 0.15})
    e.confirm_outcome("MTG", "first_q_topic", 1, "专家需求")
    e.settle_prediction("P-F1", {})
    return w


def test_calibration_panel_separates_types_and_matters():
    w = two_type_world()
    p = calibration_panel(w)
    assert set(p["groups"]) == {"binary", "categorical"}  # 不混合平均
    b, c = p["groups"]["binary"], p["groups"]["categorical"]
    assert (b["locked"], b["settled"], b["void"], b["mean_brier"], b["mean_baseline"], b["range"]) == (3, 2, 1, 0.30625, 0.25, "0–1")
    assert (c["settled"], c["mean_brier"], c["range"], c["small_sample"]) == (1, 0.945, "0–2", True)
    assert b["vs_baseline"] == "高于基线" and c["vs_baseline"] == "高于基线"  # 只陈述数字
    assert [m["matter_id"] for m in p["per_matter"]] == ["M", "M2"]
    assert set(p["per_matter"][0]["groups"]) == {"binary"} and set(p["per_matter"][1]["groups"]) == {"categorical"}
    html = render_calibration(p)
    assert "仅供参考" in html and "不混合平均" in html and "越低越好" in html
    assert "下结论" in html and not any(x in html for x in ("判断力很", "你很准", "你不准", "记忆不可靠", "记忆很可靠"))


def test_calibration_excludes_corrected_by_default():
    w = S.build_world_a()
    w.correct_fact("E4", "submitter", "user", confirmed_by_user=True)
    p = calibration_panel(w)
    assert p["groups"]["binary"]["mean_brier"] == 0.49 and p["excluded_count"] == 1
    assert next(r for r in p["rows"] if r["id"] == "P-2")["excluded"]
    assert "已更正，默认不计入" in render_calibration(p)
    assert calibration_panel(w, include_corrected=True)["groups"]["binary"]["mean_brier"] == 0.30625


# ───────── 隐私：Redactor ─────────
def test_redactor_modes():
    w = S.build_world_b()
    w.persons["F"].display_name, w.persons["F"].code = "方明", "F"
    code, names, masked = Redactor(w, "code"), Redactor(w, "names"), Redactor(w, "masked")
    assert (code.p("F"), names.p("F"), code.p(SELF), masked.p(SELF)) == ("F", "方明", "我", "我")
    assert code.t("方明说可以帮忙") == "F说可以帮忙" and names.t("方明说可以帮忙") == "方明说可以帮忙"
    assert masked.t("方明说可以帮忙") == "●●●" and masked.p("F") != "F" and masked.p("F").startswith("●")
    assert masked.p("F") != masked.p("M")  # 遮挡后仍能区分是不是同一个人，但看不出是谁
    with pytest.raises(ValueError):
        Redactor(w, "half")


def check_masked(html: str):
    leaked = [s for s in SENSITIVE if s in html]
    assert not leaked, leaked


def test_masked_rendering_hides_everything(tmp_path):
    w, st, mid = cfo(tmp_path)
    rd = Redactor(w, "masked")
    pf = build_profile(w, "F", st)
    for name, html in {
        "profile": render_html(pf, rd),
        "quadrants": render_quadrants(build_quadrants(w, rd)),
        "graph": render_graph_browser(w, st, rd),
        "matter": render_matter_page(workbench_view(w, mid, st), build_matter_graph(w, mid, st, rd), rd),
        "calibration": render_calibration(calibration_panel(w, rd=rd)),
        "app": render_app(w, st, mode="masked"),
    }.items():
        check_masked(html)
        assert "●" in html, name
    # 对照：不遮挡时这些内容确实存在（排除“测试词本来就不会出现”的假阳性）
    full = render_app(w, st, mode="names")
    assert all(s in full for s in ("争取集团专家支持", "表示愿意打听", "方明", "CFO", "先问职责"))


def test_code_mode_hides_names_only(tmp_path):
    w, st, mid = cfo(tmp_path)
    html = render_app(w, st, mode="code")
    assert "方明" not in html and "马力" not in html and "争取集团专家支持" in html and "CFO" in html
    assert "<b>F</b>｜CFO" in html


# ───────── 隐私：解锁 ─────────
def test_app_lock_unlock_idle_and_cooldown(tmp_path):
    p = str(tmp_path / "lock.db")
    st = Store(p, PASS); st.new_conversation("c", "机密"); st.add_message("c", "user", "原话"); st.close()
    now = [0.0]
    lock = AppLock(p, idle_seconds=60, max_attempts=3, cooldown=30, clock=lambda: now[0])
    assert lock.locked
    with pytest.raises(Locked):
        lock.store
    for _ in range(2):
        with pytest.raises(crypto.WrongPassphrase):
            lock.unlock("错")
    with pytest.raises(crypto.WrongPassphrase):
        lock.unlock("错")  # 第 3 次：触发冷却
    with pytest.raises(TooManyAttempts):
        lock.unlock(PASS)  # 冷却期内连正确口令也不行
    now[0] = 31
    lock.unlock(PASS)
    assert lock.store.messages("c")[0]["text"] == "原话"
    now[0] = 80  # 距上次使用 49 秒：未超时
    assert not lock.locked
    now[0] = 200  # 超过 60 秒空闲：自动上锁
    assert lock.locked
    with pytest.raises(Locked):
        lock.store
    lock.unlock(PASS)
    key = lock.store
    lock.lock()
    assert lock.locked and key.key == b""  # 不再持有密钥


def test_capture_protection_markers(tmp_path):
    w, st, mid = cfo(tmp_path)
    html = render_app(w, st)
    assert "name='privacy-capture' content='graph'" in html and "data-no-capture='graph'" in html and "@media print" in html
    assert {"android", "macos", "windows", "electron"} <= set(privacy.NATIVE_FLAGS)  # 系统级禁截屏需原生外壳，网页只做标记


# ───────── 应用外壳：真实浏览器（电脑 + 手机） ─────────
JS = """const {chromium}=require('playwright');(async()=>{const b=await chromium.launch();const out={};
const file='file://'+process.argv[2];
for (const [n,w,h] of [['desktop',1280,800],['mobile',390,844]]){
 const p=await b.newPage({viewport:{width:w,height:h}});await p.goto(file);const r={};
 const vis=async s=>await p.evaluate(s=>{const e=document.querySelector(s);return !!e&&getComputedStyle(e).display!=='none'&&e.getBoundingClientRect().width>0},s);
 r.aside=await vis('aside');r.bottom=await vis('nav.bottom');
 r.bottomLabels=await p.evaluate(()=>[...document.querySelectorAll('nav.bottom a')].map(a=>a.textContent));
 r.workbenchShown=await vis('[data-view=workbench].on');
 const sw=async()=>await p.evaluate(()=>document.documentElement.scrollWidth);
 r.sw={};r.sw.workbench=await sw();
 await p.evaluate(()=>{location.hash='#/people'});await p.waitForTimeout(80);r.people=await vis('[data-view=people].on .quads');r.sw.people=await sw();
 r.cards=await p.evaluate(()=>document.querySelectorAll('[data-view=people] .pcard').length);
 await p.click('[data-view=people] .pcard'+process.argv[3]);await p.waitForSelector('[data-view=person].on');r.person=await vis('[data-view=person].on');r.sw.person=await sw();
 r.personTitle=await p.evaluate(()=>document.querySelector('[data-view=person].on h2').textContent);
 r.drawerClosed=!(await p.evaluate(()=>document.getElementById('drawer').classList.contains('open')));
 const ev=await p.$('[data-view=person].on [data-evidence]');
 if(ev){await ev.click();r.drawerOpen=await p.evaluate(()=>document.getElementById('drawer').classList.contains('open'));
   r.drawerText=await p.evaluate(()=>document.querySelector('#drawer div').textContent);}
 await p.evaluate(()=>{location.hash='#/graph'});await p.waitForTimeout(80);r.graph=await vis('[data-view=graph].on');r.sw.graph=await sw();
 r.orgPanel=await vis('.gpanel[data-sub=org].on');
 const tabs=await p.$$('[data-view=graph] .tab');await tabs[1].click();
 r.matterPanel=await p.evaluate(()=>[...document.querySelectorAll('.gpanel')].filter(x=>x.classList.contains('on')).map(x=>x.dataset.sub));
 await p.evaluate(()=>{location.hash='#/calibration'});await p.waitForTimeout(80);r.cal=await vis('[data-view=calibration].on');r.sw.cal=await sw();
 r.html=await p.evaluate(()=>document.documentElement.outerHTML);
 out[n]=r;await p.close();}
console.log(JSON.stringify(out));await b.close();})();"""


def run_browser(tmp_path, html, name, person="[data-person=F]"):
    page = tmp_path / f"{name}.html"
    page.write_text(html, encoding="utf-8")
    js = tmp_path / "shell.js"
    js.write_text(JS)
    r = subprocess.run(["node", str(js), str(page), person], capture_output=True, text=True, timeout=180)
    if r.returncode != 0 and "Cannot find module" in r.stderr:
        pytest.skip("没有 playwright")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="需要 node + playwright")
def test_shell_desktop_and_mobile_navigation(tmp_path):
    w, st, mid = cfo(tmp_path)
    out = run_browser(tmp_path, render_app(w, st, mode="code", toggle_href="masked.html"), "app")
    d, m = out["desktop"], out["mobile"]
    assert d["aside"] and not d["bottom"]  # 电脑端：左栏导航
    assert m["bottom"] and not m["aside"] and m["bottomLabels"] == ["工作台", "人物", "关系图"]  # 手机端：底部导航
    for r in (d, m):
        assert r["workbenchShown"] and r["people"] and r["cards"] == 2 and r["person"] and r["graph"] and r["orgPanel"]
        assert r["matterPanel"] == ["MT1"] and r["cal"]
        assert r["personTitle"].startswith("F ") and "方明" not in r["html"]
        assert r["drawerClosed"] and r["drawerOpen"] and "他先问专家具体要解决哪个问题" in r["drawerText"]  # 点证据 → 抽屉
    assert m["sw"] and max(m["sw"].values()) <= 390  # 手机端任何视图都不横向滚动


@pytest.mark.skipif(not shutil.which("node"), reason="需要 node + playwright")
def test_masked_shell_leaks_nothing_in_any_view(tmp_path):
    w, st, mid = cfo(tmp_path)
    out = run_browser(tmp_path, render_app(w, st, mode="masked", toggle_href="app.html"), "masked", "[data-person=pA]")
    for name, r in out.items():
        check_masked(r["html"])  # 整个 DOM（含未显示的视图）都不含敏感内容
        assert r["personTitle"].startswith("●")


# ───────── SVG 必须是合法 XML（曾经因为一个没加引号的属性，把后面的单元全吞掉了） ─────────
def test_svgs_are_well_formed_xml(tmp_path):
    import re
    import xml.etree.ElementTree as ET
    w, st, mid = cfo(tmp_path)
    for rd in (Redactor(w, "plain"), Redactor(w, "code"), Redactor(w, "masked")):
        for svg in re.findall(r"<svg.*?</svg>", render_graph_browser(w, st, rd), flags=re.S):
            root = ET.fromstring(svg.replace("&", "&amp;") if "&amp;" not in svg else svg)
            assert root.tag.endswith("svg")
    org = ET.fromstring(render_org_svg(build_org_graph(w)))
    texts = ["".join(t.itertext()) for t in org.iter() if t.tag.endswith("text")]
    assert any("Finance 线" in t for t in texts) and any("CEO 办公室" in t for t in texts)  # 三个单元都画出来了
    assert any("马力 · 战略组负责人" in t for t in texts) and any("方明 · CFO" in t for t in texts)
