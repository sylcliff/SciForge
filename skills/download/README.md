# download — SciForge 的 API-first 论文下载 skill

一个用公开 API 把论文的**元数据 + OA PDF** 抓下来的 micro / companion skill，专门给 `sf-lit add` 喂料。

- **只走公开 API**：arXiv / Crossref / Unpaywall / OpenAlex / Semantic Scholar。
- **不做**：Sci-Hub / 机构会话 / Chrome 控制 / 关键词检索 / 本地库管理。付费墙拿不到 OA 就报 `paywalled`，交给别的 skill。
- Python + PEP 723 inline deps (`httpx`, `pydantic`)。
- 输出 NDJSON，per-paper 一行 + 末尾一行 summary，天然对接 `sf-lit`。

## 快速上手

```bash
# 1. 配一次邮箱（可选，但 Unpaywall 要它）
export SCIFORGE_POLITE_EMAIL="you@example.com"

# 2. 自检
scripts/sf-download doctor

# 3. 单篇 arXiv
scripts/sf-download 1706.03762
# → ~/.sciforge/inbox/1706.03762.pdf
# → stdout 一行 JSON：status=downloaded, source_used=arxiv, meta={...}

# 4. 单篇 DOI（自动走 Unpaywall → S2 → Crossref）
scripts/sf-download 10.1038/s41586-020-2649-2

# 5. 批量
printf "1706.03762\n10.1038/s41586-020-2649-2\n" > /tmp/ids.txt
scripts/sf-download --from-file /tmp/ids.txt
# → 每篇一行 NDJSON，末尾一行 summary

# 6. 精确题名兜底（arXiv 老论文 / 无 DOI）
scripts/sf-download --title "Attention Is All You Need"
```

## 支持的 identifier

自动识别，任意一种都行：

```
1706.03762                          # 裸 arXiv ID
arXiv:1706.03762                    # 带前缀
10.1038/s41586-020-2649-2           # 裸 DOI
https://doi.org/10.1038/...         # DOI URL
https://arxiv.org/abs/1706.03762    # arXiv URL
W2741809807                         # OpenAlex work ID
649def...(40 hex)                   # Semantic Scholar hash
```

## Fallback 顺序

**PDF（谁先命中谁赢）**：
`arXiv → Unpaywall → Semantic Scholar → Crossref links`

**元数据 union（优先级高的字段覆盖低的）**：
`Crossref > Semantic Scholar > OpenAlex > arXiv`

细节：[SKILL.md](SKILL.md) + [references/sources.md](references/sources.md)。

## 输出契约

每篇一行 NDJSON：

```json
{
  "index": 0,
  "identifier": "10.1038/s41586-020-2649-2",
  "status": "downloaded",
  "pdf_path": "/home/you/.sciforge/inbox/10.1038_s41586-020-2649-2.pdf",
  "source_used": "unpaywall",
  "sources_queried": ["crossref", "unpaywall"],
  "bytes": 3521117,
  "meta": {
    "title": "...", "authors": ["..."], "year": 2020,
    "doi": "10.1038/s41586-020-2649-2", "url": "...", "abstract": "..."
  }
}
```

批量末尾一行：

```json
{"summary": {"total": 10, "downloaded": 7, "paywalled": 1,
             "identifier_not_found": 1, "rate_limited": 1,
             "elapsed_seconds": 8.4, "warnings": []}}
```

`meta` 严格对齐
[`skills/literature/references/ingest-interface.md`](../literature/references/ingest-interface.md)
v2，直接 pipe 给 `sf-lit add --meta-json -` 就行。

## 9 个 status 值

`downloaded` · `metadata_only` · `paywalled` · `identifier_not_found` ·
`pdf_link_broken` · `title_ambiguous` · `rate_limited` · `network_error` ·
`invalid_input`

三个"失败"故意分开写：
- `paywalled` — Unpaywall 明说 `is_oa=false`
- `metadata_only` — 元数据齐但 5 个源都没给 OA PDF
- `pdf_link_broken` — 有 PDF URL 但字节抓不到 / 不是 `%PDF`

完整触发规则见 [references/status-codes.md](references/status-codes.md)。

## 和 sf-lit 的 pipeline

```bash
scripts/sf-download 1706.03762 --emit-json > /tmp/r.jsonl
jq -c 'select(.status=="downloaded") | .meta' /tmp/r.jsonl \
  | ../literature/scripts/sf-lit add --meta-json - \
      --pdf-path "$(jq -r 'select(.status=="downloaded") | .pdf_path' /tmp/r.jsonl)" \
      --move-pdf --and-convert
```

`--move-pdf` 会把 inbox 里的 PDF 移进 library，避免两处重复。

跟进任务：让 `sf-lit add --meta-json -` 支持顶层 `pdf_path` 字段，管道能收敛成一条：
```bash
scripts/sf-download 1706.03762 --emit-json | sf-lit add --meta-json -
```

## 配置

配置在 `SCIFORGE_CONFIG`（和 `sf-lit` 共用的那份 TOML）里的 `[download]` section：

```toml
[download]
polite_email = "you@example.com"    # 强烈建议；Unpaywall 需要它，Crossref/OpenAlex polite pool 也用
semanticscholar_api_key = ""        # 可选；不填就匿名跑，容易 429
http_timeout_seconds = 30
```

环境变量覆盖（env > config > default）：

- `SCIFORGE_POLITE_EMAIL`
- `SCIFORGE_S2_API_KEY`
- `SCIFORGE_HTTP_TIMEOUT`
- `SCIFORGE_DOWNLOAD_DIR`

**没配 email 会怎样？** `doctor` 只 warn，不 fail。运行时 Unpaywall 会被静默跳过，其它 4 个源照常跑。

## Exit codes

- **批量模式**（`--ids` / `--from-file`）：**永远 exit 0**（除非启动阶段崩）。每篇的成败看 JSON 的 `status`。
- **单篇模式**：`downloaded/paywalled/metadata_only/pdf_link_broken` → 0；`identifier_not_found` → 3；`invalid_input` → 2；崩溃 → 1/≥64。

## Non-goals（明确不做）

1. Sci-Hub / LibGen / 任何盗版镜像
2. CDP / 浏览器控制 / 机构会话
3. 主题 / keyword 检索（那是发现层的活）
4. 本地库管理 / 元数据索引 / 全文搜索（`sf-lit` 的活）
5. BibTeX / RIS 导出（`sf-lit export`）
6. PDF → Markdown 转换（`sf-lit convert`）
7. 补充材料 (SI) 抓取
8. 元数据手工修正 / DOI 映射
9. 超出 `%PDF` 头之外的 PDF 内容校验

## 目录结构

```
skills/download/
├── SKILL.md                      # 给 agent 读的调用契约
├── README.md
├── references/
│   ├── config.md
│   ├── sources.md                # 5 个源的 endpoint、请求形状、字段映射
│   ├── status-codes.md           # 9 个状态码的权威定义
│   ├── output-schema.md          # NDJSON 结构 + pydantic 模型 + 文件名规则
│   └── recipes.md                # 常用调用模式（含 sf-lit pipe）
├── scripts/
│   ├── sf-download               # PEP 723 entry point
│   ├── main.py                   # argparse + verb dispatch
│   ├── identifiers.py            # 归一化 & 安全文件名
│   ├── sources/                  # 5 个源，一文件一个
│   ├── fetch.py                  # 编排：fallback + union + 并发
│   ├── pdf.py                    # 下载 + %PDF 头校验
│   ├── doctor.py
│   ├── config.py
│   └── output.py
└── tests/
    ├── test_identifiers.py
    ├── test_output_schema.py
    ├── test_fetch_orchestration.py  # respx mock 5 个源
    └── fixtures/responses/
```

## 测试

```bash
pip install pytest respx
pytest skills/download/tests/
```

## 参考文档

- [SKILL.md](SKILL.md) — 契约、routing、interaction rules、non-goals
- [references/config.md](references/config.md) — 全配置键
- [references/sources.md](references/sources.md) — 5 个源的 API 细节
- [references/status-codes.md](references/status-codes.md) — 9 状态码定义
- [references/output-schema.md](references/output-schema.md) — NDJSON schema + 文件名规则
- [references/recipes.md](references/recipes.md) — 常用调用模式

## 设计原则（一句话版）

- **谁在乎语义，谁保。** `paywalled` / `metadata_only` / `pdf_link_broken` 故意分开写——三种失败对应完全不同的下一步。这是从 `nature-downloader` 学来的最有价值的观察。
- **Token 纪律。** 大响应体（arXiv Atom XML、PDF 字节）都不进 agent context，只回一行紧凑 JSON。
- **不做发现，只做落地。** 用户/agent/别的 skill 先决定"我要哪几篇"，然后这个 skill 干活。
- **Skill 之间只通过 JSON 契约。** `sf-download` 不 import `sf-lit`；`sf-lit` 不 import `sf-download`；两者只在 shell pipe 里相遇。
