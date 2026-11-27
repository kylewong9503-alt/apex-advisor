# 识人系统（People Reading System）v4.6

帮助用户在具体事情上做决定，并能事后查清判断错在哪一步。开发说明见需求文档 v4.6。

## 当前进度：第一项交付 + 第 1 步（固定计算）+ 第 2 步（对话录入、加密存储、本地模型）+ 第 3 步（议题与解读）+ 第 4 步（会前会后闭环）+ 第 5 步（全局视图与隐私）+ 第 6 步（观察排序、象限自动计算、承诺账本）

纯逻辑层（零依赖，内存实现）加最简演示页：

- `people_reading/engine.py`：锁定、结算、前提核对、当前比较状态、事实更正、校准面板
- `people_reading/cards.py`：判断卡文案模板与结算首屏
- `people_reading/world.py`：人、组织、三层关系、草稿、观察、机会、人物合并、解读失效（验收 B）
- `people_reading/stats.py`：发生率、三维对照、观察排序
- `people_reading/crypto.py` / `store.py`：口令派生密钥，AES-256-GCM 逐列加密的 SQLite；备份与导出整体加密
- `people_reading/llm.py` / `schema.py`：本地模型适配器（仅回环地址）、输出校验与重试
- `people_reading/intake.py`：对话录入、草稿、确认、点回原话
- `people_reading/skills.py` / `assistant.py` / `profile.py`：竞争解释 Skill、对话编排（调用 Skill / 回忆 / 事实更正）、人物个人页
- `people_reading/judgment.py` / `premeeting.py` / `settle_flow.py` / `graph.py` / `workbench.py`：三行判断卡与两条时间线、会前准备、会后结算、议题关系小图、工作台与结算首屏
- `people_reading/quadrants.py` / `panels.py` / `privacy.py` / `shell.py`：四象限首页、跨议题校准面板、隐私遮挡与解锁、应用外壳（电脑左栏 / 手机底部导航）
- `people_reading/ranking.py` / `classify.py`：观察排序（自动枚举候选场景、代价由你评估）、象限自动计算与迁移
- `people_reading/model_eval.py`：本地模型选型测试
- `people_reading/scenarios.py`：验收用例数据
- `people_reading/demo/demo.html`：演示页（C5-g 与 A.5.6）

```bash
python -m pytest people_reading/tests   # 229 项测试；python -m people_reading.demo_step3 … demo_step6 生成演示页
python -m people_reading.demo           # 重新生成演示页
```

后续按文档第 11 节：固定计算 → 可试用工作台（本地模型、加密存储）→ 议题与解读 → 会前会后闭环 → 全局视图。

## 本地模型（第 2 步）

```bash
ollama serve &  &&  ollama pull deepseek-r1:14b          # 按你的显存换尺寸
python -m people_reading.model_eval --model deepseek-r1:14b --repeats 5
```

环境变量：`PR_MODEL_BASE_URL`（默认 `http://127.0.0.1:11434/v1`）、`PR_MODEL`。
非本机地址一律拒绝，没有开关；调用也不经系统代理。

## 外部 API（DeepSeek 官方，可选）

> ⚠ 这会把你的原话发送到外部服务，与 v4.6 “MVP 只接本地模型”的隐私规则冲突。默认关闭，必须显式开启。

```bash
export PR_PROVIDER=deepseek
export PR_ALLOW_EXTERNAL=1            # 明确知情并开启
export DEEPSEEK_API_KEY=sk-...        # 只放环境变量，不要写进文件或提交
```

```python
from people_reading.llm import make_model
model = make_model()                  # 按 PR_PROVIDER 返回 LocalModel 或 RemoteModel
# Intake(store, world, model) 照常使用；远程失败会退回手动表单，原话仍已加密保存
```

`PR_MODEL` 默认 `deepseek-chat`；`deepseek-reasoner` 也支持。只允许 `https://api.deepseek.com`。
密钥不落盘、不进日志、不进异常信息。评测：`python -m people_reading.model_eval --provider deepseek`。

## 界面：一问一答（flow.py）
主界面是 `people_reading/flow.py`：每屏一个问题、几个选项、一个按钮，答完直接给结论。会前 = 判断 →（分叉问题）→ 会上那一句话；会后 = 开放题（不给候选答案）→ 前提核对 → 结果。人物页、关系图、校准面板仍在 `shell.render_app`，作为“资料”入口。演示：`python -m people_reading.demo_flow`。

## 手机用法（本地服务）
`python -m people_reading.server --db my.db` 在本机 127.0.0.1:8765 起服务，口令在浏览器里输入。手机访问需在电脑上起服务并放在 Tailscale 等私有加密通道里：
`--host <tailscale-ip> --allow-remote --allow-host <机器名>:8765`；不要暴露公网。手机浏览器里“添加到主屏幕”即成 app（无 service worker，不缓存任何人物信息）。
