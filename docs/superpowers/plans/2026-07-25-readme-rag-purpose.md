# README RAG Purpose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 README 的项目介绍中加入后续使用 Markdown 构建 RAG 个体知识库的建议，同时明确 RAG 不属于本项目当前功能。

**Architecture:** 只修改 `README.md` 的介绍区域，在首段之后新增“项目目的”章节。该章节说明 PDF 转 Markdown、RAG 输入、来源可追溯性和功能边界，不调整程序代码、配置或现有工作流。

**Tech Stack:** Markdown、Git

---

### Task 1: 增加项目目的与 RAG 建议

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 验证目标章节尚不存在**

Run:

```bash
rg -n '^## 项目目的$|个体知识库' README.md
```

Expected: 无匹配结果，命令退出码为 1。

- [ ] **Step 2: 在首段之后加入项目目的章节**

在 README 第一段与“项目支持”之间加入：

```markdown
## 项目目的

本项目旨在把个人 PDF 文献库持续转换为结构清晰、便于程序读取的 Markdown，并尽量保留来源路径和文档边界，方便后续检索结果回溯到原始论文。

建议在转换完成后，将 Markdown 接入 RAG（检索增强生成）流程，通过文本切分、向量化和索引建立基于个人文献库的个体知识库，让 Agent 能够检索、引用并综合自己的文献资料。RAG 的切分、索引和问答系统不属于本项目当前功能范围，本项目负责为这些后续流程准备 Markdown 文档来源。
```

- [ ] **Step 3: 校验文案与 Markdown 格式**

Run:

```bash
git diff --check
rg -n '^## 项目目的$|RAG（检索增强生成）|个体知识库|不属于本项目当前功能范围' README.md
```

Expected: `git diff --check` 无输出；四项关键文案均匹配。

- [ ] **Step 4: 确认变更范围**

Run:

```bash
git diff --stat
git diff -- README.md
```

Expected: 除实施计划外，功能变更仅涉及 `README.md` 的新增项目目的章节，现有配置说明不变。

- [ ] **Step 5: 提交并推送**

Run:

```bash
git add README.md docs/superpowers/plans/2026-07-25-readme-rag-purpose.md
git commit -m "docs: explain downstream RAG purpose"
git push origin main
```

Expected: 提交成功并将 `main` 推送到 GitHub。
