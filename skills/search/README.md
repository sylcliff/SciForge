# search — SciForge 的多源文献发现 skill

一个 CLI-first、Python stdlib-only、零第三方依赖的多源学术检索工具。核心工作流:
**主题 / 关键词 / 布尔式 / MeSH 策略 → 并发查 5 个源 → 跨源去重 + RRF 排名 + arXiv→正刊升级 → NDJSON 直接管道进 `sf-download` 或 `sf-lit add`**。

- 只做发现(discovery)。**不下载 PDF** —— PDF 抓取交给姊妹 skill `sf-download`。
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

# 3. 布尔式(PubMed 语法原样透传;其他源当作自由文本)
scripts/sf-search --query '("graph neural network"[tiab] OR GNN) AND drug[tiab]'

# 4. MeSH 策略工作流(PubMed 专用)
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

## 四种查询模式(互斥)

| 模式 | 何时用 | 例子 |
|---|---|---|
| 位置参数 | 最常用,各源默认相关性搜 | `sf-search "topic"` |
| `--query STR` | 已经会写 PubMed 语法,想精确 | `--query '(A OR B) AND C[tiab]'` |
| `--fields` | 按字段(标题/作者/期刊/年)搜 | `--title "..." --author "..."` |
| `--from-strategy PATH` | 执行 `mesh build` 产出的 `strategy.json` | `--from-strategy s.json` |

外加批量:`--from-file queries.txt`(一行一个查询,自动尊重每源限速)。

## 数据源

| Source | Auth | 备注 |
|---|---|---|
| PubMed E-utilities | polite email 建议 | MeSH 索引,生物医学 |
| Crossref REST | polite mailto 建议 | 跨学科,DOI 权威 |
| arXiv Atom API | 无(3s 硬间隔) | 预印本 |
| OpenAlex REST | polite mailto 建议 | 跨学科 + 引用数 + is_oa |
| Semantic Scholar | API key 建议 | 引用图 + 领域过滤 |

polite email 和 S2 key 通过环境变量共享(和 `sf-download` 同一套):
- `SCIFORGE_POLITE_EMAIL`
- `SCIFORGE_S2_API_KEY`

## 跨源去重(β 模式)

**Union-find on** `(doi, pmid, pmcid, arxiv_id, openalex_id, s2_id)` —— 任一 id 相等就并成一组。**不做标题模糊匹配**(零误合并承诺)。

字段级合并优先级:
- `title / abstract`:Crossref > OpenAlex > PubMed > S2 > arXiv
- `authors`:      Crossref > PubMed > OpenAlex > S2 > arXiv
- `journal / volume`: Crossref > OpenAlex > PubMed
- `year`:         最早非空
- `citation_count`: `max(S2, OpenAlex)`
- `is_oa`:        仅 OpenAlex 权威(其他源为 null)
- `identifiers` / `sources_hit`:union

## arXiv 预印本 ↔ 正刊升级

一篇论文常常在 arXiv 和正刊各有一份。**upgrade 逻辑分三级,默认前两级开:**

- **Path A(零 HTTP,静默红利)**:从 OpenAlex `locations[*].landing_page_url`、Crossref `relation.has-preprint`、arXiv `journal_ref` / `comment` 里提取 arxiv id 或正刊 DOI。让 β dedup 后续能自动合并。
- **Path B(post-dedup lookup,默认开)**:对**只有 arxiv 命中**的组,并发查 OpenAlex + Semantic Scholar,拿正刊 DOI 后再跑一次 β dedup。OpenAlex 用**两跳查询**(preprint DOI → title → `type:article`),避开 OA 把预印本和正刊分成两个 work 的坑。
- **Path C(可选)**:`--arxiv-upgrade-fallback title-search` 打开时,Path B 失败后走 Crossref 标题反查,要求标题 Jaccard ≥ 0.85 且首作者姓一致才合并。
- **Post-hoc 验证**:每个 upgrade 都反查 Crossref DOI,比对年份(±3)+ 首作者姓,不通过就拒绝,不占用 `sources_hit`(RRF 分不受影响)。

关闭全部升级:`--no-arxiv-upgrade`。

审计字段:
- `arxiv_upgraded: true | false`
- `arxiv_upgrade_via: "id-lookup" | "title-search"`(仅 upgraded 时)

DataCite 的 arxiv 自 DOI(`10.48550/arxiv.*`)明确不接受为"正刊 DOI",防止假升级。

## 排名

**默认 RRF(k=60)**:`score = Σ 1 / (60 + rank_in_source)`。跨源共识越强分越高。

`--sort` 覆盖:
- `relevance`(默认,RRF)
- `year:desc`(最新)
- `citations:desc`(被引最多)

## 输出格式(`--format`)

| 格式 | 用途 |
|---|---|
| `ndjson`(默认) | 机器,一行一条,`meta` 严格匹配 `sf-download` schema |
| `ids` | 一行一个标识符,直接 `--from-file -` 喂 `sf-download` |
| `table` | 人眼对齐表格 |
| `bib` | BibTeX,直接进 Zotero / EndNote / LaTeX |
| `ris` | RIS,同上 |

## Agent 呈现契约

当 agent(Claude / Codex / 其他 LLM)在对话里给用户展示 `sf-search` 结果时,遵守 memory 里的 [`sf-search-presentation`](~/.claude/projects/D--code-SciForge/memory/sf-search-presentation.md) 三段式:

1. **Section 0**:命令回显 + 3 行摘要(命中数、源分布、失败源、去重效果、upgrade 数、OA 估计、噪声估计)
2. **Section 1**:**5 个固定角色槽**(综述 / 里程碑 / 核心方法 / 新兴前沿 / 应用),密度 C(3-4 行 + 摘要片段 + 推荐理由)
3. **Section 2**:**4 组动态命名**(第 1、4 组通常是"综述"和"噪声",中间两组跟查询),每组 top 5,密度 B
4. **Section 3**:完整清单,密度 A(1 行/篇),上限 100

推荐理由必须用**闭集 6 tag**:`[综述] / [里程碑] / [方法核心] / [新方向] / [应用] / [疑似噪声]`。禁用"必读 / 经典 / 很重要"等空词。

## 目录结构

```
search/
├── SKILL.md
├── README.md
├── pytest.ini
├── scripts/
│   ├── sf-search             # 入口 shim
│   ├── main.py               # argparse + 顶层派发
│   ├── config.py             # 环境变量 + 共享 HTTP helper + token bucket
│   ├── doctor.py             # 自检
│   ├── query.py / query_obj.py  # 4 种输入模式 + 每源查询编译
│   ├── dedup.py              # union-find on 6 类 id
│   ├── merge.py              # 字段级优先级合并
│   ├── rank.py               # RRF + --sort 覆盖
│   ├── output.py             # ndjson / ids / table / bib / ris 序列化
│   ├── mesh.py               # mesh lookup / build / check
│   ├── arxiv_upgrade.py      # Path B/C 编排 + 验证
│   └── sources/
│       ├── pubmed.py
│       ├── crossref.py       # + lookup_by_title, get_by_doi
│       ├── arxiv.py          # + journal_ref/comment DOI 抽取
│       ├── openalex.py       # + is_oa, pmcid, 两跳 lookup_by_arxiv
│       └── s2.py             # + lookup_by_arxiv
├── references/
│   ├── output-schema.md
│   ├── query-modes.md
│   ├── sources.md
│   ├── mesh-strategy.md
│   └── config.md
└── tests/                    # 87 offline tests, ~0.4s
    ├── conftest.py           # 自动 mock crossref.get_by_doi
    ├── test_arxiv_upgrade.py
    ├── test_crossref_extraction.py
    ├── test_dedup_merge_rank.py
    ├── test_mesh.py
    ├── test_output.py
    └── test_query.py
```

## 常用 flag 速查

```
sf-search [QUERY]
  --query STR                              # 原样透传给每源
  --title T --author A --year 2020..2024   # 字段模式
  --from-strategy strategy.json            # 执行 MeSH 策略
  --from-file queries.txt                  # 批量

  --sources pubmed,crossref,arxiv,openalex,s2   # 子集(默认全部)
  --top N                                  # 用户可见数(默认 30)
  --per-source-limit N                     # 每源召回上限(默认 top*2,上限 100)

  --sort relevance|year:desc|citations:desc
  --format ndjson|ids|table|bib|ris        # 默认 ndjson
  --out PATH                               # 默认 stdout

  --no-arxiv-upgrade                       # 关掉 Path B (加快,可能出现重复)
  --arxiv-upgrade-fallback title-search    # 开启 Path C(慢但更全)
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
- 标题级模糊去重(β 模式的零误判承诺)

## 测试

```bash
cd skills/search && python -m pytest tests/ -v
# 87 passed in ~0.4s
```

所有测试离线,通过 `conftest.py` 自动 mock 网络。
