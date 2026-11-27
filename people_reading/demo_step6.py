"""第 6 步演示：python -m people_reading.demo_step6 → demo/step6.html
观察排序、象限自动计算与迁移、承诺账本。数字与状态全部由程序算出。
"""
from __future__ import annotations

import datetime
import html

from . import scenarios as S
from .classify import closeness, depth, refresh_quadrants, suggest_quadrant
from .profile import build_profile, render_html
from .ranking import discriminating_signals
from .workbench import page
from .world import SELF

E = html.escape
OUT = __import__("pathlib").Path(__file__).parent / "demo" / "step6.html"


def table(head, rows):
    return ("<table style='border-collapse:collapse;width:100%;font-size:13px'><tr>" + "".join(f"<th style='border:1px solid var(--line);padding:4px 8px;text-align:left'>{E(h)}</th>" for h in head) + "</tr>"
            + "".join("<tr>" + "".join(f"<td style='border:1px solid var(--line);padding:4px 8px'>{E(str(c))}</td>" for c in r) + "</tr>" for r in rows) + "</table>")


def run() -> str:
    w = S.build_world_a()
    sec = []
    # 1 观察排序
    w.rate_scene("X", "他人提交的跨部门提案", "observe")
    w.rate_scene("X", "我提交的部门内提案", "observe")
    w.rate_scene("X", "他人提交的部门内提案", "ask", 2)
    sig = discriminating_signals(w, "X")
    sec.append("<h2>观察排序（X 的三条规则之间）</h2><p class='mut'>先筛掉区分力不足的场景；零代价（观察信号）按区分力排；"
               "正代价（当面询问，代价由你评）按区分力÷代价排在其后；没评估代价的单列在最后。“我提交的跨部门提案”三条规则预测相同，被筛掉。</p>"
               + table(["#", "类型", "代价", "区分力", "排序分", "看到什么 / 能分清哪两种解释"],
                       [(i + 1, e["kind"] or "待评估代价", "—" if e["cost"] is None else e["cost"], f"{e['power']:.0%}",
                         f"{e['score']:.3g}" if e["status"] == "ranked" else "—", e["text"]) for i, e in enumerate(sig)]))
    # 2 象限自动计算
    rows = []
    for pid in ("X", "丁", "戊", "丙", "下属"):
        s = suggest_quadrant(w, pid)
        rows.append((pid, s.quadrant, f"{s.closeness['score']}（边 {s.closeness['n_edges']}、近 30 天互动 {s.closeness['n_recent']}）",
                     f"{s.depth['score']}（有效证据 {s.depth['effective_evidence']}、已结算 {s.depth['n_settled']}）"))
    sec.append("<h2>象限自动计算</h2><p class='mut'>closeness 只用全局互动关系边与互动频率，议题里的临时角色不影响；depth 看有效证据量与预测表现（回忆算半份，更正后重算的预测不计入）。"
               "门槛 0.4 / 0.5，过程完整给出，不下结论。手动设置优先。</p>"
               + table(["人物", "建议象限", "closeness", "depth"], rows))
    # 3 迁移
    h1 = w.add_hypothesis("X", "他对谁都追问", scope="person")
    h2 = w.add_hypothesis("X", "他对我格外严格", scope="relation")
    refresh_quadrants(w)  # 先算出自动象限（近-深）
    w.set_quadrant("X", "远-浅")  # 你手动改成远-浅（近→远：只记修订）
    w.set_quadrant("X", None)  # 取消手动，回到自动的近-深（远→近：触发迁移）
    rev = [r for r in w.revisions if r.kind == "quadrant_migration"][-1]
    sec.append("<h2>象限迁移（远 → 近）</h2>" + table(
        ["判断", "范围", "迁移后状态"], [(h.statement, "关于他本人（person）" if h.scope == "person" else "关于我们的关系（relation）", h.status) for h in (h1, h2)])
        + f"<p class='mut'>修订：{E(rev.reason)}；person 类规则保留，关系类规则标 needs_review，并生成待办“{E(next(f.text for f in w.followups if f.kind == 'verify_role'))}”。</p>")
    # 4 承诺
    w.today_fn = lambda: datetime.date(2026, 11, 27).toordinal()
    o1 = w.add_observation("C-1", ("丁说下周五前把名单发我",), "behavior", SELF, "丁", "丁答应下周五前把名单发我").id
    o2 = w.add_observation("C-2", ("丁把名单发过来了",), "behavior", SELF, "丁", "丁把名单发过来了").id
    o3 = w.add_observation("C-3", ("我答应丁周三给他材料",), "behavior", SELF, "丁", "我答应周三给丁材料").id
    c1 = w.add_commitment("丁", SELF, "把名单发我", evidence_obs_id=o1, due_at=w.today_fn() - 3, due_text="下周五前")
    w.add_commitment("丁", SELF, "对接预算表", evidence_obs_id=o1, due_at=w.today_fn() - 2, due_text="上周")
    w.close_commitment(c1.id, "fulfilled", closing_obs_id=o2)
    w.add_commitment(SELF, "丁", "周三给他材料", evidence_obs_id=o3, due_at=w.today_fn() + 3, due_text="周三")
    pf = build_profile(w, "丁")
    led = pf["commitments"]
    STATE = {"open": "进行中", "overdue": "已逾期", "fulfilled": "已兑现", "broken": "未兑现", "cancelled": "已取消"}
    sec.append("<h2>承诺账本（丁）</h2><p class='mut'>只记录谁欠谁什么、何时到期、依据哪句原话；兑现/违约必须回指原话；逾期由到期日与今天推出；不打分，不据此给人下判断。</p>"
               + "<p><b>他欠我</b></p>" + table(["承诺", "到期", "状态"], [(r["content"], r["due"], STATE[r["state"]]) for r in led["owes_me"]])
               + "<p><b>我欠他</b></p>" + table(["承诺", "到期", "状态"], [(r["content"], r["due"], STATE[r["state"]]) for r in led["i_owe"]]))
    return page("<h1>Kagami v4.6 · 第 6 步演示</h1><p class='mut'>观察排序完善、象限自动计算与迁移、承诺账本。数据来自验收 A 的 X 与同级人物。</p>" + "".join(sec), "Kagami 第 6 步演示")


if __name__ == "__main__":
    OUT.write_text(run(), encoding="utf-8")
    print("已写入", OUT)
