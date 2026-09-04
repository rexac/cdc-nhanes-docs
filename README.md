# CDC NHANES Codebook 本地化仓库

将 CDC/NCHS 官方 NHANES HTML Codebook 镜像到本地并纳入 Git 管理，便于离线查证变量含义、取值、检测限和发布修订。

## v3.1 的核心变化

- **未来 NHANES 周期自动发现**：不再需要为新周期手工修改 Python 周期列表。
- 以 CDC 官方 `Demographics Data - Continuous NHANES` 总表的 `Years` 列作为“已正式发布周期”的发现源。
- 历史周期仅作为安全基线和特殊目录映射；未来周期会使用 CDC 官方完整周期名作为本地目录，例如未来若正式发布 `2025-2027`，会自动建立 `nhanes_docs/2025-2027/`。
- 只有真正出现 public-use Demographics codebook 的周期才会被视为已发布；提前出现但没有正式 codebook 的导航/空壳周期不会建立空目录。
- 一个动态周期一旦已经被镜像，如果后续 CDC 发现页临时缺失该周期，同步器会在任何下载或 prune 之前失败，避免误删整个新周期。
- 动态周期中尚未发布的组件允许暂时为空；某个组件一旦已有本地 codebook，后续该组件的索引就进入严格验证，CDC 页面异常不会被误解释为“全部下架”。
- `query_codebook.py --cycle 2025` 对未来周期也可用：若本地只有一个以 2025 开头的周期目录，会自动解析到完整周期名。

## v3 的基础安全设计

- 使用 CDC 官方 `Cycle=` 参数，而不是用开始年份猜周期。
- 官方人口学组件参数是 `Demographics`（复数）；本地目录为了兼容仍叫 `demographic/`。
- 移除并拒绝伪 `Delayed` 组件；延迟发布的数据仍归入其真实组件（如 Laboratory）。
- `2021` / `2023` 合并为官方的 `2021-2023` release。
- 新增真正的 `2017-2020` Pre-Pandemic (`P_*`) release，同时保留 `2017` 目录表示 2017-2018 (`*_J`)。
- 所有脚本共享 `nhanes_core.py`，避免周期/组件配置再次漂移。
- `--prune` 带大规模删除保险：默认超过 100 个删除就终止，迁移时必须显式 `--allow-large-prune`。
- 在任何下载/删除之前先完成周期发现和全部活跃 CDC 索引验证；一旦发现页、索引页疑似 fallback 或抓取失败，直接终止且不改本地文件。

## 规范目录结构

```text
nhanes_docs/
├── 1999/                 # 1999-2000
│   ├── demographic/
│   ├── dietary/
│   ├── examination/
│   ├── laboratory/
│   └── questionnaire/
├── 2001/                 # 2001-2002
├── ...
├── 2017/                 # 2017-2018, *_J
├── 2017-2020/            # 2017-March 2020 Pre-Pandemic, P_*
├── 2021-2023/            # August 2021-August 2023, *_L
└── <future-cycle>/        # 自动发现，例如未来官方正式发布的完整周期名
```

默认镜像 5 个公开组件：Demographics、Dietary、Examination、Laboratory、Questionnaire。RDC/limited-access codebook 可通过 `--include-non-public` 选择性镜像。

## 安装

```bash
python -m pip install -r requirements.txt
```

## 初次下载 / 修复缺失

```bash
python download_all_cdc_docs.py ./nhanes_docs
```

默认只补缺失，速度更快；若同时要逐文件检查官方正文是否修订：

```bash
python download_all_cdc_docs.py ./nhanes_docs --verify-content
```

`download_all_cdc_docs.py` 复用同一个 `CDCSync`，因此也会自动发现未来正式发布的周期。

## 同步与完整性检查

```bash
# 同步新增 + 比较修订
python sync_cdc_docs.py --target nhanes_docs

# 同步 + 清理官网索引之外的旧文件（常规安全上限 100）
python sync_cdc_docs.py --target nhanes_docs --prune

# 只读检查；任何缺失/修订/多余文件都会返回非 0
python sync_cdc_docs.py --target nhanes_docs --check

# 仅在明确审核过的大规模目录迁移时使用
python sync_cdc_docs.py --target nhanes_docs --prune --allow-large-prune
```

### 自动发现新周期时会发生什么

假设 CDC 将来正式发布 `2025-2027`：

```text
Release discovery: 13 published cycle(s) (1 dynamic)
New dynamically discovered release(s): 2025-2027
OK 2025-2027 demographic      1 docs
WAIT 2025-2027 dietary        0 published codebooks
...
ACTIVE dynamic release 2025-2027: 1 codebooks
```

同步器会创建 `nhanes_docs/2025-2027/demographic/` 并下载已正式发布的 codebook。以后 Dietary、Laboratory 等组件陆续发布时，会在后续每周 Actions 中自动补齐。

### 为什么不默认允许大规模 prune

CDC 偶发网络错误或页面结构变化不应该被解释成“上千份文件被下架”。同步器会先验证周期发现和活跃索引，并且即使索引都通过，默认超过 100 个删除仍会触发保险。只有明确审核过的结构迁移才应使用 `--allow-large-prune`。

## 查询变量

```bash
# 模糊搜索
python query_codebook.py SSHOLO --root nhanes_docs

# 精确变量名，并限定周期/组件
python query_codebook.py MCQ070 --exact --cycle 2011 --component questionnaire --root nhanes_docs

# 旧 2021 / 2023 参数会自动映射到 2021-2023
python query_codebook.py --list 2021 laboratory --root nhanes_docs

# 未来动态周期：若只有一个 2025-* 目录，可直接使用开始年份
python query_codebook.py --list 2025 laboratory --root nhanes_docs

# 输出命中附近文本
python query_codebook.py LBXTC --exact --show --root nhanes_docs

# 机器可读结果
python query_codebook.py LBXTC --exact --json --root nhanes_docs
```

## GitHub Actions

`.github/workflows/sync.yml` 每周一 02:00 UTC 自动运行。无需为新周期修改 workflow；周期发现发生在 `nhanes_core.py` 中。普通定时运行不能大规模删除文件。

## 数据来源

文档来自 CDC/NCHS NHANES 官方站点。数据和文档版权及使用条件以 CDC/NCHS 官方说明为准。
