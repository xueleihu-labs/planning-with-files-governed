<!-- Template version source: VERSION -->
# 项目总入口与索引

<!-- INDEX-MANAGER:MANUAL-START -->
## 项目概况

- **项目名称**：{{PROJECT_NAME}}
- **项目 ID**：{{PROJECT_ID}}（来源：{{PROJECT_ID_SOURCE}}）
- **业务主线**：{{BUSINESS_LINE}}
- **项目目标**：待定义
- **当前状态**：地基已创建
- **当前阶段**：项目初始化
- **下一步动作**：完成产品价值判断和工程执行计划
- **主归属机器**：{{PRIMARY_MACHINE}}（来源：{{PRIMARY_MACHINE_SOURCE}}）
- **主执行智能体**：{{PRIMARY_AGENT}}（来源：{{PRIMARY_AGENT_SOURCE}}）
- **辅助智能体**：待登记
- **项目相对路径**：{{RELATIVE_PATH}}（来源：{{RELATIVE_PATH_SOURCE}}）
- **核心输入目录**：{{INPUT_DIR}}
- **核心输出目录**：{{OUTPUT_DIR}}
- **项目入口**：00_PROJECT_INDEX.md
- **验收标准**：见 1_master_plan.md 的 Done Criteria
- **最后更新时间**：{{TIMESTAMP}}

## 执行入口验证状态

| 入口 | 状态 | 证据 |
|---|---|---|
| Codex | 未验证 | 待对应端实际验证 |
| Claude | 未验证 | 待对应端实际验证 |
| WSL Shell | 未验证 | 待对应端实际验证 |

## 运行路径与映射状态

| 运行路径 | LOBSTER_ROOT | 项目绝对路径 | 状态 |
|---|---|---|---|
| {{PRIMARY_MACHINE}} | {{MAC_ROOT}} | {{MAC_PROJECT_PATH}} | 未验证 |
| Windows | {{WIN_ROOT}} | {{WIN_PROJECT_PATH}} | 未验证 |
| WSL | {{WSL_ROOT}} | {{WSL_PROJECT_PATH}} | 未完成 |

- **当前可写主端**：{{PRIMARY_MACHINE}}
- **辅助只读端**：待登记
- **路径映射验证状态**：未完成

规则：三端共用项目 ID 和统一相对路径；绝对路径由 `LOBSTER_ROOT + 项目相对路径` 推导。未登记路径不得猜测，未验证不得宣称通过。

## Skill 联动

| Skill | 状态 | 调用时间 | 调用入口 | 执行结果 | 证据或产物 |
|---|---|---|---|---|---|
| Index Manager | {{INDEX_STATUS}} | {{TIMESTAMP}} | 项目初始化 | {{INDEX_RESULT}} | {{INDEX_EVIDENCE}} |
| Skill Manager | 应考虑 | - | - | - | - |
| Product Manager | 未调用 | - | - | - | 产品价值判断待完成 |
| Development Assistant | 未调用 | - | - | - | 工程计划待确认 |
| planning-with-files-governed | 已调用 | {{TIMESTAMP}} | 项目初始化 | 成功 | 五张治理表 |
| Project Owner | 不适用 | - | - | - | 按复杂度启用 |
| orchestrator | 不适用 | - | - | - | 按多智能体编排启用 |
| Skill Evolution Manager | 应考虑 | - | - | - | 阶段结束或封板时执行 |

- **本项目必用 Skill**：planning-with-files-governed、Index Manager（或等价流程）
- **本项目候选 Skill**：Skill Manager、Product Manager、Development Assistant
- **是否可能沉淀为 Skill**：待观察
<!-- INDEX-MANAGER:MANUAL-END -->

{{RULE_PROFILE_STATUS}}

<!-- INDEX-MANAGER:AUTO-START -->
## 文件索引

由Index Manager或等价流程更新。
<!-- INDEX-MANAGER:AUTO-END -->
