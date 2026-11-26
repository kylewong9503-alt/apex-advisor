# 识人系统（People Reading System）v4.6

帮助用户在具体事情上做决定，并能事后查清判断错在哪一步。开发说明见需求文档 v4.6。

## 当前进度：第一项交付

纯逻辑层（零依赖，内存实现）加最简演示页：

- `people_reading/engine.py`：锁定、结算、前提核对、当前比较状态、事实更正、校准面板
- `people_reading/cards.py`：判断卡文案模板与结算首屏
- `people_reading/scenarios.py`：验收用例数据
- `people_reading/demo/demo.html`：演示页（C5-g 与 A.5.6）

```bash
python -m pytest people_reading/tests   # 27 项测试
python -m people_reading.demo           # 重新生成演示页
```

后续按文档第 11 节：固定计算 → 可试用工作台（本地模型、加密存储）→ 议题与解读 → 会前会后闭环 → 全局视图。
