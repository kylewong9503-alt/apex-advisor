# 识人系统（People Reading System）v4.6

帮助用户在具体事情上做决定，并能事后查清判断错在哪一步。开发说明见需求文档 v4.6。

## 当前进度：第一项交付 + 第 1 步（固定计算）+ 第 2 步（对话录入、加密存储、本地模型）

纯逻辑层（零依赖，内存实现）加最简演示页：

- `people_reading/engine.py`：锁定、结算、前提核对、当前比较状态、事实更正、校准面板
- `people_reading/cards.py`：判断卡文案模板与结算首屏
- `people_reading/world.py`：人、组织、三层关系、草稿、观察、机会、人物合并、解读失效（验收 B）
- `people_reading/stats.py`：发生率、三维对照、观察排序
- `people_reading/crypto.py` / `store.py`：口令派生密钥，AES-256-GCM 逐列加密的 SQLite；备份与导出整体加密
- `people_reading/llm.py` / `schema.py`：本地模型适配器（仅回环地址）、输出校验与重试
- `people_reading/intake.py`：对话录入、草稿、确认、点回原话
- `people_reading/model_eval.py`：本地模型选型测试
- `people_reading/scenarios.py`：验收用例数据
- `people_reading/demo/demo.html`：演示页（C5-g 与 A.5.6）

```bash
python -m pytest people_reading/tests   # 82 项测试
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
