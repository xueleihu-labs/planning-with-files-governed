# PLAN 统一规划与协同体系正式架构标准

```text
PLAN_STANDARD_VERSION: 1.0.0
OWNER: planning-with-files
STATUS: FROZEN
```

## 1. 定位与总原则

PLAN 是复杂任务的唯一规划、状态和验收契约。它把老板目标转换为可执行计划，再把执行事实、验证结果和知识交接收回同一条可追溯链。

核心原则：

- 一主多接入：`planning-with-files` 持有 PLAN 标准、机器契约和项目运行实例。
- 组装非造新：优先复用已有 Skill、脚本、事件和回执，不建立平行大系统。
- 一个阶段一个闸门：当前阶段未验收，不进入下一阶段。
- 一个事实源：任务状态、计划版本、验收证据和推荐下一步只能有一个权威来源。
- 读写分离：读取、预览和审计可以并行；同一状态源的实际写入必须单写者、加锁、校验摘要并原子提交。
- 证据优先：没有真实产物、验证结果或回执，不宣称完成。
- 默认最小权限、最小上下文、最小变更和可回滚。

PLAN 标准由本文件持有；联动 Skill 只声明能力、输入输出、事件订阅、回执和引用，不复制完整 PLAN。

## 2. 一主多接入职责边界

| 组件 | 唯一职责 | 不得越权 |
|---|---|---|
| Project Owner | 目标、价值、风险、阶段和老板闸门 | 不写业务代码、不替代执行 Skill |
| `orchestrator` | 运行时调度、角色路由、DAG 投影和阶段推进 | 不维护第二份 PLAN，不绕过可信阶段状态 |
| `planning-with-files` | PLAN 标准、机器契约、任务清单、版本、证据和运行实例 | 不替代业务执行或上游价值裁决 |
| `phase-checkpoint-loop` | 外置检查点、可信恢复链和 CAS 发布 | 不替代 PLAN，不修改业务状态 |
| 同级治理 Skill | 自检、补丁、审计、清理、索引和进化建议 | 不覆盖 PLAN 权威，不静默推进阶段 |
| 下游执行 Skill | 在授权范围内完成具体业务动作 | 不改写上游目标、正式模板或其他项目资产 |
| `phase-evolution-bridge` | 将真实差异转换为进化候选或下一阶段上下文 | 不直接修改 Skill、模板、Hook 或 checkpoint |
| external-publishing-system | 唯一保存传播正文和发布资产 | 来源项目不创建第二知识库或成稿区 |

## 3. PLAN 三层结构

### 3.1 上游治理层

负责回答“为什么做、做什么、何时停”：

- 目标、价值、范围、风险和人工闸门。
- 阶段顺序、优先级、资源和冲突裁决。
- 暂停、恢复、关闭和最终批准。

### 3.2 PLAN 主核层

负责回答“按什么契约做、如何验证”：

- `TaskEnvelope`、`PlanPackage`、`ExecutionPacket`。
- 任务依赖、阶段、完成条件、证据要求和版本。
- 机器可读状态、推荐下一步、锁、摘要、原子写入和冲突报告。

### 3.3 执行与证据层

负责回答“实际做了什么、结果是什么”：

- 下游执行产物、测试输出、审计记录、交接回执。
- checkpoint 可信恢复点和阶段结论。
- 知识交接、进化候选和最终归档。

同级治理 Skill 位于主核与执行层之间，以事件订阅和执行回执协作，不拥有第二份 PLAN。

## 4. 标准数据包

### 4.1 TaskEnvelope

`TaskEnvelope` 是任务进入 PLAN 主核的最小输入：

```json
{
  "task_id": "项目内唯一任务 ID",
  "project_id": "项目 ID",
  "objective": "可验证目标",
  "scope": {"include": [], "exclude": []},
  "phase_id": "当前阶段",
  "priority": "P0/P1/P2/P3",
  "risk_level": "LOW/MEDIUM/HIGH/CRITICAL",
  "owner": "主责智能体",
  "inputs": [],
  "dependencies": [],
  "done_criteria": [],
  "verification_commands": [],
  "write_scope": [],
  "rollback_plan": "失败恢复方式",
  "knowledge_policy": "NO_HANDOFF/PENDING_INGEST/HANDOFF"
}
```

输入缺少目标、范围、验收或写入边界时，不得直接进入执行。

### 4.2 PlanPackage

`PlanPackage` 是冻结后的结构化计划，至少包含：

- 计划 ID、版本、来源任务和基线摘要。
- 阶段列表、依赖关系、阶段完成条件和人工闸门。
- 每个阶段的输入、产物、验证命令、回滚方式和主责。
- 事件订阅、执行回执、知识传播策略和下一阶段入口。
- 变更原因、影响范围和兼容性结论。

计划冻结后，普通执行者只能消费，不得静默改变目标、阶段或验收。

### 4.3 ExecutionPacket

`ExecutionPacket` 是发给执行 Skill 的最小授权包，至少包含：

- `task_id`、`phase_id`、`packet_id` 和基线摘要。
- 允许读取的输入引用、允许写入的路径和禁止触碰范围。
- 预期产物、完成证据、验证命令、超时和失败关闭动作。
- 回传格式、执行者、工具版本和结果摘要。

执行 Skill 不接收无关历史全文，不凭自然语言扩大写入范围。

## 5. 能力注册与输入输出契约

能力声明的最小字段为：

```text
capability_id
owner_skill
capability_version
trigger
input_contract
output_contract
subscribed_events
emitted_events
write_scope
permission_level
receipt_schema
failure_close
rollback
```

`SKILL_INDEX.md` 和索引管理员负责发现与登记；能力的实际输入输出契约仍由能力所属 Skill 和本标准共同定义。F0 阶段不新建独立能力总表。

## 6. 标准事件

事件是确定性事实通知，不是第二套状态系统。首期标准事件为：

```text
TASK_ACCEPTED
PLAN_PROFILE_SELECTED
PLAN_FROZEN
PHASE_STARTED
PHASE_COMPLETED
VERIFY_FAILED
VERIFY_PASSED
PATCH_REQUIRED
USER_GATE_REQUIRED
KNOWLEDGE_CANDIDATE_READY
KNOWLEDGE_INGESTED
EVOLUTION_CANDIDATE_READY
TASK_CLOSED
```

每个事件至少带有 `event_id`、`event_type`、`task_id`、`phase_id`、`occurred_at`、`source`、`payload_digest` 和 `evidence_refs`。事件只引用事实，不复制大型产物全文。

## 7. 执行回执

执行回执至少说明：

- 执行包 ID、任务和阶段。
- `SUCCEEDED`、`FAILED`、`PAUSED`、`BLOCKED` 或 `CANCELLED`。
- 实际产物路径、摘要和验证结果。
- 未完成项、阻塞原因和推荐下一步。
- 是否产生知识交接或进化信号。

成功回执必须有证据引用；失败回执必须有失败原因；暂停回执不得伪装为完成。

## 8. 冲突裁决顺序

发生目标、状态、路径、版本或证据冲突时，按以下顺序裁决：

1. 安全红线、敏感数据和不可逆操作保护。
2. 老板最新明确指令和人工闸门。
3. 可信 checkpoint head、已发布提交和基线摘要。
4. PLAN Schema、版本和机器契约。
5. 阶段依赖、任务完成条件和锁状态。
6. 最新可验证产物、测试和审计证据。
7. 下游建议、历史说明和自然语言推断。

无法裁决时保持原状态，生成冲突报告并等待人工处理，不覆盖已有进度。

## 9. 知识交接边界

来源项目只生成临时、最小化的 `knowledge_handoff.json`，内容包括：

- 来源项目、任务、阶段和证据引用。
- 摘要、价值判断、适用范围和隐私过滤结果。
- 目标入口、交接状态和失败原因。

交接规则：

- 公众号、小红书和百家号正文只由中央发布系统保存。
- 来源项目只保留中央引用和接收回执。
- 写入失败标记 `PENDING_INGEST`，不阻塞技术任务关闭。
- 不复制密钥、Token、`.env`、大型产物或无关个人信息。
- 不在来源项目创建第二知识库或内容沉淀区。

## 10. 可靠性、安全与恢复

- 所有摘要使用 UTF-8、无 BOM、LF、稳定键序和单一末尾换行。
- JSON 写入采用临时文件、校验后原子替换。
- 实际状态写入必须加锁、核对 base digest、保留未知字段并支持冲突报告。
- 外置 checkpoint 由 `phase-checkpoint-loop` 的可信链负责；运行态只能作为投影。
- 恢复只能读取可信 head、commit、result 和基线，不使用聊天记录替代机器状态。
- 路径必须限制在授权根内；敏感文件、生产环境和高权限配置默认拒绝。
- 所有执行包声明最小读取、写入和外部调用权限。
- 失败关闭优先于猜测、覆盖和自动迁移。

## 11. Token 节省规则

- 先读 `00_PROJECT_INDEX.md` 和必要入口，再读原文。
- 上游只接收摘要、引用和机器状态，不接收完整历史。
- 执行包只带当前任务必需字段。
- 大型证据只保存路径和摘要。
- 重复输入使用稳定摘要、引用和幂等键。
- 无新增事实时返回已有结论，不重复生成完整交接包。

## 12. F0–F6 实施路线

| 阶段 | 目标 | 当前边界 |
|---|---|---|
| F0 | 盘点现有能力、重复能力和缺口 | 只读盘点，不开发新系统 |
| F1 | 建立 PLAN 最小可用主核 | `TaskEnvelope`、`PlanPackage`、`ExecutionPacket` |
| F2 | 接入Project上游 | 发起、暂停、恢复、关闭和状态回传 |
| F3 | 同级 Skill 逐项接入 | 一次只接入一个，完成真实调用和回滚测试 |
| F4 | 连接中央发布系统 | 最小知识交接，不复制内容正文 |
| F5 | 真实端到端任务验收 | 形成完整执行、验证和交接证据 |
| F6 | 推广统一标准 | 新任务默认采用，旧项目按需兼容升级 |

前一阶段未验收，不得进入后一阶段；不得批量迁移旧项目。

## 13. 最低可用验收标准

至少证明：

1. 一个真实任务能生成结构化、可执行、可验证的 PLAN。
2. 上游不维护第二份 PLAN。
3. 执行 Skill 能收到最小授权包并返回结构化回执。
4. 阶段可以暂停、恢复、失败关闭并读取可信状态。
5. 任务完成有真实证据，失败不会伪装成功。
6. 知识交接失败不破坏技术项目闭环。
7. 正式资产、模板、注册表和候选生命周期有独立批准门。
8. 相同输入重复执行不会产生重复状态或重复候选。

## 14. 明确禁止方向

- 不建立第二套 PLAN、任务状态、事件总线或知识库。
- 不把五表、聊天记录或自然语言报告当作唯一机器权威。
- 不复制完整 PLAN 到每个联动 Skill。
- 不调用外部模型替代确定性契约验证。
- 不自动批准、自动晋升或自动迁移正式模板。
- 不批量接入所有 Skill，不新增大量业务模板。
- 不在来源项目生成传播平台成稿。
- 不绕过老板闸门、checkpoint、索引、审计或安全红线。

## 15. 完整闭环

```text
Project发起目标
→ TaskEnvelope
→ PLAN 选择与冻结
→ ExecutionPacket
→ 下游执行 Skill
→ 执行回执与证据
→ checkpoint / 自检 / 审计
→ knowledge_handoff
→ 中央发布系统接收回执
→ phase-evolution-bridge 只读提案
→ 人工批准与受控晋升
→ 任务关闭与可恢复归档
```

本标准只定义边界和契约；具体实现按 F0–F6 分阶段落地。
