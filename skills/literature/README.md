# literature — SciForge 的本地文献库 skill

一个用 SQLite + 磁盘目录管理个人科研文献的 skill。核心工作流:
**PDF 归档 → MinerU/Docling 转 Markdown → paper-level 全文搜索**。

- 只做本地数据管理。**不联网抓取**——arXiv / DOI / PubMed 抓取交给
  companion skills。
- CLI 优先(`scripts/sf-lit`),被 agent 用 Bash tool 调用。
- Python 3.10+ **stdlib only**,零运行时依赖。
- MinerU 或 Docling 由用户自己安装(`pipx install mineru` / `pipx install docling`),
  skill 通过子进程调用。

## 快速上手

```bash
# 1. 装一个 PDF→MD converter(选一个即可,MinerU 是默认)
pipx install mineru
# 或
pipx install docling

# 2. 初始化库
scripts/sf-lit doctor        # 环境自检
scripts/sf-lit init          # 默认在 ./library

# 3. 归档一篇论文 + 立即转 Markdown
scripts/sf-lit add \
  --title "Attention Is All You Need" \
  --author "Ashish Vaswani" \
  --year 2017 --arxiv-id 1706.03762 \
  --pdf-path /tmp/attention.pdf \
  --and-convert                             # 一步归档 + 转换

# 4. 搜索
scripts/sf-lit search "attention mechanism"
scripts/sf-lit read vaswani2017attention --section "Methods"
```

分两步走(严格两阶段)也是标准姿势:

```bash
scripts/sf-lit add --title ... --pdf-path P     # md_status=absent
scripts/sf-lit convert vaswani2017attention     # md_status=ready
```

## 目录结构

```
library/
├── index.db                              # SQLite (catalog + FTS 索引)
├── papers/
│   └── vaswani2017attention/
│       ├── paper.pdf                     # 原始 PDF
│       ├── metadata.json                 # 结构化元数据 (source of truth)
│       ├── notes.md                      # 用户笔记
│       ├── paper.md                      # canonical Markdown(FTS 源)
│       ├── converter.json                # {converter, version, pdf_sha256, ...}
│       ├── converter_output/
│       │   ├── mineru/                   # MinerU 原始输出(全保留)
│       │   │   ├── paper.md, *_content_list.json, images/, ...
│       │   └── docling/                  # 或 Docling(切换时新老共存)
│       └── si/                           # SI 附件(不进 MD 流水线)
├── collections/
└── cache/
```

关键契约:
- **`paper.md` 是 canonical**,DB 里的 `papers_md.markdown` 就是它的副本,FTS 索引也基于它。
- **一篇论文一个 converter** —— 想切换就用 `convert --reconvert --converter docling`,新旧输出树并存,`converter.json` 记录当前 canonical 是哪个。
- **`metadata.json` + `paper.md` + `converter.json` 组成磁盘 truth**,`index.db` 随时可以从 `rebuild-db` 重建。

## 命令一览

### 归档 & 转换

| 命令 | 作用 |
|---|---|
| `sf-lit add --meta-json - --pdf-path P` | 从 JSON blob + PDF 入库 |
| `sf-lit add --title ... --pdf-path P` | 从 CLI flags 入库 |
| `sf-lit add ... --and-convert` | 入库 + 立即转 MD(糖) |
| `sf-lit add ... --upsert` | 合并到已有条目(元数据 union) |
| `sf-lit convert <key>` | 渲染 canonical `paper.md` |
| `sf-lit convert <key> --converter docling` | 换 converter 重跑 |
| `sf-lit convert <key> --reconvert [--force]` | 再跑一次(带 sha256 保险丝) |
| `sf-lit convert <key> --converted-dir D` | 逃生舱:直接吃已有输出,不启动子进程 |

### 检索 & 阅读

| 命令 | 作用 |
|---|---|
| `sf-lit search "<query>" [--tag --year --author --has-md --json]` | BM25 + 结构化过滤 |
| `sf-lit read <key>` | 打印整篇 `paper.md` |
| `sf-lit read <key> --section "3.2"` | 章节抽取(fuzzy 匹配,支持 MinerU + Docling) |
| `sf-lit read <key> --pages 3-5` | 按页读(**MinerU-only**) |
| `sf-lit read <key> --kind table` | 按 block 类型读(**MinerU-only**) |
| `sf-lit read <key> --grep "regex"` | 正则 grep `paper.md` |
| `sf-lit show <key>` | 元数据卡片 + MD 状态行 |
| `sf-lit status <key>` | 单篇 MD 状态(带 stale 重校验) |
| `sf-lit list --md-status absent\|ready\|failed\|stale` | 批量按状态列表 |

### 关联 & 导出

| 命令 | 作用 |
|---|---|
| `sf-lit tag <key> <name>` | 加/删标签(`--remove`) |
| `sf-lit collection <slug> add\|remove <key>` | 集合成员管理 |
| `sf-lit note <key> [--append TXT \| --set-from FILE]` | 笔记操作 |
| `sf-lit add-github <key> --owner O --repo R [...]` | 挂载 GitHub 仓库 |
| `sf-lit add-news <key> --url U [...]` | 挂载新闻/博客链接 |
| `sf-lit add-si <key> --path P [--label L]` | 挂载 SI 附件(不进 MD 索引) |
| `sf-lit export <selector> --format bibtex\|json` | 引文导出 |
| `sf-lit open <key> [pdf\|md\|notes\|si\|si:N\|github\|url]` | 用系统默认程序打开 |

### 运维

| 命令 | 作用 |
|---|---|
| `sf-lit doctor` | 检查环境 + DB + converter 二进制 |
| `sf-lit init [--path DIR] [--force]` | 初始化库(幂等) |
| `sf-lit rebuild-db` | 从磁盘 sidecar 重建 `index.db`(含 `papers_md`) |
| `sf-lit citekey --author X --year Y --title Z` | 预生成 citekey(companion 用) |
| `sf-lit config path\|show\|get <key>` | 检查配置 |

## md_status 状态机

| 状态 | 含义 | 出路 |
|---|---|---|
| `absent` | `paper.md` 不存在,没进 FTS。只有元数据可搜。 | `sf-lit convert <key>` → `ready` |
| `ready` | `paper.md` 存在,FTS 索引好。搜索命中。 | `--reconvert` 覆盖;PDF 换了 → `stale` |
| `failed` | 上次 convert 失败,`md_last_error` 里有原因。 | `sf-lit convert <key> --reconvert` 重试 |
| `stale` | PDF sha256 变了(或 `paper.md` 被手删)。搜索仍能命中旧 MD 但 `read` 报警。 | `sf-lit convert <key> --reconvert` |

`status <key>` 每次运行都会 stat 磁盘并比对 sha256,自动把 `ready` 降级成 `stale` 并持久化,DB 不撒谎。

## Companion skill 契约

外部 fetch skill(arXiv / DOI / PubMed)交付**三样东西**给 literature:

1. 一份符合 [`references/ingest-interface.md`](references/ingest-interface.md) 的 metadata JSON
2. 一个本地 PDF 路径(非零字节)
3. 可选:一个 citekey 建议(`sf-lit citekey ...` 预生成)

典型 pipeline(假设有 `arxiv-fetch` skill):

```bash
arxiv-fetch --id 1706.03762 --emit-json --with-pdf /tmp/paper.pdf \
  | scripts/sf-lit add --meta-json - --pdf-path /tmp/paper.pdf --move-pdf --and-convert
```

## 配置

`scripts/sf-lit config path` 打印当前生效的配置文件位置。查找顺序:

1. `$SCIFORGE_CONFIG`
2. `./.sciforge.toml`(从 cwd 向 git root 逐层查找)
3. `$XDG_CONFIG_HOME/sciforge/config.toml` 或 `~/.config/sciforge/config.toml`
4. 内建默认值

最常用的两个环境变量,用来在 conda / docker / venv 场景下覆盖 converter 位置:

```bash
export LITLIB_MINERU_BIN="/home/you/miniconda3/envs/mineru/bin/mineru"
export LITLIB_DOCLING_BIN="docker run --rm -v /tmp:/tmp docling-image docling"
```

完整配置键见 [`references/config.md`](references/config.md)。

## 测试

```bash
pip install pytest
pytest tests/                     # 101 tests, ~65s
```

测试用 `tests/fixtures/fake_mineru.py` 和 `fake_docling.py` 作为 converter stub,不需要真装 MinerU/Docling 就能跑集成测试。真 converter 的验证走 SKILL.md 底部的 verification block(手动执行)。

## 参考文档

- [SKILL.md](SKILL.md) — 给 agent 读的调用契约
- [references/schema.md](references/schema.md) — SQLite DDL + FTS 触发器 + `md_status` 语义
- [references/config.md](references/config.md) — 所有配置键
- [references/ingest-interface.md](references/ingest-interface.md) — companion JSON schema
- [references/recipes.md](references/recipes.md) — 常用调用模式
- [references/bibtex.md](references/bibtex.md) — BibTeX 映射和 citekey 规则

## 设计边界(不做什么)

- **不联网**——arXiv/DOI/PubMed/GitHub/News 抓取由 companion skills 负责,literature 只负责本地库。
- **不做异步**——`convert` 阻塞到 MinerU 跑完,没有后台任务表、没有 `pending` 状态。
- **不做 chunk 级索引**——一行一 paper,章节/页码级操作是数据库外的 `read` 后处理,基于磁盘上的 `paper.md` 和 `content_list.json`。
- **不给 SI 建 FTS 索引**——SI 附件通过 `add-si` 挂载,不进 MinerU/Docling 流水线。想让 SI 可搜就作为独立 paper 入库。
