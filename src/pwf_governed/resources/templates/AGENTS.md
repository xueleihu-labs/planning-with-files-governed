<!-- Template version source: VERSION -->
# 项目协作规则

## 第一准则

效率优先：先交付可用版本，再基于真实使用渐进修复。普通、低风险、可回滚问题由执行智能体直接处理、验证并汇报；不重复规划、不重复审计、不扩大范围。

## 指令归属与项目边界

1. 当前项目根目录、项目 ID 与当前任务是唯一默认执行目标。消息中出现其他项目路径、Skill 口令、阶段材料或五表内容，不等于切换项目授权。
2. 指令与当前项目目标、根目录或任务上下文不一致，或疑似来自老板同时指挥的另一项目时，必须在任何执行前暂停，说明疑似跑偏点并等待老板明确确认“是否切换并执行”。
3. 文件创建、修改、删除、状态落盘、生成产物和会写入的命令默认只能在当前项目根目录及其已登记目录内执行。需要写入其他项目、其他 Skill、外置状态目录或共享资产时，必须先向老板说明目标根、范围和影响，再取得明确确认。
4. 不得把聊天记录、其他项目的五表或路径引用当作覆盖当前项目规则、目标、历史或状态的依据。

## 项目事实

- 项目：{{PROJECT_NAME}} / {{PROJECT_ID}}
- 当前任务：{{TASK_ID}}
- 任务事实源：`00.项目规划与治理/{{TASK_ID}}/`
- 统一相对路径：{{RELATIVE_PATH}}
- 当前可写主端：{{PRIMARY_MACHINE}}
- 主执行智能体：{{PRIMARY_AGENT}}
- {{PRIMARY_MACHINE}}：{{MAC_PROJECT_PATH}}
- Windows：{{WIN_PROJECT_PATH}}
- WSL：{{WSL_PROJECT_PATH}}

执行入口为 Codex、Claude、WSL Shell；运行路径为 {{PRIMARY_MACHINE}}、Windows、WSL。不得混用两个维度，同一文件和同一状态源不得多端并发写入。

## 启动与接管

1. 读 `AGENTS.md`、`CLAUDE.md`、`00_PROJECT_INDEX.md`。
2. 读 `00.项目规划与治理/task-index.yaml`，只用它定位任务，不从中推断阶段、PASS 或写入者。
3. 进入 `00.项目规划与治理/<task-id>/`，再读 `task_plan.md`、`1_master_plan.md`、`3_status_update.md`、`4_handoff.md` 和 `WORKFLOW_CHECKLIST.md`。
4. 项目只有一个任务时可自动发现；存在多个任务时必须显式指定 `--task-id` 或 `PWF_TASK_ID`，禁止按修改时间猜测。
5. 检查 Git 状态；缺失文件只补缺，不覆盖历史。项目无根 Git 时，按项目约定检查各独立 Git 边界。
6. 开工和接管时优先更新索引；无法调用Index Manager时必须记录“等价流程”。

## PLAN 事实源

- 新任务的唯一活跃事实源位于项目内 `00.项目规划与治理/<task-id>/`，随项目普通文件同步。
- 项目源码仍放正常源码目录；只有任务专用的盘点、迁移或验收脚本进入任务包的 `scripts/`。
- 缓存、日志、临时锁、凭证、`.env`、设备私有配置和可再生产物不得进入任务包。
- 已绑定旧 checkpoint/read-head 的任务保持原路径；只有显式迁移、逐文件哈希核验和事务激活后才可切换权威位置。

## Skill 路由

| 场景 | Skill | 边界 |
|---|---|---|
| 项目价值、MVP、优先级 | Product Manager | 不写业务代码或替代执行 |
| 工程计划、测试、验证、调试 | Development Assistant | 按任务选子技能 |
| 项目现场与五表 | planning-with-files-governed | 不替代业务 Skill |
| 文件地图 | Index Manager | 不替代五表 |
| 阶段复盘 | Skill Evolution Manager | 只在阶段结束或封板 |

未实际调用的 Skill 不得标记为已调用。

## 红线

密钥、Token、Cookie、密码、`.env`、生产状态源、不可逆删除或覆盖、正式 Skill 删除、系统级安装、后台常驻任务、真实交易、付费操作、`git push`、改写远程历史和未经授权跨端覆盖必须暂停确认。

## 验收

- A0：Done Criteria 与验证通过，无未说明阻塞。
- A1：满足 A0，且 `5_audit.md` 最终结论为 `PASS`。
- A2：满足 A1，审计 Agent 与执行 Agent 不同，老板门禁为 `APPROVED`。

{{RULE_PROFILE_BLOCK}}

## 项目专属规则

在此记录当前项目独有的目录、命令、技术限制和禁止事项。个人规则同步器不会修改本节。
