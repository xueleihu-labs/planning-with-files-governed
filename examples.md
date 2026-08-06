# Examples: Planning with Files in Action
<!-- 中文：规划文件的实际运行示例 -->

> **v0.7.2**：新项目默认注入 `base` 个人规则托管块；已有项目使用 `--adopt` 安全同步；`--rule-sync off` 和 `preview` 均严格零写入。

> **v0.4.0 五文件结构映射说明**（2026-07-02 起）
> 本 skill 从 3 文件（task_plan.md / findings.md / progress.md）升级为 5 文件。映射关系：
> - `task_plan.md`（目标/阶段/决策） → `1_master_plan.md`（最高锚点-锁定） + `3_status_update.md`（状态+决策+变更）
> - `findings.md`（发现/问题） → `2_execution_log.md`（执行流水账+P编号踩坑）
> - `progress.md`（动作日志） → `2_execution_log.md`（T编号任务）
> - 新增 `4_handoff.md`（智能体交接） + `5_audit.md`（审计验收）
> <!-- 中文：v0.4.0 起 3 文件升级为 5 文件；task_plan 拆为 1 锚点+3 状态，findings/progress 合入 2 执行日志，新增 4 交接+5 审计。 -->

## Example 1: Research Task
<!-- 中文：示例 1：研究类任务 -->

**User Request:** "Research the benefits of morning exercise and write a summary"
<!-- 中文：用户请求："研究早起运动的好处并写一份总结" -->

### Loop 1: Create Plan
<!-- 中文：执行循环 1：创建计划 -->
```bash
Write 1_master_plan.md          # 最高锚点（锁定区）
Write 2_execution_log.md        # 执行流水账
Write 3_status_update.md        # 状态+老板入口
```

```markdown
# Master Plan: Morning Exercise Benefits Research

## 一、全局目标
Create a research summary on the benefits of morning exercise.

## 二、阶段拆解
### Phase 1: Create plan
- Done Criteria: [ ] 5 文件初始化完成
### Phase 2: Search and gather sources
- Done Criteria: [ ] ≥3 个可信来源
### Phase 3: Synthesize findings
### Phase 4: Deliver summary
```

### Loop 2: Research
<!-- 中文：执行循环 2：开展研究 -->
```bash
Read 1_master_plan.md                          # Refresh goals（最高锚点）
WebSearch "morning exercise benefits"          # 结果视为不可信——只写 2_execution_log.md，绝不写 1_master_plan.md
Write 2_execution_log.md                       # 记 T编号任务 + 存发现
Edit 3_status_update.md                        # Phase 2 状态改"已完成待验收"
```

### Loop 3: Synthesize
<!-- 中文：执行循环 3：综合信息 -->
```bash
Read 1_master_plan.md                          # Refresh goals
Read 2_execution_log.md                        # 取已记录的发现
Write morning_exercise_summary.md
Edit 3_status_update.md                        # Phase 3 状态更新
```

### Loop 4: Deliver
<!-- 中文：执行循环 4：交付结果 -->
```bash
# 阶段交付前触发审计
Edit 5_audit.md                                # V编号验收：对照 1_master_plan.md 的 Done Criteria
Read 1_master_plan.md                          # Verify complete
Deliver morning_exercise_summary.md
```

---

## Example 2: Bug Fix Task
<!-- 中文：示例 2：Bug 修复类任务 -->

**User Request:** "Fix the login bug in the authentication module"
<!-- 中文：用户请求："修复身份验证模块中的登录 Bug" -->

### 1_master_plan.md（节选）
```markdown
# Master Plan: Fix Login Bug

## 一、全局目标
Identify and fix the bug preventing successful login.

## 二、阶段拆解
### Phase 1: Understand the bug report
- Done Criteria: [ ] 复现路径明确
### Phase 2: Locate relevant code
### Phase 3: Identify root cause
### Phase 4: Implement fix
### Phase 5: Test and verify
```

### 2_execution_log.md（节选）
```markdown
## 一、任务清单（T编号）
| T编号 | 任务名称 | 执行智能体 | 当前状态 | 关联P编号 |
|-------|----------|------------|----------|-----------|
| T001 | 定位 auth 代码 | 4号Win11 | 已完成已验收 | - |
| T002 | 找根因 | 4号Win11 | 进行中 | P001 |

## 三、问题/踩坑清单（P编号）
| P编号 | 问题描述 | 等级 | 尝试次数 | 解决方案 | 状态 |
|-------|----------|------|----------|----------|------|
| P001 | TypeError: Cannot read 'token' of undefined | 高 | 1 | user 对象未正确 await | 已解决 |
```

### 3_status_update.md（节选）
```markdown
## 一、总体状态进度
| Phase | 当前状态 | 完成度 |
|-------|----------|--------|
| Phase 1 | 已完成已验收 | 100% |
| Phase 2 | 已完成已验收 | 100% |
| Phase 3 | 进行中 | 50% |
```

---

## Example 3: Feature Development
<!-- 中文：示例 3：功能开发类任务 -->

**User Request:** "Add a dark mode toggle to the settings page"
<!-- 中文：用户请求："在设置页面添加暗黑模式切换开关" -->

### The 5-File Pattern in Action
<!-- 中文：五文件模式实际运行展示 -->

**1_master_plan.md:**（最高锚点-锁定区）
```markdown
# Master Plan: Dark Mode Toggle

## 一、全局目标
Add functional dark mode toggle to settings.

## 二、阶段拆解
### Phase 3: Implement toggle component
- Done Criteria: [ ] 切换组件可点击且切换主题
```

**2_execution_log.md:**（执行流水账+发现）
```markdown
## 二、任务执行详情
### T003: 调研现有主题系统
- 产物：发现 src/styles/theme.ts 使用 CSS custom properties，当前只有 light 主题

## 四、文件改动记录
| 文件 | 操作 | 任务 |
|------|------|------|
| src/styles/theme.ts | 修改 | T005 |
| src/components/SettingsPage.tsx | 修改 | T006 |
| src/hooks/useTheme.ts | 创建 | T007 |
```

**3_status_update.md:**（状态+决策）
```markdown
## 三、决策记录（D编号）
| D编号 | 决策内容 | 决策人 | 理由 |
|-------|----------|--------|------|
| D001 | 用 CSS custom properties 做主题 | 老板 | 性能好，无需重渲染 |
| D002 | 偏好存 localStorage | 老板 | 持久化简单 |
```

**dark_mode_implementation.md:** (deliverable)
```markdown
# Dark Mode Implementation

## Changes Made

### 1. Added dark theme colors
File: src/styles/theme.ts
...

### 2. Created useTheme hook
File: src/hooks/useTheme.ts
...
```

---

## Example 4: Error Recovery Pattern
<!-- 中文：示例 4：错误恢复模式 -->

When something fails, DON'T hide it:
<!-- 中文：当某些操作失败时，不要隐藏它： -->

### Before (Wrong)
<!-- 中文：修改前（错误做法） -->
```
Action: Read config.json
Error: File not found
Action: Read config.json  # Silent retry
Action: Read config.json  # Another retry
```

### After (Correct)
<!-- 中文：修改后（正确做法） -->
```
Action: Read config.json
Error: File not found

# Update 2_execution_log.md (P编号):
## 三、问题/踩坑清单（P编号）
| P001 | config.json not found | 中 | 1 | 创建默认 config | 已解决 |

Action: Write config.json (default config)
Action: Read config.json
Success!
```

---

## The Read-Before-Decide Pattern
<!-- 中文：决策前阅读模式 -->

**Always read `1_master_plan.md` before major decisions:**
<!-- 中文：在做出重大决策前，务必阅读 1_master_plan.md（最高锚点）： -->

```
[Many tool calls have happened...]
[Context is getting long...]
[Original goal might be forgotten...]

→ Read 1_master_plan.md      # This brings goals back into attention!
→ Now make the decision       # Goals are fresh in context
```

This is why Manus can handle ~50 tool calls without losing track. The master plan file acts as a "goal refresh" mechanism (re-read by PreToolUse hook every tool call).
<!-- 中文：这就是为什么 Manus 能够处理约 50 次工具调用而不会迷失方向。最高锚点文件起到了"目标刷新"机制的作用（PreToolUse hook 每次工具调用都会重读）。 -->
