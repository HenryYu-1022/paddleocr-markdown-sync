# PaddleOCR Markdown Sync 设计

## 目标

创建一个独立、公开、可跨平台运行的 Python 项目，将本地 PDF 文献库通过 PaddleOCR 在线 API 增量转换为适合 Agent 直接读取的 Markdown 文件库。

项目不运行本地 OCR 模型，不包含 RAG 或本地大模型。它只负责扫描 PDF、控制每日额度、提交并轮询 PaddleOCR 云端任务、下载转换结果、维护状态，以及在 macOS 和 Windows 上安装每日定时同步。

公开仓库名为 `paddleocr-markdown-sync`，使用 MIT License。

## 支持平台

- Python 3.10 及以上。
- macOS：完整支持转换、同步和 `launchd` 用户级定时任务。
- Windows：完整支持转换、同步和当前用户的任务计划程序。
- Linux：支持转换和同步；第一版不提供定时任务安装器。
- GitHub Actions 在 Windows、macOS 和 Linux 上运行测试。

## 核心命令

安装后提供 `paddleocr-md` 命令：

```text
paddleocr-md init
paddleocr-md convert <pdf>
paddleocr-md sync
paddleocr-md status
paddleocr-md doctor
paddleocr-md config path
paddleocr-md schedule install --time 03:00
paddleocr-md schedule status
paddleocr-md schedule uninstall
```

`init` 创建用户配置目录和普通配置文件，但不会将 Token 写入 Git 仓库。`convert` 转换单个 PDF。`sync` 扫描配置的文献库并只处理新增或变化的 PDF。`status` 汇总转换状态。`doctor` 检查 Python、配置、目录、Token 和 API 端点可达性。`config path` 打印准确的配置与凭据路径。

## 配置与凭据

普通配置保存在平台用户配置目录中的 `config.toml`：

- PDF 来源目录。
- Markdown 输出目录。
- 每日页数上限，默认 19,000。
- API 轮询间隔。
- HTTP 超时与最大重试次数。

PaddleOCR Token 单独保存在同一用户配置目录的 `credentials.env`：

```dotenv
PADDLE_OCR_TOKEN=把已经申请到的Token粘贴到这里
```

默认配置目录：

- macOS：`~/Library/Application Support/paddleocr-markdown-sync/`
- Windows：`%APPDATA%\paddleocr-markdown-sync\`
- Linux：遵循 XDG 配置目录。

环境变量 `PADDLE_OCR_TOKEN` 的优先级高于 `credentials.env`。命令行显式指定的普通配置项优先于 `config.toml`。真实凭据文件、用户路径、日志、PDF 和转换结果都不得进入公开仓库。

README 以中文为主，必须明确说明用户拿到 API Token 后应粘贴到哪个文件、如何打印该文件路径、如何在 macOS Terminal 和 Windows PowerShell 中设置临时环境变量，以及如何用 `doctor` 检查配置。

本机部署时，可以将旧工具私有 `.env` 中的 Token 安全迁移到新项目的用户级 `credentials.env`。迁移过程不得在终端、日志、提交或回复中显示 Token。

## 数据流

```text
本地 PDF 文献库
    -> 递归扫描、PDF 指纹、页数估算、每日预算
    -> PaddleOCR 在线 API 提交与轮询
    -> 下载原始 JSONL、Markdown 和图片
    -> 原子写入本地 Markdown 文献库
    -> Agent 递归检索并读取 page_*.md
```

第一版按顺序处理任务，避免重复提交和不必要的额度消耗。系统不尝试推断 PaddleOCR 账户被其他程序消耗的页数，只记录本工具当日已经规划和提交的页数，并在文档中明确这一限制。

## 增量识别与恢复

扫描必须递归遍历 PDF 根目录下任意层级的所有子文件夹，并匹配扩展名大小写不同的 `.pdf` 文件。扫描深度不设人为上限，稳定排序使用相对于 PDF 根目录的完整路径。

项目不做跨路径或跨文件名的 PDF 内容去重，也不尝试判断两个名称不同的 PDF 是否内容相同。每个来源相对路径都被视为独立文档，因此相同内容出现在两个不同路径时会分别提交转换。这样避免不可靠的重复判定误删或漏掉用户有意保留的副本。

每个 PDF 使用来源相对路径的稳定哈希生成文档目录名，并保存来源路径、文件大小和修改时间。只有同一来源路径已经成功且指纹未变化时才被跳过；同一路径文件被替换或更新后会重新处理。文件被移动或重命名后按新的来源路径视为新文档，旧输出不会被自动删除。

`metadata.json` 至少包含：

- 文档 ID 和来源路径。
- 文件指纹与估算页数。
- `pending`、`submitted`、`running`、`done` 或 `failed` 状态。
- PaddleOCR 任务 ID。
- 创建、提交、完成与更新时间。
- 使用的云端模型。
- 错误类别和可读错误信息。

如果程序在任务提交后退出，下次同步优先使用已有任务 ID 恢复轮询，不重复提交 PDF。失败任务可在后续同步中有限次数重试。

## 输出格式

每篇 PDF 使用独立目录：

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

目录结构与旧版 PaddleOCR Markdown 输出兼容。Markdown 图片路径保持相对路径。API 返回的绝对路径或包含 `..` 的路径必须被拒绝或规范化，不能写出当前论文目录。

下载内容先进入同一输出目录下的临时区域。只有原始 JSONL、全部 Markdown 和可下载图片完成后才更新最终状态，避免 Agent 读取半成品。

## 每日额度

默认每日页数上限为 19,000，给官方每日 20,000 页额度保留余量。使用 `pypdf` 估算 PDF 页数，按稳定顺序选择当日任务。加入下一个 PDF 会超过预算时停止规划，并把剩余文件留给下一次同步。

本地额度记录按配置时区的自然日保存。`--daily-page-limit` 可临时覆盖默认值。页面无法估算时，文件被标为需要谨慎处理；不得静默绕过预算。

## API 客户端与错误处理

默认 PaddleOCR 任务端点为 `https://paddleocr.aistudio-app.com/api/v2/ocr/jobs`，默认模型为 `PaddleOCR-VL-1.6`。

- 401 和 403：立即停止新任务，提示检查 `credentials.env`。
- 429：遵循服务端 `Retry-After`，在有限重试后保留可恢复状态。
- 网络超时和 5xx：使用有上限的指数退避。
- 无效 JSON 或缺失任务 ID：记录协议错误，不猜测结果。
- 单篇任务失败：写入该文档元数据，保留其他成功结果。
- Ctrl-C 或进程退出：已经提交的任务 ID 必须保留，下一次同步可恢复。
- 日志不得写入 Authorization 头、Token 或完整凭据文件内容。

## 定时任务

`schedule install --time HH:MM` 使用当前 Python 解释器、绝对配置路径和 `sync` 子命令创建每日任务。

- macOS：生成用户级 plist 并通过 `launchctl` 安装。
- Windows：生成 PowerShell 启动脚本，并通过 `schtasks.exe` 注册当前用户任务。
- `schedule status` 检查任务是否存在并显示计划时间。
- `schedule uninstall` 只删除本项目创建的精确任务，不影响其他用户任务。
- 运行日志写到用户配置目录的 `logs/`。

平台命令的生成逻辑必须可在测试中验证；CI 不实际修改宿主机的任务计划。

## Agent 工作流

README 给出完整示例：

1. 使用 `paddleocr-md sync` 更新 Markdown 库。
2. 使用 `paddleocr-md status` 检查失败和待处理文件。
3. 将输出目录交给 Agent。
4. 要求 Agent 先按文件名或关键词定位相关论文，再读取命中的 `page_*.md`，并引用 `metadata.json` 中的来源路径。

正文、Supporting Information、ESI、补充材料和其他附件一律作为独立 PDF 转换到独立 Markdown 目录。项目不根据文件名、目录、标题或 DOI 猜测它们之间的关系，不生成正文/SI 分级，也不物理合并 Markdown。

README 必须明确说明：后续 RAG 默认应将每个输出目录视为独立来源，并保留 `document_id`、`source_path` 和页码。若用户需要关联正文与 SI，应在独立的索引或数据整理阶段使用人工映射或经过确认的元数据实现，不属于本转换项目的职责。

项目不自动建立向量索引。`metadata.json` 和稳定输出结构为以后接入 RAG 保留接口，但不把 RAG 纳入第一版范围。

## 代码边界

```text
src/paddleocr_markdown_sync/
├── cli.py             # 命令解析与用户输出
├── config.py          # 跨平台路径、TOML 和凭据加载
├── models.py          # 配置、任务和状态数据模型
├── discovery.py       # PDF 扫描、指纹和页数预算
├── api.py             # PaddleOCR HTTP 提交、轮询和下载
├── exporter.py        # JSONL 解析、安全路径和原子落盘
├── sync.py            # 单篇转换、恢复和批量协调
└── scheduler/
    ├── __init__.py
    ├── macos.py       # launchd 定义与命令
    └── windows.py     # 任务计划定义与命令
```

各模块通过明确的数据模型交互。CLI 不直接实现 HTTP 或文件导出；API 客户端不决定扫描和额度策略；平台调度器不参与转换。

## 测试

开发采用测试驱动方式。测试覆盖：

- 跨平台配置路径和配置优先级。
- Token 缺失、读取和日志脱敏。
- PDF 任意层级递归扫描、大小写扩展名、稳定排序、指纹和变化检测。
- 不同路径或不同名称的相同内容 PDF 会分别进入转换队列。
- 每日页数预算与无法估算页数的处理。
- API 提交、轮询、恢复、401、429、5xx 和超时。
- JSONL 到 Markdown/图片的导出。
- 绝对路径和 `..` 路径穿越防护。
- 临时目录到最终输出的原子更新。
- macOS plist 和 Windows `schtasks` 参数生成。
- CLI 的成功、失败和退出码。

单元测试使用模拟 HTTP 响应，不调用真实 PaddleOCR API，也不消耗额度。GitHub Actions 在 `ubuntu-latest`、`macos-latest` 和 `windows-latest` 上运行测试和包构建。

本地验收包含：

- 完整测试套件。
- wheel/sdist 构建。
- 安装后 CLI 帮助和 `doctor`。
- 无 Token 的公开测试。
- 敏感信息扫描。

除非用户明确要求消耗额度，发布验收不提交真实 PDF 任务。

## 公开发布

创建 GitHub 公开仓库 `HenryYu-1022/paddleocr-markdown-sync`，默认分支为 `main`。首次发布直接提交完整的独立项目，不依赖原单体仓库历史。

仓库包含：

- 中文 `README.md`。
- MIT `LICENSE`。
- `.env.example` 和安全的示例配置。
- `pyproject.toml` 与控制台入口。
- GitHub Actions 测试工作流。
- 设计与实施文档。

发布前检查 Git 状态、提交范围、测试结果、构建结果和敏感信息扫描。随后推送 `main` 到新仓库。因为这是全新仓库的初始发布，不创建针对既有默认分支的 Pull Request。

## 非目标

第一版明确不包含：

- 本地 OCR 模型。
- Ollama、Qwen 或其他聊天模型。
- RAG、向量数据库或自动摘要。
- Web UI。
- Linux 定时任务安装器。
- PaddleOCR Token 申请教程。
- 多进程或高并发任务提交。
- 正文、SI、ESI 或附件的自动识别、分级和合并。

这些功能只有在独立需求确认后才扩展。
