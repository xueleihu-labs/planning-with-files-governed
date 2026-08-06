# Reference: Manus Context Engineering Principles
<!-- 中文：参考：Manus 上下文工程原则 -->

This skill is based on context engineering principles from Manus, the AI agent company acquired by Meta for $2 billion in December 2025.
<!-- 中文：本技能基于 Manus 的上下文工程原则。Manus 是一家 AI Agent 公司，2025 年 12 月被 Meta 以 20 亿美元收购。 -->

## The 6 Manus Principles
<!-- 中文：Manus 的 6 大原则 -->

### Principle 1: Design Around KV-Cache
<!-- 中文：原则 1：围绕 KV-Cache（键值缓存）进行设计 -->

> "KV-cache hit rate is THE single most important metric for production AI agents."
<!-- 中文：“对于生产环境的 AI Agent 来说，KV-cache 命中率是唯一最重要的指标。” -->

**Statistics:**
- ~100:1 input-to-output token ratio
- Cached tokens: $0.30/MTok vs Uncached: $3/MTok
- 10x cost difference!
<!-- 中文：统计数据：输入输出 token 比约为 100:1；缓存 token 价格只有非缓存的十分之一。 -->

**Implementation:**
- Keep prompt prefixes STABLE (single-token change invalidates cache)
- NO timestamps in system prompts
- Make context APPEND-ONLY with deterministic serialization
<!-- 中文：实现方式：保持提示词前缀稳定；系统提示词中不加时间戳；让上下文保持“仅追加”模式并使用确定性的序列化方式。 -->

### Principle 2: Mask, Don't Remove
<!-- 中文：原则 2：屏蔽，而非移除 -->

Don't dynamically remove tools (breaks KV-cache). Use logit masking instead.
<!-- 中文：不要动态移除工具（这会破坏 KV-cache），而应使用 Logit 屏蔽。 -->

**Best Practice:** Use consistent action prefixes (e.g., `browser_`, `shell_`, `file_`) for easier masking.
<!-- 中文：最佳实践：使用一致的动作前缀（如 `browser_`、`shell_`、`file_`）以便于屏蔽。 -->


### Principle 3: Filesystem as External Memory
<!-- 中文：原则 3：文件系统即外部记忆 -->

> "Markdown is my 'working memory' on disk."
<!-- 中文：“Markdown 是我落在磁盘上的‘工作记忆’。” -->

**The Formula:**
```
Context Window = RAM (volatile, limited)
Filesystem = Disk (persistent, unlimited)
```
<!-- 中文：公式：上下文窗口 = 内存（易失、有限）；文件系统 = 磁盘（持久、无限）。 -->

**Compression Must Be Restorable:**
- Keep URLs even if web content is dropped
- Keep file paths when dropping document contents
- Never lose the pointer to full data
<!-- 中文：压缩必须是可还原的：即使丢弃网页内容也要保留 URL；即使丢弃文档内容也要保留文件路径；永远不要丢失指向原始完整数据的指针。 -->

### Principle 4: Manipulate Attention Through Recitation
<!-- 中文：原则 4：通过“背诵”来操纵注意力 -->

> "Creates and updates todo.md throughout tasks to push global plan into model's recent attention span."
<!-- 中文："在整个任务过程中创建并更新 todo.md（本 skill 中对应 `1_master_plan.md`），将全局计划推送到模型的近期注意力跨度中。" -->

**Problem:** After ~50 tool calls, models forget original goals ("lost in the middle" effect).
<!-- 中文：问题：在约 50 次工具调用后，模型会忘记原始目标（即"迷失在中间"效应）。 -->

**Solution:** Re-read `1_master_plan.md` before each decision. Goals appear in the attention window.
<!-- 中文：解决方案：在每次决策前重读 `1_master_plan.md`（最高锚点），使目标出现在当前注意力窗口中。 -->

```
Start of context: [Original goal - far away, forgotten]
...many tool calls...
End of context: [Recently read 1_master_plan.md - gets ATTENTION!]
```
<!-- 中文：上下文开头：原始目标（太远，被遗忘）；中间：大量的工具调用；上下文末尾：最近阅读的 `1_master_plan.md`（获得注意力！）。 -->


### Principle 5: Keep the Wrong Stuff In
<!-- 中文：原则 5：保留错误的内容 -->

> "Leave the wrong turns in the context."
<!-- 中文：“把走过的弯路留在上下文中。” -->

**Why:**
- Failed actions with stack traces let model implicitly update beliefs
- Reduces mistake repetition
- Error recovery is "one of the clearest signals of TRUE agentic behavior"
<!-- 中文：原因：带有堆栈追踪的失败动作能让模型隐式更新信念；减少错误重复；错误恢复是“真正代理行为（Agentic behavior）的最清晰信号之一”。 -->

### Principle 6: Don't Get Few-Shotted
<!-- 中文：原则 6：不要被少量示例带偏 -->

> "Uniformity breeds fragility."
<!-- 中文：“统一性滋生脆弱性。” -->

**Problem:** Repetitive action-observation pairs cause drift and hallucination.
<!-- 中文：问题：重复的“动作-观察”对会导致模型漂移和幻觉。 -->

**Solution:** Introduce controlled variation:
- Vary phrasings slightly
- Don't copy-paste patterns blindly
- Recalibrate on repetitive tasks
<!-- 中文：解决方案：引入受控的变化，例如稍微改变表述方式；不要盲目复制模式；在处理重复性任务时进行重新校准。 -->


---

## The 3 Context Engineering Strategies
<!-- 中文：3 大上下文工程策略 -->

Based on Lance Martin's analysis of Manus architecture.
<!-- 中文：基于 Lance Martin 对 Manus 架构的分析。 -->

### Strategy 1: Context Reduction
<!-- 中文：策略 1：上下文缩减 -->

**Compaction:**
<!-- 中文：压实（Compaction）： -->
```
Tool calls have TWO representations:
├── FULL: Raw tool content (stored in filesystem)
└── COMPACT: Reference/file path only

RULES:
- Apply compaction to STALE (older) tool results
- Keep RECENT results FULL (to guide next decision)
```
<!-- 中文：工具调用有两种表现形式：1. 完整形式：原始工具内容（存放在文件系统）；2. 压实形式：仅保留引用/文件路径。规则是对陈旧结果进行压实，对近期结果保留完整内容。 -->

**Summarization:**
<!-- 中文：摘要化（Summarization）： -->
- Applied when compaction reaches diminishing returns
- Generated using full tool results
- Creates standardized summary objects
<!-- 中文：当压实收益递减时应用；基于完整结果生成；创建标准化的摘要对象。 -->

### Strategy 2: Context Isolation (Multi-Agent)
<!-- 中文：策略 2：上下文隔离（多代理） -->

**Architecture:**
<!-- 中文：架构图： -->
```
┌─────────────────────────────────┐
│         PLANNER AGENT           │
│  └─ Assigns tasks to sub-agents │
├─────────────────────────────────┤
│       KNOWLEDGE MANAGER         │
│  └─ Reviews conversations       │
│  └─ Determines filesystem store │
├─────────────────────────────────┤
│      EXECUTOR SUB-AGENTS        │
│  └─ Perform assigned tasks      │
│  └─ Have own context windows    │
└─────────────────────────────────┘
```
<!-- 中文：顶层是规划代理（分配任务）；中间是知识经理（审查对话、管理存档）；底层是执行代理（执行任务，拥有独立的上下文窗口）。 -->

**Key Insight:** Manus originally used `todo.md` for task planning but found ~33% of actions were spent updating it. Shifted to dedicated planner agent calling executor sub-agents.
<!-- 中文：核心见解：Manus 最初使用 `todo.md` 进行规划，但发现约 33% 的动作都浪费在更新它上面了。因此转向由专门的规划代理调用执行代理的模式。 -->


### Strategy 3: Context Offloading
<!-- 中文：策略 3：上下文卸载 -->

**Tool Design:**
<!-- 中文：工具设计： -->
- Use <20 atomic functions total
- Store full results in filesystem, not context
- Use `glob` and `grep` for searching
- Progressive disclosure: load information only as needed
<!-- 中文：总共使用少于 20 个原子功能；将完整结果存入文件系统而非上下文；使用 `glob` 和 `grep` 进行搜索；渐进式披露：仅在需要时加载信息。 -->

---

## The Agent Loop
<!-- 中文：代理循环 -->

Manus operates in a continuous 7-step loop:
<!-- 中文：Manus 在一个持续的 7 步循环中运行： -->

```
┌─────────────────────────────────────────┐
│  1. ANALYZE CONTEXT                      │
│     - Understand user intent             │
│     - Assess current state               │
│     - Review recent observations         │
├─────────────────────────────────────────┤
│  2. THINK                                │
│     - Should I update the plan?          │
│     - What's the next logical action?    │
│     - Are there blockers?                │
├─────────────────────────────────────────┤
│  3. SELECT TOOL                          │
│     - Choose ONE tool                    │
│     - Ensure parameters available        │
├─────────────────────────────────────────┤
│  4. EXECUTE ACTION                       │
│     - Tool runs in sandbox               │
├─────────────────────────────────────────┤
│  5. RECEIVE OBSERVATION                  │
│     - Result appended to context         │
├─────────────────────────────────────────┤
│  6. ITERATE                              │
│     - Return to step 1                   │
│     - Continue until complete            │
├─────────────────────────────────────────┤
│  7. DELIVER OUTCOME                      │
│     - Send results to user               │
│     - Attach all relevant files          │
└─────────────────────────────────────────┘
```
<!-- 中文：1. 分析上下文；2. 思考（是否更新计划、下一步动作）；3. 选择工具；4. 执行动作；5. 接收观察结果；6. 迭代；7. 交付结果。 -->


---

## File Types planning-with-files Creates
<!-- 中文：planning-with-files 创建的文件类型（v0.4.0 五文件结构） -->

| File | Purpose | When Created | When Updated |
|------|---------|--------------|--------------|
| `1_master_plan.md` | 最高锚点：目标+阶段+Done Criteria+边界（锁定区） | 任务开始 | 写定后只读，变更需升版本号 |
| `2_execution_log.md` | 执行流水账：T编号任务+P编号踩坑+文件改动 | 任务开始 | 执行全程持续追加 |
| `3_status_update.md` | 状态对比+老板入口+D决策+C变更 | 任务开始 | 阶段切换/决策/变更即更新 |
| `4_handoff.md` | 智能体交接+身份登记+接班指引 | 任务开始 | 截断/换人前必写 |
| `5_audit.md` | 审计验收+R风险+V多轮复审 | 阶段交付前 | 审计/复审/风险即记 |
| Code files | Implementation | Before execution | After errors |
<!-- 中文：上表说明了 5 个 planning 文件和代码文件的职责及更新时机；1 是锁定锚点，2 是流水账，3 是状态+老板入口，4 是交接，5 是审计。 -->

---

## Critical Constraints
<!-- 中文：关键约束 -->

- **Single-Action Execution:** ONE tool call per turn. No parallel execution.
<!-- 中文：单动作执行：每轮仅一次工具调用，禁止并行。 -->
- **Plan is Required:** Agent must ALWAYS know: goal, current phase, remaining phases
<!-- 中文：必须有计划：代理必须时刻清楚目标、当前阶段和剩余阶段。 -->
- **Files are Memory:** Context = volatile. Filesystem = persistent.
<!-- 中文：文件即记忆：上下文是易失的，文件系统是持久的。 -->
- **Never Repeat Failures:** If action failed, next action MUST be different
<!-- 中文：永不重复失败：如果动作失败，下一步动作必须做出改变。 -->
- **Communication is a Tool:** Message types: `info` (progress), `ask` (blocking), `result` (terminal)
<!-- 中文：沟通也是一种工具：消息类型分为进度信息、阻塞询问和最终结果。 -->


---

## Manus Statistics
<!-- 中文：Manus 统计数据 -->

| Metric | Value |
|--------|-------|
| Average tool calls per task | ~50 |
| Input-to-output token ratio | 100:1 |
| Acquisition price | $2 billion |
| Time to $100M revenue | 8 months |
| Framework refactors since launch | 5 times |
<!-- 中文：各项数据展示了 Manus 的规模和业务增长速度。 -->

---

## Key Quotes
<!-- 中文：核心语录 -->

> "Context window = RAM (volatile, limited). Filesystem = Disk (persistent, unlimited). Anything important gets written to disk."
<!-- 中文：“上下文窗口 = 内存；文件系统 = 磁盘。任何重要的东西都要落盘。” -->

> "if action_failed: next_action != same_action. Track what you tried. Mutate the approach."
<!-- 中文：“如果动作失败，下一步动作就不能相同。追踪尝试记录，改变方法。” -->

> "Error recovery is one of the clearest signals of TRUE agentic behavior."
<!-- 中文：“错误恢复是真正代理行为的最清晰信号之一。” -->

> "KV-cache hit rate is the single most important metric for a production-stage AI agent."
<!-- 中文：“对于生产阶段的 AI Agent 来说，KV-cache 命中率是唯一最重要的指标。” -->

> "Leave the wrong turns in the context."
<!-- 中文：“把走过的弯路留在上下文中。” -->


---

## Source
<!-- 中文：来源 -->

Based on Manus's official context engineering documentation:
https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
<!-- 中文：基于 Manus 官方上下文工程文档。 -->

