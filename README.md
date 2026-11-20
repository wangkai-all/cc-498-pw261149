# 客服工单主题分析系统

支持**词表、规则、模型**三种分类器版本的客服工单主题分析平台，覆盖主题漂移检测、未知主题识别、
多标签工单、历史重分类双结果保留、主题合并/拆分的报表与告警级联更新，以及基于角色的权限导出。

## 技术栈

- 后端：FastAPI + SQLAlchemy 2 + SQLite（零外部依赖，可替换为 PostgreSQL）
- 前端：原生 HTML/CSS/JS + 内联 SVG，无构建步骤
- 测试：pytest + FastAPI TestClient（29 个用例）

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 1. 初始化演示数据（主题、词表/规则/模型版本、13 条工单、告警规则）
.venv/bin/python -m backend.scripts.seed

# 2. 启动服务
.venv/bin/uvicorn backend.app.main:app --port 8077
# 打开 http://127.0.0.1:8077/
```

## 测试

```bash
.venv/bin/python -m pytest backend/tests -q
```

## 核心能力与设计说明

### 1. 三种分类器版本（`backend/app/classifiers.py`）

| 类型 | 机制 | 证据 |
|---|---|---|
| `keyword` 词表 | 主题词表命中计分，多命中加分，支持权重 | 命中词列表与分数 |
| `rule` 规则 | `all/any/not` 布尔表达式，支持规则命名 | 命中规则与命中项 |
| `model` 模型 | 多标签朴素贝叶斯（中英文分词 + softmax 概率） | Top 特征词与概率 |

- 同一时间只有一个**激活版本**，版本不可变；切换激活版本会产生告警。
- 所有引擎统一输出：多标签 `labels`、逐标签 `scores`、`confidence`、`evidence`、`is_unknown`。
- 低于阈值但有候选的工单判为**未知主题**，候选分数保留并进入未知样本队列。

### 2. 主题漂移与未知主题（`services/reports.py`、`services/alerts.py`）

- 按时间窗口计算主题分布，用 **Jensen-Shannon 散度**量化相邻窗口漂移（归一化 0~1）。
- 占比变化超过阈值的主题列入 `drifted_topics`；漂移快照持久化，可回溯。
- 告警规则支持 `drift_spike` / `unknown_rate` / `topic_volume` / 版本切换四类，评估幂等。

### 3. 多标签工单

一条分类结果的 `labels` 为数组，词表/规则/模型均可能同时命中多个主题；
趋势与分布按标签分别计数。

### 4. 历史重分类保留双结果（`services/versions.py`）

- 重分类**始终追加新行**（`source=reclass`，关联 `job_id`），旧分类行不删除不覆盖。
- `ReclassificationJob.diffs` 逐条记录 `original_labels → new_labels`，并统计变化数与未知解决数。
- 在线**迟到更新**走另一条路径：同版本覆盖在线旧行，避免历史膨胀（24h 窗口外才标记迟到）。
- 版本差异接口 `/api/versions/diff` 对比两个版本在同一批工单上的结果。

### 5. 主题合并/拆分的依赖级联（`services/topics.py`）

- 血缘记录在 `TopicLineage`（merge/split + 源/目标主题 + 原因），旧主题归档不删除。
- 分类结果的 `topic_id` 与字符串标签同步迁移到新主题。
- **告警依赖**：`AlertRule.topic_ids` 自动重映射到新主题并去重。
- **报表依赖**：全部 `ReportSnapshot` 按新口径重建，`lineage_applied` 记录应用过的血缘。
- 拆分无法自动判定单条工单归属，统一迁入首个目标主题，再通过人工纠正细化。

### 6. 人工纠正闭环（`services/correction.py`、`services/training.py`）

- 纠正以 `source=manual` 的新分类行写入，机器结果保留；工单标记 `is_corrected`。
- 纠正样本可通过 `/api/training-samples` 导出，或直接调用 `/api/versions/train`
  训练新的朴素贝叶斯模型版本，形成「识别 → 纠正 → 训练 → 再识别」闭环。

### 7. 数据质量与权限

- **重复工单**：标题+正文规范化 SHA-256 指纹去重，重复工单只登记引用、不重复分类。
- **迟到更新**：事件时间与入库时间比较，超过 24h 窗口标记 `updated` 并重新分类。
- **权限导出**（`services/export.py`）：
  - `admin`：可导出工单原文；
  - `analyst`：可导出明细但 PII（邮箱/手机号/身份证）自动脱敏；
  - `viewer`：仅允许聚合统计导出；
  - 每次导出写 `AuditLog`，含操作人、角色、行数、脱敏标记。

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/tickets` | 工单入库（去重 + 在线分类） |
| POST | `/api/tickets/{id}/late-update` | 迟到更新 |
| GET | `/api/tickets/{id}/evidence` | 样本证据与纠正历史 |
| POST | `/api/versions` / `/activate` / `/train` | 版本管理/激活/训练 |
| POST | `/api/versions/reclassify` | 历史重分类（双结果保留） |
| GET | `/api/versions/diff?a=&b=` | 版本差异 |
| POST | `/api/topics/lineage` | 主题合并/拆分（级联更新） |
| GET | `/api/analytics/trend` / POST `/drift/detect` | 趋势与漂移 |
| POST | `/api/analytics/alerts/evaluate` | 告警评估 |
| POST | `/api/tickets/{id}/correction` | 人工纠正 |
| GET/POST | `/api/unknown-samples` | 未知样本队列与处理 |
| POST | `/api/export` | 角色化 CSV 导出（带审计） |

## 目录结构

```
backend/
  app/
    models.py          # 13 张领域表
    classifiers.py     # 词表/规则/朴素贝叶斯引擎
    schemas.py         # Pydantic 模型
    services/          # 业务逻辑（工单/版本/血缘/报表/告警/纠正/导出/训练）
    routers/           # API 路由
    main.py            # 应用入口
  scripts/seed.py      # 演示数据
  tests/               # 29 个 pytest 用例
frontend/              # 无构建前端（6 个功能页签 + SVG 图表）
```
