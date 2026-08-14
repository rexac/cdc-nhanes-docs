# CDC NHANES Codebook 本地化仓库

全自动下载 **CDC NHANES 所有调查周期、所有模块**的 Codebook 文档页
（.htm），建立本地完整的变量字典数据库，并纳入 git 版本管理。

## 为什么需要本地化

- NHANES 官方文档页（`wwwn.cdc.gov/.../DataFiles/XXX.htm`）是**变量含义的权威来源**
- 容器 SQL 的 `Metadata.VariableCodebook` 可能滞后/不完整
- 网络不稳时（CDC 反爬、间歇 404）本地文档可离线查证
- **铁律**：任何变量用于分析前必须查 codebook 确认含义（见 nhanes-paper/memory.md）

## 快速开始

```bash
# 1. 安装依赖（容器或宿主机均可）
pip install requests beautifulsoup4

# 2. 运行（下载到默认 ./nhanes_docs/）
python3 download_all_cdc_docs.py

# 3. 指定目标目录
python3 download_all_cdc_docs.py /HostData/Data/cdc_docs

# 4. 增量更新（只下载缺失/变化的文档，可反复运行）
python3 download_all_cdc_docs.py
```

## 目录结构

```
nhanes_docs/
├── 1999/
│   ├── demographic/    # 人口统计 (Demographic)
│   ├── dietary/        # 饮食 (Dietary)
│   ├── examination/    # 体检 (Examination)
│   ├── laboratory/     # 实验室 (Laboratory)
│   ├── questionnaire/  # 问卷 (Questionnaire)
│   └── delayed/        # 延迟公开数据 (Delayed) ← SSHOLO/SSKL/SSHPV 等
├── 2001/
│   └── ...
└── 2023/
    └── ...
```

每周期 × 每组件一个子目录，文档文件名为 `数据表名.htm`（如 `SSHOLO_G.htm`、
`MCQ_G.htm`），内含完整 Codebook（变量名、取值、描述、检测限）。

## 脚本要点（v2 修复）

| 项 | 说明 |
|---|---|
| Component 拼写 | 官方接口用单数 `Demographic`（`Demographics` 只返回 40/195 链接） |
| Delayed 组件 | **必须包含** —— 延迟公开数据文档全在此（初版遗漏） |
| 周期覆盖 | 1999-2023（含 2017-2020 P 周期、2021-2023 L 周期） |
| 分目录存储 | 周期/组件子目录，避免平铺重名 |
| 断点续传 | 已下载非空文件自动跳过，网络断了重跑即可 |
| 失败重试 | 每 URL 最多 3 次，指数退避 + 随机抖动 |
| 防封禁 | 浏览器 UA + 请求间隔 0.2-0.4s |

## 常见问题

- **下载失败较多**：CDC 对高频请求有限制，降低频率（改 sleep）或分批跑
- **某文件为空**：可能是 404（该表在该周期不存在），脚本会跳过并计数
- **更新文档**：CDC 会修订文档（Last Revised），重跑脚本会重新下载？
  → 当前逻辑只下载缺失文件；如需强制刷新删掉对应 .htm 再跑即可

## 变量查证用法

```bash
# 查某变量含义（本地离线）
grep -A3 'SSHOLO' nhanes_docs/2011/delayed/SSHOLO_G.htm
```

## License / 来源

数据版权归 CDC/NCHS 所有，仅供研究使用。详见 NHANES Data User Agreement。
