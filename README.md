# PaddleOCR Markdown Sync

使用 **PaddleOCR 在线 API**，把本地 PDF 文献库增量转换成适合 Agent 直接读取的 Markdown。OCR 计算发生在 PaddleOCR 云端并消耗在线 API 的每日额度，本项目不会在电脑上安装或运行本地 OCR 模型。

## 项目目的

本项目旨在把个人 PDF 文献库持续转换为结构清晰、便于程序读取的 Markdown，并尽量保留来源路径和文档边界，方便后续检索结果回溯到原始论文。

建议在转换完成后，将 Markdown 接入 RAG（检索增强生成）流程，通过文本切分、向量化和索引建立基于个人文献库的个体知识库，让 Agent 能够检索、引用并综合自己的文献资料。RAG 的切分、索引和问答系统不属于本项目当前功能范围，本项目负责为这些后续流程准备 Markdown 文档来源。

项目支持：

- macOS 和 Windows 的完整转换、增量同步及每日定时任务。
- Linux 的手动转换和同步。
- 递归扫描 PDF 根目录下的所有子文件夹。
- 中断恢复、失败记录、每日页数预算及安全的图片下载路径。
- 单篇 PDF 和整个文献库两种工作方式。

## 重要行为

### 扫描所有子文件夹

`paddleocr-md sync` 会递归扫描配置目录下任意层级的所有子文件夹，并识别 `.pdf`、`.PDF` 等大小写形式。

### 不进行 PDF 内容去重

本项目不进行 PDF 内容去重，也不会尝试判断两个名称不同的 PDF 是否内容相同。每个来源路径都被视为独立文档；相同内容位于两个不同路径时会分别提交转换。

只有“同一路径、已经转换成功、文件大小和修改时间未变化”的 PDF 才会被增量跳过。

### 正文和 SI 保持独立

正文和 SI、ESI、Supporting Information、补充材料及其他附件全部作为独立 PDF 转换到独立 Markdown 目录。本项目不判断或合并它们之间的关系。

后续接入 RAG 时，默认应把每个输出目录视为独立来源。若需要关联正文与 SI，请在索引阶段使用人工映射或经过确认的 DOI、标题等元数据处理，不要依赖本转换工具猜测。

## 安装

需要 Python 3.10 或更高版本。

### 从 GitHub 安装

macOS Terminal：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install "git+https://github.com/HenryYu-1022/paddleocr-markdown-sync.git"
```

Windows PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install "git+https://github.com/HenryYu-1022/paddleocr-markdown-sync.git"
```

验证安装：

```bash
paddleocr-md --help
```

## 第一次配置

先准备 PDF 根目录和 Markdown 输出目录，然后运行：

macOS：

```bash
paddleocr-md init \
  --pdf-root "/path/to/your/PDF-library" \
  --markdown-root "/path/to/your/markdown-output"
```

Windows PowerShell：

```powershell
paddleocr-md init `
  --pdf-root "D:\Papers" `
  --markdown-root "D:\PaddleOCR-Markdown"
```

该命令会创建：

- `config.toml`：PDF 目录、输出目录、每日页数上限等普通设置。
- `credentials.env`：只保存 PaddleOCR API Token。

## API Token 填到哪里

拿到 PaddleOCR API Token 后，不需要修改 Python 代码。

先运行下面的命令打印本机的准确路径：

```bash
paddleocr-md config path
```

默认凭据文件位置：

- macOS：`~/Library/Application Support/paddleocr-markdown-sync/credentials.env`
- Windows：`%APPDATA%\paddleocr-markdown-sync\credentials.env`
- Linux：通常为 `~/.config/paddleocr-markdown-sync/credentials.env`

打开 `credentials.env`，把 Token 粘贴到等号后面：

```dotenv
PADDLE_OCR_TOKEN=把你的Token粘贴到这里
```

保存后执行：

```bash
paddleocr-md doctor
```

`doctor` 会确认 Token 已被读取，但不会显示 Token 内容，也不会提交 PDF 或消耗转换页数。Token 是否仍然有效，最终以实际提交 PDF 时 API 是否返回 401/403 为准。

### 临时使用环境变量

环境变量的优先级高于 `credentials.env`。

macOS zsh：

```bash
export PADDLE_OCR_TOKEN="把你的Token粘贴到这里"
paddleocr-md doctor
```

Windows PowerShell：

```powershell
$env:PADDLE_OCR_TOKEN = "把你的Token粘贴到这里"
paddleocr-md doctor
```

环境变量只对当前终端有效。每日定时任务建议使用 `credentials.env`。

## 普通配置

运行 `paddleocr-md config path` 找到 `config.toml`。格式如下：

```toml
[library]
pdf_root = "/path/to/your/PDF-library"
markdown_root = "/path/to/your/markdown-output"

[sync]
daily_page_limit = 19000
poll_interval = 5.0
http_timeout = 120
max_retries = 3

[api]
job_url = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
model = "PaddleOCR-VL-1.6"
```

Windows 路径由 `init` 自动正确转义，建议优先使用命令生成配置。

默认 `daily_page_limit = 19000`，为官方每日 20,000 页额度保留余量。本工具只能记录自己当天提交的页数；如果同一个 Token 被其他程序使用，本工具无法得知那部分消耗。

## 使用方法

### 检查环境

```bash
paddleocr-md doctor
```

### 查看状态

```bash
paddleocr-md status
```

### 只查看同步计划，不调用 API

```bash
paddleocr-md sync --dry-run
```

### 同步整个 PDF 文献库

```bash
paddleocr-md sync
```

### 转换单个 PDF

```bash
paddleocr-md convert "/path/to/paper.pdf"
```

### 临时修改当日页数上限

```bash
paddleocr-md sync --daily-page-limit 5000
```

## 每日自动同步

### macOS

每天 03:00 同步：

```bash
paddleocr-md schedule install --time 03:00
paddleocr-md schedule status
```

卸载：

```bash
paddleocr-md schedule uninstall
```

macOS 使用当前用户的 `launchd` 任务。

### Windows

以当前用户身份打开 PowerShell：

```powershell
paddleocr-md schedule install --time 03:00
paddleocr-md schedule status
```

卸载：

```powershell
paddleocr-md schedule uninstall
```

Windows 使用任务计划程序，并在配置目录生成 `run-sync.ps1`。

日志保存在配置目录的 `logs/` 下。

## 输出结构

每个 PDF 都有独立目录：

```text
markdown-output/
└── 论文名-路径哈希/
    ├── metadata.json
    ├── result.jsonl
    ├── page_0001.md
    ├── page_0002.md
    ├── images/
    └── output_images/
```

`metadata.json` 记录来源路径、状态、文件指纹、PaddleOCR 任务 ID、页数和错误。程序在提交后被关闭时，下一次同步会继续轮询已有任务，不会重复提交。

## 让 Agent 直接读取 Markdown

同步完成后，可以把 Markdown 输出根目录交给 Agent。例如：

```text
请递归检索 <markdown-output> 下所有 page_*.md。
先根据文件名和关键词定位相关论文，只读取命中的页面；
回答时同时给出 metadata.json 中的 source_path、Markdown 路径和页码。
不要把正文与 SI 自动视为同一个文档。
```

文献较少时可以直接检索文件。文献很多时可另建 RAG，但建议每个 chunk 至少保留：

- `document_id`
- `source_path`
- `page`
- Markdown 文件路径

正文/SI 分组属于 RAG 或资料整理层，不属于本项目。

## 中断与错误处理

- **401/403**：Token 无效或权限不足。运行 `paddleocr-md config path`，检查 `credentials.env`。
- **429**：API 限流。程序尊重 `Retry-After` 并有限重试。
- **5xx 或网络超时**：程序执行有限次数的指数退避重试。
- **远程解析失败**：单篇 PDF 写入 `failed` 状态，其余 PDF 继续。
- **程序中断**：任务 ID 已写入 `metadata.json`，下次同步恢复轮询。
- **页数无法读取**：为防止绕过额度，该 PDF 暂不提交，并在计划中计为“页数未知”。

Token 不会写入日志、Markdown、元数据或 Git 提交。

## 开发与测试

```bash
python -m pip install -e ".[test]"
python -m pytest -q
python -m build
```

测试使用模拟 HTTP 响应，不调用真实 PaddleOCR API，也不消耗每日额度。

## License

[MIT](LICENSE)
