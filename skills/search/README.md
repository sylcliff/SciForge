# search — SciForge 的多源文献发现 skill

一个 CLI-first、Python stdlib-only、零第三方依赖的多源学术检索工具。核心工作流:
**主题/关键词/布尔式/MeSH 策略 → 并发查 5 个源 → 跨源去重 + RRF 排名 → NDJSON 直接管道进 `sf-download` 或 `sf-lit add`**。

- 只做发现(discovery)。**不下载 PDF**——PDF 抓取交给姊妹 skill `sf-download`。
- 只用公开 API,无 MCP、无爬虫。**Google Scholar / WoS / Scopus / CNKI 明确不做**(无稳定公开 API)。
- CLI 优先(`scripts/sf-search`),被 agent 用 Bash tool 调用。
- Python 3.10+ **stdlib only**,零运行时依赖。
- 覆盖 nature-academic-search 的 **multi-source-search** 和 **mesh-strategy** 两个工作流,其余 4 个工作流将来独立成 skill。

## 快速上手

```bash
# 0. 环境自检
scripts/sf-search doctor

# 1. 关键词搜索(最省心)
scripts/sf-search "graph neural network drug discovery" --top 20

# 2. 结构化字段
scripts/sf-search --title "attention is all you need" --author "Vaswani"

# 3. 布尔式(PubMed 精确,其他源近似)
scripts/sf-search --query '("graph neural network"[tiab] OR GNN) AND drug[tiab]'

# 4. MeSH 策略工作流
scripts/sf-search mesh lookup --concept "diabetes" --concept "heart failure"
scripts/sf-search mesh build \
  --mesh "Diabetes Mellitus" --synonym diabetes --synonym diabetic \
  --mesh "Heart Failure" --synonym "cardiac failure" \
  --op AND -o strategy.json
scripts/sf-search mesh check strategy.json
scripts/sf-search --from-strategy strategy.json --top 50

# 5. 常用管道
scripts/sf-search "topic" --top 30 --format table              # 终端表格
scripts/sf-search "topic" --top 30 | sf-lit add --meta-json -  # 免下载入库
scripts/sf-search "topic" --top 30 --format ids \
    | sf-download --from-file - \
    | sf-lit add --meta-json -                                 # 全流程
```

## 数据源

| Source | Auth | 备注 |
|---|---|---|
| PubMed E-utilities | polite email 建议 | MeSH 索引,生物医学 |
| Crossref REST | polite mailto 建议 | 跨学科,DOI 权威 |
| arXiv Atom API | 无(3s 硬间隔) | 预印本 |
| OpenAlex REST | polite mailto 建议 | 跨学科 + 引用数 |
| Semantic Scholar | API key 建议 | 引用图 + 领域过滤 |

polite email 和 S2 key 通过环境变量共享(和 `sf-download` 同一套):
- `SCIFORGE_POLITE_EMAIL`
- `SCIFORGE_S2_API_KEY`

## 目录结构

```
search/
├── SKILL.md
├── README.md
├── pytest.ini
├── scripts/
│   ├── sf-search             # 入口 shim
│   ├── main.py               # argparse + 顶层派发
│   ├── config.py             # 环境变量 + 共享 HTTP helper
│   ├── doctor.py             # 自检
│   ├── query.py              # 4 种输入模式 + 每源查询编译
│   ├── dedup.py              # union-find on (doi/pmid/arxiv_id/s2_hash)
│   ├── merge.py              # 字段级优先级合并
│   ├── rank.py               # RRF + --sort 覆盖
│   ├── output.py             # ndjson / ids / table / bib / ris 序列化
│   ├── mesh.py               # mesh lookup / build / check
│   └── sources/
│       ├── pubmed.py
│       ├── crossref.py
│       ├── arxiv.py
│       ├── openalex.py
│       └── s2.py
├── references/
│   ├── output-schema.md
│   ├── query-modes.md
│   ├── sources.md
│   ├── mesh-strategy.md
│   └── config.md
└── tests/
```

## SciForge 定位

- **kind**:micro skill
- **namespace**:无(不产生持久标识符)
- **上游**:host agent(拆概念、给关键词)、或 mesh-strategy 子命令产生的 `strategy.json`
- **下游**:`sf-download`(拿 PDF)、`sf-lit add`(免 PDF 入库)、Zotero/EndNote(通过 `--format bib/ris`)
- **配套 skill**:`sf-download`(共享 polite email、S2 key、meta schema),`sf-lit`(共享 meta ingest 契约)

## 非目标

明确**不做**的事情:

- PDF 抓取 → `sf-download`
- PDF → Markdown → `sf-lit convert`
- 本地库/标签/笔记 → `sf-lit`
- Google Scholar / Web of Science / Scopus / CNKI(无稳定公开 API,不做爬虫和机构代理)
- 引用核对、他引审计、参考文献格式转换 —— 各自将来独立成 skill
- 内嵌 LLM、自动概念拆分 —— 交给 host agent
