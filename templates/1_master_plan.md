<!-- Template version source: VERSION -->
# Master Plan: {{PROJECT_NAME}}

> 锁定目标、MVP、边界和验收；日常流水进入 `2_execution_log.md`。

## North Star and MVP

- **项目 ID**：{{PROJECT_ID}}
- **North Star goal**：待定义
- **MVP 第一刀**：待定义
- **项目相对路径**：{{RELATIVE_PATH}}
- **主归属机器**：{{PRIMARY_MACHINE}}
- **主执行智能体**：{{PRIMARY_AGENT}}

## 阶段与 Done Criteria

| Phase | 阶段目标 | Done Criteria | 状态 |
|---|---|---|---|
| Phase 1 | 需求与地基 | 项目目标、边界、路径和 Skill 计划明确 | 进行中 |
| Phase 2 | MVP 实施 | 可用版本可运行 | 待开始 |
| Phase 3 | 验证与交付 | 验收完成 | 待开始 |

- **Done Criteria Status**: PENDING
- **Validation Status**: PENDING
- **Unresolved Blockers**: NONE

## 范围与禁止扩展

- **修改范围**：待定义
- **禁止扩展范围**：不扩大项目范围，不顺手重构无关模块。
- **红线**：密钥、生产状态源、不可逆覆盖、正式 Skill 删除、远程 Git 写入和未经授权跨端覆盖。

## 三套运行路径

| 路径 | 项目绝对路径 | 状态 |
|---|---|---|
| {{PRIMARY_MACHINE}} | {{MAC_PROJECT_PATH}} | 未验证 |
| Windows | {{WIN_PROJECT_PATH}} | 未验证 |
| WSL | {{WSL_PROJECT_PATH}} | 未完成 |

## Skill 与验证计划

- **必用 Skill**：planning-with-files-governed、Index Manager（或等价流程）
- **候选 Skill**：Product Manager、Development Assistant、Skill Manager
- **验证方案**：按审计等级执行验证命令并记录结果。
