"""整套流程走一遍：python -m people_reading.demo_walkthrough [输出目录]
起一个真的本地服务（脚本化模型、临时加密库），按页面上的动作一步步走完：
说一句 → 确认草稿 → 会前判断 → 锁定预测 → 会上那一句 → 会后结算 → 结果；每一屏截图，拼成总览。
模型输出是脚本化演示数据；其余全部由服务和引擎算出。
"""
from __future__ import annotations

import html as _h
import http.client
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
from urllib.parse import quote

from .llm import ScriptedModel
from .server import serve
from .tests import test_step4 as T4

NODE = r"""
const {chromium}=require('playwright');const fs=require('fs');
(async()=>{const [port,cookie,out,n,w,h]=process.argv.slice(2);const b=await chromium.launch();
const c=await b.newContext({viewport:{width:+w,height:+h},deviceScaleFactor:1});
await c.addCookies([{name:'sid',value:cookie,url:'http://127.0.0.1:'+port}]);
const p=await c.newPage();
for(let i=1;i<=+n;i++){await p.goto('http://127.0.0.1:'+port+'/'+process.argv[8]+'#/'+i);await p.waitForTimeout(250);
 await p.screenshot({path:out+'_'+i+'.png'});}
await b.close()})();
"""


class Walk:
    def __init__(self, outdir: pathlib.Path) -> None:
        self.out = outdir
        self.out.mkdir(parents=True, exist_ok=True)
        for old in self.out.glob("s*_*.png"):
            old.unlink()
        tmp = pathlib.Path(tempfile.mkdtemp())
        w, st, it = T4.env(tmp, [])
        path = st.path
        st.close()
        prep = {**T4.PREP_OUT, "premise_options": [{"action": "主动展示或讲解分工说明", "anchor_event": "F 提出第一个实质性问题"}]}
        outs = [T4.C1_OUT, T4.C2_OUT, prep, T4.report_out(T4.REPORT_DRAFTS), T4.proposal(), T4.REVIEW]
        self.srv = serve(path, "127.0.0.1", 0, model=ScriptedModel(outs))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]
        self.shots: list = []
        (self.out / "shot.js").write_text(NODE)
        body = "passphrase=" + quote(T4.PASS)
        st_, h, _ = self.req("POST", "/unlock", body)
        self.cookie = h["Set-Cookie"].split(";")[0].split("=", 1)[1]

    def req(self, method, path, body="", cookie=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        hd = {"Host": f"127.0.0.1:{self.port}"}
        if body:
            hd["Content-Type"] = "application/x-www-form-urlencoded"
        if cookie and getattr(self, "cookie", None):
            hd["Cookie"] = "sid=" + self.cookie
        c.request(method, path, body=body, headers=hd)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read().decode("utf-8", "replace")

    def page(self, label: str, path: str = "/") -> str:
        """取当前页面并给每一屏截图（手机宽度）。"""
        st, _, body = self.req("GET", path)
        n = body.count("<section class='step")
        tag = f"{len(self.shots) + 1:02d}"
        subprocess.run(["node", str(self.out / "shot.js"), str(self.port), self.cookie, str(self.out / f"s{tag}"), str(n), "390", "844",
                        "", path.lstrip("/")], check=True)
        self.shots += [(label, f"s{tag}_{i}.png") for i in range(1, n + 1)]
        return body

    def post(self, path, **kw):
        st, _, _ = self.req("POST", path, "&".join(f"{k}={quote(str(v))}" for k, v in kw.items()))
        assert st == 303, (path, st)

    def confirm_here(self, body: str) -> bool:
        """当前页面如果是确认草稿，就全部记下（一屏一条原话）；返回是否处理了。"""
        if "都记下吗" in body:
            ids = re.findall(r"data-id='([^']*)'", body)
            self.post("/confirm_many", msg=self.fields(body)["msg"], ids=",".join(ids))
            return True
        if "记下这一条吗" in body:
            self.post("/confirm", id=self.fields(body)["id"], choice=0)
            return True
        return False

    @staticmethod
    def fields(body: str) -> dict:
        m = re.findall(r"data-fields='([^']*)'", body)
        return json.loads(_h.unescape(m[0])) if m else {}


def run(outdir: pathlib.Path) -> pathlib.Path:
    w = Walk(outdir)
    page = w.page("① 说一句", "/")
    w.post("/say", text=T4.C1_TEXT)
    for _ in range(10):  # 每条草稿：记下 / 不记
        body = w.page("② 确认草稿")
        if not w.confirm_here(body):
            break
    if w.srv.app.reading:  # 记下之后的回执
        w.page("②′ 已归档回执")
        w.post("/reading/done")
    w.page("② 还没有判断：先问你")
    w.post("/say", text=T4.C2_TEXT)
    for _ in range(10):
        body = w.page("② 确认你的判断")
        if not w.confirm_here(body):
            break
    if w.srv.app.reading:
        w.page("②′ 已归档回执")
        w.post("/reading/done")
    body = w.page("③ 会前：判断与分叉")
    w.post("/lock/start")
    w.post("/lock/next", k="cats", choice=0)
    body = w.page("④ 锁定：各解释指向哪类")
    for hid, c in (("H1", 0), ("H2", 1)):
        w.post("/lock/next", k="hyp", choice=c, hid=hid)
    w.post("/lock/next", k="lean", choice=0)
    w.page("④ 锁定：把握")
    w.post("/lock/next", k="conf", choice=1)
    w.page("④ 锁定：这个预测在什么情况下才算数")
    w.post("/lock/next", k="premise", choice=0)
    w.page("④ 锁定：截止点")
    w.post("/lock/next", k="until", choice=0)
    w.page("④ 锁定：看一眼再锁")
    w.post("/lock/commit")
    w.page("⑤ 会上，先说这一句")
    w.post("/act")
    w.post("/settle/start")
    w.page("⑥ 会后：什么时候的事")
    w.post("/settle/when", choice=0)
    w.page("⑥ 会后：开放题")
    w.post("/settle/report", text=T4.REPORT)
    body = w.page("⑥ 会后：核对前提")
    opts = len(re.findall(r"role='radio'", body))
    w.post("/settle/premise", q=w.fields(body)["q"], choice=0)
    w.page("⑥ 会后：确认结果")
    body = w.req("GET", "/")[2]
    cats = list(w.srv.app.sess._behavior_for()[0].categories)
    w.post("/settle/outcome", choice=cats.index("专家需求"))
    for _ in range(10):
        body = w.page("⑦ 确认报告里的草稿")
        if not w.confirm_here(body):
            break
    w.page("⑧ 结果")
    w.srv.shutdown()
    w.srv.server_close()
    # 总览页
    cells = "".join(f"<figure><img src='{f}' width='195'><figcaption>{_h.escape(l)}</figcaption></figure>" for l, f in w.shots)
    sheet = (f"<!doctype html><meta charset='utf-8'><title>Kagami 全流程</title><style>body{{margin:0;padding:24px;background:#eef2f7;font:13px Inter,'Noto Sans SC',sans-serif}}"
             f"main{{display:grid;grid-template-columns:repeat(auto-fill,195px);gap:16px}}figure{{margin:0}}img{{display:block;border:1px solid #dbe3ec;border-radius:10px}}"
             f"figcaption{{margin-top:6px;color:#546072}}</style><main>{cells}</main>")
    (outdir / "walkthrough.html").write_text(sheet, encoding="utf-8")
    return outdir / "walkthrough.html"


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "walkthrough_out")
    print("已写入", run(out))
