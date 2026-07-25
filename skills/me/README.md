# me — SciForge 的「知己」skill

古人云「知己知彼，百战不殆」——做科研也一样。`sf-me` 是 SciForge
里的**知己**：把「你这个研究者」结构化地记录成一份本地 Markdown
文件，供其它 skill（现在的和未来的）用来做选题匹配、可行性判断和
自我校准。

- **只做本地档案管理**——不打分、不建议、不给你「排选题」。选题
  顾问逻辑留给未来的 companion skill（`sf-fit-check` 等）。
- CLI 优先（`scripts/sf-me`），Python 3.11+ **stdlib only**。
- 一个文件搞定：`~/.sciforge/me/me.md`。
- TOML front-matter 存结构化字段，Markdown 正文存自由反思。

## 快速上手

```bash
# 1. 初始化档案（在 ~/.sciforge/me/me.md 生成骨架文件）
scripts/sf-me init

# 2. 打开来填内容（用 $EDITOR）
scripts/sf-me edit

# 3. 查看现状
scripts/sf-me show           # 人读表格
scripts/sf-me show --json    # 机器读 JSON
```

`init` 生成的骨架里每个 section 都有一条**注释掉的示例条目**，你
第一次打开就知道怎么填。

## 五个 section = 选题四问

| 选题问 | Section |
|---|---|
| **能做吗？**（会不会这套方法 / 会什么技能）| `skill` |
| **做得成吗？**（有没有仪器、算力、数据）| `equipment`, `compute` |
| **想做吗？**（研究品位、风险偏好）| `preference` |
| **做过什么？**（差异化优势、自然接续方向）| `history` |

每个 section 是一个 TOML 数组，每条目**只需填两个字段**：

```toml
[[skill]]
name  = "pytorch-distributed"   # slug，永不重命名
short = "用过 DDP 训 7B 模型"    # 一句话说明
```

想填更多就填（`level`、`updated`、`evidence`、`tags`……都可以），
`sf-me` 不校验、不强制——极简 schema 是有意的。

## 文件结构

`me.md` 用 Hugo 风格的 `+++` 分隔 TOML front-matter：

```markdown
+++
[[skill]]
name  = "pytorch-distributed"
short = "用过 DDP 训 7B 模型"

[[compute]]
name  = "gpu-a100-cluster"
short = "8×A100 80GB，组里共享"
+++

# Notes

自由 Markdown 区。可以写：
- 跨条目的整体反思（我这两年的方向调整）
- 职业阶段的思考
- 品位偏好的长版描述
- 未来想做但还没决定的方向

正文**不参与** `--json` 输出，纯粹是给 LLM 的补充语料，
`sf-fit-check` 之类的下游可以再决定要不要读它。
```

## 目录

```
~/.sciforge/me/
└── me.md         # 唯一的文件；authoritative on disk
```

没有数据库，没有缓存，没有索引。每次 `sf-me show` 都是重新读文件、
重新 parse。

## 命令一览

| 命令 | 作用 |
|---|---|
| `sf-me init` | 生成骨架文件（已存在则 exit 4） |
| `sf-me init --force` | 强制覆盖（destructive，走 ADR-0004） |
| `sf-me show` | 人读表格（按 section 分组、带计数） |
| `sf-me show self` | 同上（`self` 是唯一的 id） |
| `sf-me show --json` | ADR-0006 契约输出 |
| `sf-me edit` | 用 `$EDITOR` 打开 `me.md` |

**v1 不提供** `add` / `remove` / `list` / `summary`——加/删条目
靠手工编辑。`sf-me` 就是一份「读取契约 + 编辑器入口」；写入是低频、
需要深思熟虑的动作，值得让用户在编辑器里做。

## 存储位置

默认 `~/.sciforge/me/me.md`（**用户级**，跨项目共享）。这和
`sf-lit` 默认走项目级（`./library`）**有意不同**——因为「知己」是
研究者本人的属性，不该跟着某个 repo 走。

想改路径就写 `[me]` section 到 SciForge config：

```toml
[me]
dir = "/some/other/place"
```

Config 查找顺序：`$SCIFORGE_CONFIG` → `./.sciforge.toml` →
`~/.config/sciforge/config.toml` → 内建默认。见
[`references/config.md`](references/config.md)。

## 跨 skill 引用

这个 skill 拥有 URI 命名空间 `sciforge://me/self`——一个实体、一个
id。下游 skill 通过：

```bash
sf-me show --json
```

拿到你的档案，然后自己决定怎么用。典型的未来 pipeline：

```bash
# 假设有 sf-fit-check
sf-me show --json | sf-fit-check "训练一个 175B 语言模型"
# → 输出：算力不足；skill 里的 pytorch-distributed 是加分项
```

## 测试

```bash
pip install pytest
pytest tests/
```

测试用 subprocess 跑真实 CLI，通过 `SCIFORGE_CONFIG` 把存储指向
`tmp_path`，从不污染你的真实 `~/.sciforge/me/`。

## 设计边界（不做什么）

- **不打分**——`sf-me` 是被动档案，不告诉你「这个选题该不该做」
- **不写入**（v1）——手工编辑 Markdown，或用 `sf-me edit` 打开
- **不校验可选字段**——只强制 `name` + `short`，其它随便加
- **不做历史 / 审计**——想看 diff 请自行 `git init` 你的 `~/.sciforge/me/`
- **不做多机同步**——一台机器一份文件，跨机同步是 dotfiles 的事
- **不做选题顾问**——`sf-fit-check` 是未来的 micro skill，不在这里

## 相关文档

- [SKILL.md](SKILL.md) — 给 agent 读的调用契约
- [references/config.md](references/config.md) — 配置键
- [references/schema.md](references/schema.md) — TOML front-matter 的字段说明
