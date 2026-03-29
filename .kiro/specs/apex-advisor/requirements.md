# 需求文档

## 简介

Apex Advisor 是一款职场适应辅助 App，帮助用户系统化地整理和管理职场关键信息，并基于这些信息自动生成适合具体场景的 AI Prompt 模板，从而提升用户在汇报、沟通、协作、规划等职场场景中的表现。

App 的核心结构分为四层：
- **Layer 1 — Know Your Team**：了解上司与团队的性格、沟通偏好、决策风格等
- **Layer 2 — Know Your Company**：了解公司文化、战略、组织结构与关键干系人
- **Layer 3 — Know Your Work**：定义不同工作场景下的交付物标准库，支持 AI 对草稿进行合规性评审
- **Layer 4 — Know Your Industry**：持续投喂行业原始材料，由 AI 提炼结构化行业情报与话题素材库

在四层信息的基础上，系统自动生成常态化 AI Prompt 模板，供用户在具体职场场景中直接使用或二次编辑。

---

## 词汇表

- **Apex_Advisor**：本 App 整体系统
- **User**：使用本 App 的职场人士
- **Profile**：用户在某一层（Team/Company/Work/Industry）下录入的结构化信息集合
- **Team_Profile**：Layer 1 中关于上司与团队成员的信息集合
- **Company_Profile**：Layer 2 中关于公司的信息集合
- **Work_Profile**：Layer 3 中关于个人工作的信息集合
- **Industry_Profile**：Layer 4 中关于行业的信息集合
- **Prompt_Template**：由系统根据 Profile 信息自动生成的 AI Prompt 模板
- **Scene**：用户选择的具体职场应用场景（如"汇报优化"、"提案撰写"等）
- **Prompt_Engine**：负责根据 Profile 和 Scene 生成 Prompt_Template 的核心模块
- **Taboo**：Team_Profile 中记录的上司或团队成员的禁忌雷区信息
- **Behavior_Tag**：由 Behavior_Profiler 提炼生成的结构化行为特征标签，描述团队成员的典型行为模式（如：回避冲突型、数据驱动型、微观管理倾向等）
- **Behavior_Profiler**：负责与 User 进行多轮对话、提炼并生成 Behavior_Tag 的 AI 交互模块
- **Company_Intelligence**：由 AI 从 User 输入的碎片化公司相关信息中提炼出的结构化公司洞察，涵盖经营业绩动态、战略方向与重点、组织文化信号、内部规则与潜规则四个维度
- **Intelligence_Input**：User 向系统输入的原始公司相关信息片段，包括但不限于会议记录、内部通知、领导讲话、财报摘要、内部邮件等
- **Intelligence_Digest**：AI 对一批 Intelligence_Input 进行分类、提炼和总结后生成的结构化摘要报告
- **Intelligence_Classifier**：负责对 Intelligence_Input 进行分类、提炼并更新 Company_Intelligence 知识库的 AI 处理模块
- **Stakeholder_Note**：Company_Profile 中关键干系人的辅助性记录条目，包含姓名、部门和影响力描述
- **Work_Profile**：Layer 3 中关于个人工作的信息集合，核心为交付物标准库
- **Deliverable_Standard**：User 针对特定工作场景定义的交付物规范条目，涵盖格式要求、体例要求、思路框架、立场与视角、习惯偏好五个维度
- **Work_Scene**：User 定义的具体工作交付物场景类型，如：向上汇报 PPT、战略分析报告、跨部门沟通邮件、会议纪要、提案文件等，每个 Work_Scene 对应一条 Deliverable_Standard
- **Deliverable_Review**：AI 根据 Deliverable_Standard 对 User 提交的草稿内容进行逐项核验，并输出符合/不符合标准的结构化评审报告
- **Standard_Dimension**：Deliverable_Standard 中的一个规范维度，包括：格式要求、体例要求、思路框架、立场与视角、习惯偏好
- **Draft_Input**：User 提交给系统进行评审的交付物草稿内容
- **Industry_Intelligence**：由 AI 从 User 输入的行业原始材料中提炼出的结构化行业洞察，涵盖行业趋势、竞争格局、监管动态、数据要点四个维度
- **Industry_Input**：User 向系统输入的原始行业相关材料，包括但不限于研报、行业数据、行业动态、竞品资讯、监管文件等
- **Industry_Digest**：AI 对一批 Industry_Input 进行分类、提炼和总结后生成的结构化行业摘要报告
- **Industry_Classifier**：负责对 Industry_Input 进行分类、提炼并更新 Industry_Intelligence 知识库的 AI 处理模块
- **Topic_Material**：从 Industry_Input 中提炼出的适合在人际沟通中使用的具体话题素材，包含话题标题、核心观点和数据支撑
- **Review_History**：系统针对特定 Work_Scene 下的 Deliverable_Standard 所积累的历次评审记录，每条记录包含评审时间、各 Standard_Dimension 的符合/不符合状态及改进建议
- **Deliverable_Pattern**：AI 基于 Review_History 提炼出的用户在特定 Work_Scene 下的交付物演变规律洞察，包括反复不符合的维度、持续改善的维度及整体合规率变化趋势
- **Chatbox**：贯穿整个 App 的全局统一 AI 对话入口，支持基于四层 Profile 的上下文感知问答、双 Box 布局（回答 + 思维链）及快捷指令
- **Context_Tag**：用户在 Chatbox 中手动勾选或由系统自动匹配的话题标签，对应四层 Profile 中的某一 Layer，用于指定本次对话的背景信息调取范围
- **Slash_Command**：在 Chatbox 输入框中以 `/` 触发的快捷指令，如 `/行为预判 @成员名`、`/评审草稿`、`/生成Prompt [Scene]`、`/行业洞察` 等
- **Contextual_Nudge**：系统在用户打开特定页面时主动推送的非阻断式情境提示，以 banner 或 inline 卡片形式展示，无需用户主动询问
- **Update_Reminder**：用户为 Industry_Intelligence 各维度设置的定期更新提醒，到期后系统推送 Contextual_Nudge 提醒投喂新材料
- **Meeting_Note**：用户上传或粘贴的会议纪要文本，作为独立档案存储于 Work_Profile 中，支持查阅与检索
- **Workstream**：用户在 Work_Profile 中维护的工作线条目，代表一个持续推进的工作方向或职责领域，包含名称、当前进度、目标/要求、负责人等字段
- **FollowUp_Item**：从 Meeting_Note 中提取或手动创建的跟进事项条目，包含事项描述、负责人（可选）、截止日期（可选）、关联 Workstream（可选）及确认状态
- **FollowUp_Extractor**：负责从 Meeting_Note 中自动识别并提取 Action Items、生成结构化 FollowUp_Item 列表的 AI 处理模块

---

## 需求

### 需求 1：用户账户管理

**用户故事：** 作为职场人士，我希望拥有个人账户，以便安全保存和管理我的所有职场信息。

#### 验收标准

1. THE Apex_Advisor SHALL 为每位 User 提供独立的账户空间，确保数据隔离。
2. WHEN User 完成注册，THE Apex_Advisor SHALL 创建一个空白的 Profile 集合，包含四层结构。
3. IF User 提供的登录凭据无效，THEN THE Apex_Advisor SHALL 拒绝登录并返回明确的错误提示。
4. WHEN User 登录成功，THE Apex_Advisor SHALL 在 500ms 内加载该 User 的所有 Profile 数据。

---

### 需求 2：Layer 1 — 团队信息管理（Know Your Team）

**用户故事：** 作为职场人士，我希望录入和管理上司及团队成员的性格与沟通偏好信息，并能通过与 AI 对话的方式深度挖掘成员的行为特征，生成结构化标签入库，以便在汇报、沟通和行为预判时做出更有针对性的准备。

#### 验收标准

1. THE Apex_Advisor SHALL 允许 User 在 Team_Profile 中添加、编辑和删除团队成员条目。
2. WHEN User 创建团队成员条目，THE Apex_Advisor SHALL 提供以下结构化字段：姓名/角色、性格特征、沟通偏好、决策风格、汇报偏好（数据驱动 / 感性 / 简洁）、禁忌雷区（Taboo）、行为特征标签列表。
3. THE Apex_Advisor SHALL 支持 User 为每位团队成员添加自由文本备注。
4. WHEN User 保存团队成员条目，THE Apex_Advisor SHALL 在 1 秒内完成持久化存储并给出保存成功的反馈。
5. IF User 尝试保存缺少姓名/角色字段的团队成员条目，THEN THE Apex_Advisor SHALL 阻止保存并提示必填字段。
6. THE Apex_Advisor SHALL 在每位团队成员的档案页面提供"AI 建档对话"入口，允许 User 与 Behavior_Profiler 进行多轮对话，描述该成员的行为表现与特征。
7. WHEN User 在 AI 建档对话中描述团队成员的行为，THE Behavior_Profiler SHALL 根据对话内容提炼并生成结构化的行为特征标签（例如：回避冲突型、数据驱动型、微观管理倾向、风险规避型等）。
8. WHEN Behavior_Profiler 生成行为特征标签后，THE Apex_Advisor SHALL 向 User 展示待确认的标签列表，并允许 User 在保存前对标签进行编辑、删除或手动添加。
9. WHEN User 确认行为特征标签，THE Apex_Advisor SHALL 将标签持久化保存至该团队成员的 Team_Profile 条目中，并在 1 秒内完成存储。
10. THE Apex_Advisor SHALL 支持 User 在任意时间重新发起 AI 建档对话，Behavior_Profiler SHALL 在新对话中读取该成员已有的行为特征标签作为上下文，并在对话结束后提供增量更新或覆盖的选择。
11. WHEN Prompt_Engine 生成涉及特定团队成员的 Prompt_Template，THE Prompt_Engine SHALL 将该成员的行为特征标签注入 Prompt_Template，以支持行为预判推断。
12. WHEN User 在 Scene 中触发针对特定团队成员的行为预判，THE Prompt_Engine SHALL 基于该成员的行为特征标签，在 Prompt_Template 中生成该成员在当前场景下的可能行为描述。
13. IF 团队成员的行为特征标签列表为空，THEN THE Apex_Advisor SHALL 在档案页面提示 User 尚未完成 AI 建档，并引导 User 进入 AI 建档对话流程。
14. THE Apex_Advisor SHALL 为每位团队成员提供独立的结构化档案页，分区展示以下内容：基本信息区（姓名、角色、头像可选）、沟通偏好区（沟通偏好、决策风格、汇报偏好，以标签/图标形式展示）、行为特征区（Behavior_Tag 列表，带置信度可视化，如进度条或颜色深浅）、禁忌雷区区（Taboo 列表，以警示色标注）、AI 建档入口（显著的"开始/更新 AI 建档对话"按钮）、备注区（自由文本备注）。
15. WHEN User 访问团队成员档案页，THE Apex_Advisor SHALL 在页面内以置信度可视化方式（进度条或颜色深浅）展示每个 Behavior_Tag 的 confidence 值。
16. WHEN User 访问团队成员档案页，THE Apex_Advisor SHALL 以警示色（如红色或橙色）突出展示该成员的所有 Taboo 条目。

---

### 需求 3：Layer 2 — 公司情报知识库（Know Your Company）

**用户故事：** 作为职场人士，我希望将碎片化的公司相关信息（会议记录、内部通知、领导讲话、财报摘要等）输入给 AI，由 AI 自动分类、提炼并形成结构化的公司情报知识库，以便深入了解公司的经营业绩动态与战略方向，从而在提案、汇报和战略判断中更有依据。

#### 验收标准

1. THE Apex_Advisor SHALL 提供 Intelligence_Input 录入入口，允许 User 以自由文本形式粘贴或输入原始公司相关信息片段，并支持为每条 Intelligence_Input 标注来源类型（会议记录 / 内部通知 / 领导讲话 / 财报摘要 / 新闻资讯 / 其他）。
2. WHEN User 提交一条或多条 Intelligence_Input，THE Intelligence_Classifier SHALL 对输入内容进行分析，并将提炼出的洞察按以下四个维度归类更新至 Company_Intelligence 知识库：经营业绩动态、战略方向与重点、组织文化信号、内部规则与潜规则。
3. WHEN Intelligence_Classifier 完成分类提炼后，THE Apex_Advisor SHALL 向 User 展示本次生成的 Intelligence_Digest，包含各维度的新增或更新内容摘要，并允许 User 在确认前对摘要内容进行编辑或删除。
4. WHEN User 确认 Intelligence_Digest，THE Apex_Advisor SHALL 将提炼结果增量合并至 Company_Intelligence 知识库，并在 1 秒内完成持久化存储。
5. THE Apex_Advisor SHALL 在 Company_Intelligence 知识库页面按四个维度分区展示当前所有已提炼的洞察内容，并为每条洞察标注最后更新时间及来源 Intelligence_Input 的类型。
6. WHEN User 再次提交新的 Intelligence_Input，THE Intelligence_Classifier SHALL 读取 Company_Intelligence 知识库中的现有内容作为上下文，并在提炼后提供"增量补充"或"覆盖更新"的选择。
7. THE Apex_Advisor SHALL 支持 User 在 Company_Intelligence 知识库中对任意维度的洞察内容进行手动编辑和删除。
8. THE Apex_Advisor SHALL 支持 User 在 Company_Intelligence 知识库中维护一个辅助性的关键干系人列表，每条 Stakeholder_Note 包含姓名、部门和影响力描述。
9. IF User 提交的 Intelligence_Input 内容过短或信息量不足以提炼有效洞察，THEN THE Intelligence_Classifier SHALL 向 User 返回提示，说明当前输入信息不足，并建议补充更多背景信息后重新提交。
10. WHEN Prompt_Engine 生成涉及公司背景的 Prompt_Template，THE Prompt_Engine SHALL 将 Company_Intelligence 知识库中的相关维度洞察注入 Prompt_Template，以支持提案撰写、战略判断等场景。

---

### 需求 4：Layer 3 — 工作交付物标准库（Know Your Work）

**用户故事：** 作为职场人士，我希望定义和管理不同工作场景下的交付物标准，包括格式要求、体例规范、思路框架、立场偏好和表达习惯，并能让 AI 根据这套标准对我的草稿进行评审，以便核验我的交付物是否达到相关要求。

#### 验收标准

1. THE Apex_Advisor SHALL 允许 User 在 Work_Profile 中创建、编辑和删除 Deliverable_Standard 条目，每条条目对应一个 Work_Scene（如：向上汇报 PPT、战略分析报告、跨部门沟通邮件、会议纪要、提案文件等）。
2. WHEN User 创建 Deliverable_Standard 条目，THE Apex_Advisor SHALL 提供以下五个 Standard_Dimension 供 User 填写：
   - 格式要求（如：PPT 页数上限、报告字数范围、章节结构）
   - 体例要求（如：行文风格、段落结构、标题层级规范）
   - 思路框架（如：问题-分析-建议、MECE 原则、金字塔结构）
   - 立场与视角（如：数据优先、结论前置、保守/激进倾向）
   - 习惯偏好（如：上司偏好的表达方式、特定词汇的使用或回避）
3. THE Apex_Advisor SHALL 支持 User 在每个 Standard_Dimension 中以自由文本形式填写规范内容，并允许部分维度留空。
4. IF User 尝试保存缺少 Work_Scene 名称的 Deliverable_Standard，THEN THE Apex_Advisor SHALL 阻止保存并提示必填字段。
5. WHEN User 在 Work_Profile 中选择一条 Deliverable_Standard 并提交 Draft_Input，THE Deliverable_Review SHALL 逐项对照该条目的各 Standard_Dimension 对草稿内容进行核验，并在 5 秒内生成结构化评审报告。
6. WHEN Deliverable_Review 生成评审报告，THE Apex_Advisor SHALL 按 Standard_Dimension 分区展示评审结果，每项标注"符合"或"不符合"，并对不符合项提供具体说明和改进建议。
7. THE Apex_Advisor SHALL 支持 User 在评审报告页面对 AI 的评审意见进行追问或要求进一步解释。
8. THE Apex_Advisor SHALL 允许 User 在 Work_Profile 中维护辅助性的项目列表，每条项目包含项目名称、状态（进行中 / 已完成 / 暂停）和可选的截止日期与描述。
9. WHEN Prompt_Engine 生成涉及工作场景的 Prompt_Template，THE Prompt_Engine SHALL 将与当前 Scene 匹配的 Deliverable_Standard 中的规范内容注入 Prompt_Template，以支持交付物撰写与合规性校验场景。
10. WHEN Deliverable_Review 完成一次评审，THE Apex_Advisor SHALL 将本次评审结果（评审时间、各 Standard_Dimension 的符合/不符合状态及改进建议）作为一条 Review_History 记录持久化保存至对应 Deliverable_Standard 条目下。
11. THE Apex_Advisor SHALL 在 Deliverable_Standard 条目页面提供评审历史入口，允许 User 查看该 Work_Scene 下的所有历史评审记录，按时间倒序排列。
12. WHEN 某个 Deliverable_Standard 下的 Review_History 记录数量达到 3 条及以上，THE Apex_Advisor SHALL 自动触发规律提炼，由 AI 分析历史评审数据并生成 Deliverable_Pattern，包含：反复不符合的维度（如"结论前置"习惯未养成）、持续改善的维度（如格式规范越来越好）、整体合规率的变化趋势。
13. WHEN Deliverable_Pattern 生成后，THE Apex_Advisor SHALL 在评审报告页面展示该 Work_Scene 下的规律洞察摘要，并在下次评审时将 Deliverable_Pattern 作为上下文注入，使 AI 能够给出更有针对性的改进建议。
14. THE Apex_Advisor SHALL 支持 User 在 Deliverable_Standard 条目页面查看当前 Deliverable_Pattern 的完整内容，并显示该规律基于多少条历史评审记录生成。
15. THE Apex_Advisor SHALL 在 Work_Profile 中提供会议纪要存档入口，允许 User 通过粘贴文本或上传文件的方式录入 Meeting_Note，每条 Meeting_Note 包含标题、会议日期、参会人员（可选）和正文内容。
16. WHEN User 保存 Meeting_Note，THE Apex_Advisor SHALL 在 1 秒内完成持久化存储，并在会议纪要列表中按日期倒序展示所有已存档的 Meeting_Note，支持按标题或日期检索。
17. THE Apex_Advisor SHALL 在 Work_Profile 中提供 Workstream 管理模块，允许 User 创建、编辑和删除 Workstream 条目，每条 Workstream 包含以下字段：名称（必填）、当前进度描述、目标与要求、负责人（可选）、状态（进行中 / 已完成 / 暂停）。
18. IF User 尝试保存缺少名称字段的 Workstream，THEN THE Apex_Advisor SHALL 阻止保存并提示必填字段。
19. WHEN User 上传或粘贴 Meeting_Note 后，THE FollowUp_Extractor SHALL 自动分析会议纪要内容，识别其中的 Action Items，并生成结构化的 FollowUp_Item 列表，每条 FollowUp_Item 包含：事项描述、负责人（可选）、截止日期（可选）、关联 Workstream（可选）。
20. WHEN FollowUp_Extractor 生成 FollowUp_Item 列表后，THE Apex_Advisor SHALL 向 User 展示待确认的 Follow-up 清单，允许 User 在保存前对每条条目进行编辑、删除，或手动新增条目。
21. WHEN User 确认 Follow-up 清单，THE Apex_Advisor SHALL 将所有 FollowUp_Item 持久化保存，并在 Work_Profile 的 Follow-up 清单页面按截止日期升序展示，支持按状态（待处理 / 已完成）筛选。
22. THE Apex_Advisor SHALL 允许 User 在 Follow-up 清单页面将任意 FollowUp_Item 标记为"已完成"，并支持手动编辑或删除任意条目。

---

### 需求 5：Layer 4 — 行业情报知识库（Know Your Industry）

**用户故事：** 作为职场人士，我希望持续向系统投喂研报、行业数据、行业动态等原始材料，由 AI 自动提炼结构化的行业洞察，并积累可用于人际沟通的话题素材，以便深化对行业的理解，并在与人交流时有具体话题和数据支撑。

#### 验收标准

1. THE Apex_Advisor SHALL 提供 Industry_Input 录入入口，允许 User 以自由文本形式粘贴或输入原始行业相关材料，并支持为每条 Industry_Input 标注来源类型（研报 / 行业数据 / 行业动态 / 竞品资讯 / 监管文件 / 其他）。
2. WHEN User 提交一条或多条 Industry_Input，THE Industry_Classifier SHALL 对输入内容进行分析，并将提炼出的洞察按以下四个维度归类更新至 Industry_Intelligence 知识库：行业趋势、竞争格局、监管动态、数据要点。
3. WHEN Industry_Classifier 完成分类提炼后，THE Apex_Advisor SHALL 向 User 展示本次生成的 Industry_Digest，包含各维度的新增或更新内容摘要，并允许 User 在确认前对摘要内容进行编辑或删除。
4. WHEN User 确认 Industry_Digest，THE Apex_Advisor SHALL 将提炼结果增量合并至 Industry_Intelligence 知识库，并在 1 秒内完成持久化存储。
5. THE Apex_Advisor SHALL 在 Industry_Intelligence 知识库页面按四个维度分区展示当前所有已提炼的洞察内容，并为每条洞察标注最后更新时间及来源 Industry_Input 的类型。
6. WHEN User 再次提交新的 Industry_Input，THE Industry_Classifier SHALL 读取 Industry_Intelligence 知识库中的现有内容作为上下文，识别新材料与已有洞察之间的冲突或补充关系，并在 Industry_Digest 中明确标注"新增"、"补充"或"与已有洞察存在冲突"。
7. THE Apex_Advisor SHALL 在 Industry_Intelligence 知识库中维护一个 Topic_Material 话题素材库，由 Industry_Classifier 在提炼洞察的同时自动生成适合人际沟通使用的话题条目，每条 Topic_Material 包含话题标题、核心观点和数据支撑。
8. THE Apex_Advisor SHALL 支持 User 在 Topic_Material 话题素材库中对话题条目进行手动编辑和删除。
9. THE Apex_Advisor SHALL 支持 User 对 Industry_Intelligence 知识库中的任意维度洞察内容进行手动编辑和删除。
10. IF User 提交的 Industry_Input 内容过短或信息量不足以提炼有效洞察，THEN THE Industry_Classifier SHALL 向 User 返回提示，说明当前输入信息不足，并建议补充更多背景信息后重新提交。
11. WHEN Prompt_Engine 生成涉及行业背景的 Prompt_Template，THE Prompt_Engine SHALL 将 Industry_Intelligence 知识库中的相关维度洞察注入 Prompt_Template，以支持行业分析、战略判断等场景。
12. THE Apex_Advisor SHALL 在 Industry_Intelligence 知识库页面按四个维度分区展示内容，其中"数据要点"维度支持结构化数据卡片展示（数值 + 来源 + 日期），而非纯文本。
13. THE Apex_Advisor SHALL 支持 User 为 Industry_Intelligence 的每个维度设置 Update_Reminder，可选更新频率为每周 / 每两周 / 每月；到期后系统推送 Contextual_Nudge 提醒 User 投喂新材料。
14. THE Apex_Advisor SHALL 在 Industry_Intelligence 知识库页面提供"实时洞察"区域：WHEN User 投喂新 Industry_Input 后，系统在该区域高亮展示本次新增或有冲突的洞察，持续 24 小时后自动归入对应常规分区。
15. THE Apex_Advisor SHALL 支持 User 在 Topic_Material 话题素材库中按话题标签筛选话题条目，话题条目以卡片列表形式展示。

---

### 需求 6：常态化 Prompt 模板生成（Prompt Engine）

**用户故事：** 作为职场人士，我希望系统根据我录入的四层信息自动生成适合具体场景的 AI Prompt 模板，以便我能直接使用或稍作修改后用于 AI 工具。

#### 验收标准

1. WHEN User 选择一个 Scene，THE Prompt_Engine SHALL 综合当前 User 的 Team_Profile、Company_Profile、Work_Profile 和 Industry_Profile 中的相关字段，生成对应的 Prompt_Template。
2. THE Apex_Advisor SHALL 提供以下预设 Scene 供 User 选择：沟通与汇报、会议与展示准备、材料组织与提案撰写、研究与调研实施、项目进度规划与管理。
3. WHEN Prompt_Engine 生成 Prompt_Template，THE Apex_Advisor SHALL 在 3 秒内将结果展示给 User。
4. THE Apex_Advisor SHALL 允许 User 在生成的 Prompt_Template 上进行自由文本编辑后再使用。
5. THE Apex_Advisor SHALL 允许 User 将编辑后的 Prompt_Template 保存为自定义模板，并支持后续复用。
6. IF User 选择某个 Scene 时对应层的 Profile 数据为空，THEN THE Prompt_Engine SHALL 生成包含占位符提示的 Prompt_Template，并提示 User 补充相关 Profile 信息。
7. WHEN User 的任意 Profile 数据发生更新，THE Apex_Advisor SHALL 标记与该 Profile 相关的已保存 Prompt_Template 为"需要刷新"状态。

---

### 需求 7：Taboo 雷区感知

**用户故事：** 作为职场人士，我希望系统在生成 Prompt 时自动规避上司的禁忌雷区，以便我的沟通内容更加安全得体。

#### 验收标准

1. WHEN Prompt_Engine 生成涉及特定团队成员的 Prompt_Template，THE Prompt_Engine SHALL 自动将该成员的 Taboo 信息注入 Prompt_Template 的约束条件中。
2. IF Team_Profile 中某团队成员的 Taboo 字段为空，THEN THE Prompt_Engine SHALL 在生成的 Prompt_Template 中添加提示，建议 User 补充该成员的禁忌雷区信息。

---

### 需求 8：数据持久化与跨设备同步

**用户故事：** 作为职场人士，我希望我的所有 Profile 数据能够持久保存并在多设备间同步，以便随时随地访问。

#### 验收标准

1. THE Apex_Advisor SHALL 在 User 每次保存操作后将数据持久化至云端存储。
2. WHEN User 在设备 A 上更新 Profile 数据后，在设备 B 上打开 Apex_Advisor，THE Apex_Advisor SHALL 在 5 秒内将设备 B 的数据同步至最新状态。
3. IF 网络连接不可用，THEN THE Apex_Advisor SHALL 允许 User 继续在本地查看和编辑 Profile 数据，并在网络恢复后自动同步。
4. IF 本地数据与云端数据存在冲突，THEN THE Apex_Advisor SHALL 向 User 展示冲突详情并提供"保留本地版本"或"使用云端版本"的选择。

---

### 需求 9：统一 Chatbox（全局 AI 对话入口）

**用户故事：** 作为职场人士，我希望在 App 的任意页面都能打开一个统一的 AI 对话入口，系统自动调取四层 Profile 中的相关背景信息作为上下文进行回答，并通过快捷指令快速触发常用操作，以便随时获得有针对性的 AI 建议。

#### 验收标准

1. THE Apex_Advisor SHALL 在 App 全局提供统一的 Chatbox 入口，用户可在任意页面打开。
2. WHEN User 在 Chatbox 中输入问题，THE Apex_Advisor SHALL 根据问题内容自动匹配相关的四层 Profile Layer，并将对应 Profile 数据作为上下文注入 AI 回答。
3. THE Apex_Advisor SHALL 支持 User 在 Chatbox 中手动勾选 Context_Tag（对应四层 Layer），以指定本次对话的背景信息调取范围；手动勾选优先级高于系统自动匹配。
4. WHEN Chatbox 生成 AI 回答，THE Apex_Advisor SHALL 采用双 Box 布局展示：左/主 Box 展示 AI 回答及本次调取的背景信息来源标注（来自哪一层 Profile 的哪个字段）；右/侧 Box 展示 AI 的思维过程与判断依据（Chain of Thought），右侧 Box 默认可折叠。
5. WHEN User 在 Chatbox 输入框中输入 `/`，THE Apex_Advisor SHALL 弹出快捷指令面板，展示可用的 Slash_Command 列表，至少包含：`/行为预判 @成员名`、`/评审草稿`、`/生成Prompt [Scene]`、`/行业洞察`。
6. WHEN User 选择或输入 Slash_Command，THE Apex_Advisor SHALL 根据指令类型自动调取对应 Profile 数据并执行相应操作，在 Chatbox 中展示结果。
7. THE Apex_Advisor SHALL 不保留跨会话的 Chatbox 对话历史；每次新开对话均为独立会话，但当前会话内保持完整上下文。
8. WHEN Chatbox 调取 Profile 背景信息时，THE Apex_Advisor SHALL 在回答的来源标注区域明确标注每条背景信息的来源层（Team / Company / Work / Industry）及具体字段名称。
9. IF 当前 User 的所有 Profile 均为空，THEN THE Apex_Advisor SHALL 在 Chatbox 中提示 User 尚未录入任何 Profile 信息，并引导 User 进入对应 Profile 录入页面。

---

### 需求 10：情境感知建议（Contextual Nudge）

**用户故事：** 作为职场人士，我希望系统在我打开特定页面时主动推送相关的情境提示，无需我主动询问，以便及时发现需要更新的信息或注意的事项。

#### 验收标准

1. WHEN User 打开某团队成员档案页，IF 距离上次更新该成员的行为标签已超过 14 天，THEN THE Apex_Advisor SHALL 推送 Contextual_Nudge 提示："上次与 [成员名] 的 1on1 已过去 N 天，建议更新行为标签"。
2. WHEN User 打开行业情报知识库页面，IF 行业数据距上次更新已超过 7 天，THEN THE Apex_Advisor SHALL 推送 Contextual_Nudge 提示："行业数据已 N 天未更新，建议投喂新材料"。
3. WHEN User 进入交付物评审流程，IF 当前 Work_Scene 下存在 Deliverable_Pattern 且有反复不符合的维度，THEN THE Apex_Advisor SHALL 推送 Contextual_Nudge 提示："你在 [Work_Scene] 下有 N 个维度反复不符合，本次注意：[维度名]"。
4. WHEN User 触发 Prompt 生成操作，IF Industry_Intelligence 知识库距上次更新已超过 10 天，THEN THE Apex_Advisor SHALL 推送 Contextual_Nudge 提示："公司情报知识库上次更新是 N 天前，生成结果可能不够准确"。
5. THE Apex_Advisor SHALL 以非阻断式方式展示 Contextual_Nudge（不弹窗），采用 banner 或 inline 卡片形式，不中断用户当前操作流程。
6. THE Apex_Advisor SHALL 支持 User 关闭单条 Contextual_Nudge 提示。
7. THE Apex_Advisor SHALL 支持 User 在设置中全局关闭某类型的 Contextual_Nudge（如关闭所有"行为标签更新"类提示）。
8. WHEN User 关闭某条 Contextual_Nudge 后，THE Apex_Advisor SHALL 不在同一会话内重复推送相同内容的提示。
