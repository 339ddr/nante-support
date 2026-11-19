# 南特金属 · 智能客服（可配置大模型版）

一个 **RAG + Agent** 的汽配智能客服示例。首次打开网页会弹出配置窗，
填入你自己的 OpenAI 兼容接口（`base_url` + `API Key`）即可启用大模型回答；
不填也能用——系统会自动走本地混合检索。

- **密钥不进代码库**：只存在浏览器 `localStorage`，随请求转发，服务端不落盘、不写日志
- **不配也能跑**：没填模型凭据时，用本地检索 + 模板化回答，功能完整
- **零数据库依赖**：默认不需要 MySQL，会话存内存；想持久化再开
- **开箱即用**：无需下载任何向量模型，索引由字符 n-gram 哈希向量构建

---

## 快速开始

环境：Python 3.11+（推荐 3.12），Windows / macOS / Linux 均可。

```bash
# 1. 创建虚拟环境
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

# 2. 安装依赖(一定要哦)
pip install -r requirements.txt

# 3. 复制环境变量模板（不改也能跑）
copy .env.example .env      # Windows
cp .env.example .env        # macOS / Linux

# 4. 启动
python run.py
```

打开 <http://127.0.0.1:8000>，首次进入会弹出 **模型设置** 窗口。

- 填好 `base_url` 和 `API Key` → 点「测试连接」确认可用 → 「保存并使用」
- 或者直接点「先不配置，使用本地检索模式」

之后想换 Key，点右上角的 **设置** 按钮随时改，或点「清除」移除本机保存的密钥。

---

## 配置你自己的模型

任何 **OpenAI 兼容** 接口都可以，常见填法：

| 服务 | base_url | 模型名 |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 阿里通义 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Moonshot | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| 本地 Ollama | `http://127.0.0.1:11434/v1` | `qwen2.5` |

> Ollama 之类的本地服务通常不校验 key，随便填一个非空值即可（例如 `local`）。

---

## 密钥是怎么处理的

这是本项目最重要的设计，请务必了解：

```
你填 key → 存进浏览器 localStorage
         → 每次对话随请求体发给本项目的后端
         → 后端用它调用你指定的 base_url，用完即弃
```

具体保证：

- **不写入磁盘**：服务端不写 `.env`、不写数据库、不写日志
- **不回显**：接口返回和错误提示都不会包含完整 key
- **不进代码库**：`.gitignore` 已排除 `.env`，仓库里没有任何真实密钥
- **换人也能用**：别人 clone 后填自己的 key，你的代码里没有任何凭据

⚠️ **一个必须知道的现实**：key 通过 HTTP 请求体传输，如果部署在 **http://** 上，
同一网络内的抓包工具可以看到它。**本地自用没问题**；若部署到公网给他人使用，
请务必配置 **HTTPS**。

---

## 换成你自己的业务

这个项目用的是一套「汽配店铺」示例知识库，你可以替换成任何领域的文档：

```
data/knowledge/
├── raw/          # 原始 docx（可选，留档用）
└── md/           # 实际被索引的 Markdown，改这里
    ├── products.md
    ├── returns.md
    └── faq.md
```

改动步骤：

1. 替换 `data/knowledge/md/` 下的 Markdown（格式参考现有文件）
2. 修改 `app/config.py` 里的 `SHOP`（店名、电话、微信、邮箱）
3. 删除旧索引并重启：

```bash
python scripts/build_index.py
```

> 索引产物（`data/chunks.jsonl`、`data/idf.json`、`data/chroma/`）已加入 `.gitignore`，
> 首次启动会自动重建，无需提交到仓库。

知识库的文本越结构化（条目清晰、字段统一），检索效果越好。

---

## 功能

| 模块 | 说明 |
| --- | --- |
| 商品咨询 | 按商品名、品牌、车型、分类、预算查询价格与参数 |
| 退货退款 | 区分七天无理由 / 质量问题 / 发错货，支持满减分摊测算 |
| 保修查询 | 分档保修规则、易损件与例外说明 |
| 安装指引 | 难度分级、所需工具与安全提示 |
| 技术常识 | OEM 编号、机油标号、更换周期等 |
| 转人工 | 投诉或超范围问题给出客服联系方式 |
| 引用溯源 | 每条回答附知识库来源，可点击查看原文 |

---

## 技术架构

```text
浏览器（弹窗填 base_url + key，存 localStorage）
   │  POST /api/chat  { message, base_url, api_key, model }
   ▼
FastAPI ──► LangGraph Agent
              ├─ analyze    意图识别 + 实体抽取
              ├─ retrieve   Chroma 向量召回 + BM25 + 覆盖率融合
              ├─ tools      商品检索 / 退款测算 / 保修 / 安装 / 周期
              └─ generate   LLM 生成（无凭据时走本地模板回答）
                     │
                     ▼
              引用来源 + 商品卡片 + 工具轨迹
```

**检索层**：字符 1~3 gram + IDF 哈希向量（512 维，无需下载模型）写入 Chroma；
查询时融合向量相似度、BM25 与实义词二元组覆盖率。

**生成层**：凭据由请求带入；调用失败自动降级为本地回答，响应里 `mode=local-fallback`。

---

## 目录结构

```text
app/
├── api.py             # FastAPI 路由（含 /api/llm/test 凭据校验）
├── config.py          # 路径 / 检索参数 / 店铺信息（不含密钥）
├── llm.py             # 凭据封装：按请求传入，内存缓存客户端
├── db.py              # MySQL 持久化（可选，失败自动降级）
├── agent/
│   ├── graph.py       # LangGraph 状态机
│   ├── tools.py       # 业务工具与 JSON Schema
│   ├── composer.py    # 本地检索式回答生成
│   └── prompts.py     # 系统提示词
├── rag/               # 文档解析 / 向量化 / 混合检索
└── web/               # 前端（含模型配置弹窗）
data/knowledge/md/     # 知识库 Markdown（改这里）
scripts/build_index.py # 重建索引
tests/test_api.py      # 端到端测试
run.py                 # 启动入口
```

---

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/chat` | 对话，可带 `base_url` / `api_key` / `model` |
| POST | `/api/llm/test` | 校验凭据是否可用（供弹窗「测试连接」） |
| GET | `/api/llm/defaults` | 弹窗预填默认值（不含密钥） |
| GET | `/api/health` | 健康检查、向量数量 |
| GET | `/api/meta` | 店铺信息、分类统计、快捷问题 |
| GET | `/api/products` | 商品列表 |
| GET | `/api/products/{id}` | 商品详情 |
| GET | `/api/session/{id}` | 会话历史 |
| POST | `/api/session/reset` | 清空会话 |
| POST | `/api/feedback` | 回答评价 |

`/api/chat` 未携带凭据时 `mode` 为 `local`；凭据无效时会降级为 `local-fallback` 而不是报错。

---

## 可选：启用 MySQL 持久化

默认关闭。想让商品、会话、消息持久化到数据库：

1. 在 `.env` 里设置 `MYSQL_ENABLED=1` 并填好连接信息
2. 重启服务（会自动建库建表并同步商品）

连接失败不会阻塞启动，会自动降级为内存 + `data/feedback.jsonl`。

---

## 测试

```bash
python -m pytest tests -q
```

覆盖健康检查、索引数量、商品查询、退款测算、保修规则、多轮指代、
转人工、知识库兜底，以及**凭据归一化、密钥脱敏、无效 Key 降级**等场景。

---

## 备注

- 项目默认只监听 `127.0.0.1`；如需局域网访问，设置 `HOST=0.0.0.0` 后启动。
- 示例知识库中的商品价格与政策条款仅作演示，请替换为你自己的业务数据。
- 未配置 `MYSQL_ENABLED` 时，重启服务会丢失会话历史，属预期行为。

## License

MIT
