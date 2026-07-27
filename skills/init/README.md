# init — SciForge 配置向导

第一次装 SciForge?或者 `sf-download` 一直报 `Unpaywall skipped`、`sf-lit convert`
说 `mineru not found`?就跑这个:

```bash
scripts/sf-init
```

它做的一件事:把 `~/.config/sciforge/config.toml` 配好,`sf-download`、
`sf-lit`、`sf-search` 就都能干活了。

## 快速上手

```bash
# 从零开始 / 补齐缺的键
scripts/sf-init

# 只想看当前配置健康度,不改任何东西
scripts/sf-init doctor

# 打印将要写入的 TOML(遮蔽 secrets),不落盘
scripts/sf-init --print-config

# 忘了配置成什么,想全部重来
scripts/sf-init --reset

# 脚本 / CI 调用,一次把答案全塞进来
scripts/sf-init --non-interactive \
    --email you@example.com \
    --library ~/papers \
    --converter mineru
```

## 它管什么

配置 SciForge 用到的 7 个键,分两组:

**必填(2 个)**

| 键 | 用途 |
|---|---|
| `download.polite_email` | Unpaywall 强制需要;Crossref / OpenAlex polite pool 也用。不填就等着 `sf-download` 全部返回 `metadata_only`。 |
| `library.path` | `sf-lit` 存 PDF / index.db / collections 的根目录。 |

**可选(5 个,Enter 跳过)**

| 键 | 不填的后果 |
|---|---|
| `download.semanticscholar_api_key` | 批量下载 10 篇左右就 429。 |
| `converter.default` (`mineru` / `docling`) | `sf-lit convert` 每次要显式加 `--converter`。 |
| `download.download_dir` | 落到默认的 `~/.sciforge/inbox/`。 |
| `sources.github.token_env` (GitHub Token) | `sf-search` 的 GitHub 源关闭。 |
| `sources.pubmed.api_key` (NCBI Key) | PubMed 限流从 10 req/s 掉到 3 req/s。 |

## 它不管什么

- **不装 MinerU / Docling**。用 `pipx install mineru` 或 `pipx install docling`
  自己装,然后再跑 `sf-init` 让 doctor 确认能找到二进制。
- **不填 `me` 档案**(研究者自己的技能 / 设备 / 算力清单)。用完 init 结尾
  会一行提示 `sf-me edit`,不强制。
- **不动网络**。有代理就先设 `HTTP_PROXY` / `HTTPS_PROXY`,或者 `--skip-network`
  跳过探活。

## 配置文件写在哪儿

按 SciForge 现有的解析顺序(和 `sf-download` / `sf-lit` 完全一致):

1. `$SCIFORGE_CONFIG`(显式路径,最高优先级)
2. 当前目录起向上找 `.sciforge.toml`(**项目本地**),遇到 git root 停
3. `$XDG_CONFIG_HOME/sciforge/config.toml`,或没设就 `~/.config/sciforge/config.toml`(**用户全局**)
4. 内置默认

`sf-init` 默认写用户全局,首次跑会问一句 "全局还是项目本地?":

- **全局** — 邮箱 / API key 这些跨项目的东西,写这里。所有 SciForge 项目共用。
- **项目本地** — 库路径这种"跟项目走"的键,写这里。**从不写 secrets** —— 会
  被 git commit 出去的风险太大。

跑在 git 仓库里的话,init 会顺手往 `.gitignore` 追加 `.sciforge.toml`
和 `sciforge/`(幂等,已经有就跳)。

## Secrets 怎么存

三个 secret 键(`s2_api_key`、`github_token`、`ncbi_api_key`)走分层策略:

1. **环境变量已设** → 显示 `[from env]`,不写文件,不问;
2. **没设** → 问你选:
   - `env`(推荐):打印一段 `export SCIFORGE_S2_API_KEY=...`,你自己贴到
     shell rc 里。文件不动。
   - `file`:写进 **全局** config(`~/.config/sciforge/config.toml`),而且
     上面加一行 `# WARNING: secret — do not commit` 注释。项目本地
     `.sciforge.toml` **无论如何都不写**。
   - `skip`:什么都不做,以后自然踩到 rate-limit / 权限错误。

## Merge 语义

跑第二次 `sf-init` 不会覆盖你的文件:

- 已有键保留原值,除非你在提问时主动改;
- 已有注释和未识别键(比如以后新加的 [sources.custom] 之类)**全部保留**
  —— 用的是 `tomlkit`,不是标准库那个只读的 `tomllib`;
- 写入前先复制成 `config.toml.bak-<UTC-timestamp>`;
- 原子写:`.tmp` → `rename`,不会落下半份坏 TOML。

`--reset` 明确说"我要重来一次",还是先备份再从头问。

Skipped 的键会记在 TOML 自己的 `[init]` section 里:

```toml
[init]
version = "1"
last_run_at = "2026-07-26T15:32:00Z"
skipped_keys = ["semanticscholar_api_key", "github_token"]
```

下次跑就不会再骚扰你已经明确跳过的问题。

## Doctor:结束时的健康检查

TOML 写完之后,`sf-init` 会自动跑一次 `doctor`,输出一张打勾表:

```
Setup complete. Config written to C:\Users\syllz\.config\sciforge\config.toml
Backup: C:\Users\syllz\.config\sciforge\config.toml.bak-2026-07-26T15-32-08Z

Config values
─────────────────────────────────────────────────
✓ polite_email                user@example.com
✓ library                     D:\code\...\library  (writable)
✓ converter.default           mineru
⚠ semanticscholar_api_key     unset
                              → S2 will rate-limit after ~10 requests
                              → fix:  sf-init and answer question 3
                                      or  export SCIFORGE_S2_API_KEY=...

Reachability (--skip-network to bypass)
─────────────────────────────────────────────────
✓ arxiv             152ms
✓ crossref          238ms
✓ unpaywall         141ms
✓ openalex          189ms
⚠ semanticscholar   429
                    → fix:  register S2 API key (see above)

Optional: describe yourself as a researcher for topic-fit checks.
Run  `sf-me edit`  when you're ready.
```

任何一个 `⚠` 或 `✗` 都带着"下一步做什么"的复制粘贴命令。

## 反面用途

- **不要**用它跑 CI 里的 `sf-init`(会一直卡在 stdin),要么用 `--non-interactive`
  + 全 flags,要么走单元 / 集成测试路径。
- **不要**把它当 "sf-download 能不能下 PDF" 的测试 —— doctor 只探活 endpoint,
  不真的下东西。真的想 smoke test 用 `sf-download 1706.03762`。
- **不要**手改 `[init]` section 里的 `version`。以后升级 schema 时靠它识别。
