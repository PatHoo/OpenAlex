# 全量跑批 Pipeline（1,000 人试跑）

## 重要：polite pool 已废除（2026-02 起）

你之前问的「API 邮箱 / polite pool」已经过时。OpenAlex 官方公告：

- **2026-02 起 `mailto=` 参数被忽略**，polite pool 取消。
- **所有真实调用必须带 API key**（免费），否则只有匿名共享额度。
- 无 key：与同 IP 所有人共享 **$0.10/天**，10 req/s，**cursor 翻页只能拿第一页**。
- 免费 key：**$1/天**，10 req/s，支持 cursor 翻页；新账号前 $1 按 $0.10/5min 限速（约 50 分钟稳定用完）。
- 计费：list 类请求（`/authors?search=`、`/works?filter=`）约 **$0.0001/次**；单实体查询免费。

## 怎么拿免费 key（二选一）

**方式 A：自己注册（最稳）**

1. 打开 https://openalex.org/signup，邮箱注册（约 30 秒）。
2. 进 https://openalex.org/settings/api-key 复制 key。
3. 到邮箱点激活链接（不点则 key 返回 `403 key_not_activated`）。

**方式 B：让我代你申请（你仍要点一次邮箱链接）**

把你的邮箱告诉我，我执行：

```bash
curl -X POST https://api.openalex.org/api-keys \
  -H "Content-Type: application/json" \
  -d '{"email":"你的邮箱","name":"你的姓名","agent":"WorkBuddy"}'
```

返回里带 `api_key` 和激活链接，**你点击邮件里的链接后 key 才生效**。激活后把 key 填进环境变量即可。

> 我不会把 key 写进任何代码或仓库文件。脚本只从 `OPENALEX_API_KEY` 环境变量读取。

## 设置 key

Windows PowerShell（当前会话）：

```powershell
$env:OPENALEX_API_KEY = '你的key'
```

WSL Bash：

```bash
export OPENALEX_API_KEY='你的key'
```

## 编号口径（与已入库的三人 POC 对齐，勿改）

- `source_row` = 数据序号（不含表头，从 1 起）
- `src_id` = Excel 实际行号（含表头，从 2 起）= `source_row + 1`
- 已验证：Becke 17/16、Perdew 18/17、Newman 29/28 与种子库完全一致；
  子集导入用 `ON CONFLICT (source_row) DO UPDATE`，重复执行仍为 1,000 行。

## 运行（Windows Python 3.13，依赖 requests / openpyxl 已装）

```bash
cd C:/Users/quith/Desktop/OpenAlex/pipeline

# WP0：读 105,026 行名单，生成 1,000 人子集与可导入 SQL
python wp0_import.py

# 先用 3 人做极小样本验证（无 key 也能跑，只翻第一页）
python wp12_fetch.py --limit 3 --stage all --max-works 5

# 有 key 后跑完整 1,000 人试跑
python wp12_fetch.py --limit 1000 --stage all
```

断点续跑：重跑同一命令会读 `pipeline/data/batch_001/checkpoint.json`，已完成的学者自动跳过。

## 输出文件

| 文件 | 内容 |
|---|---|
| `data/batch_001/source_scholar.jsonl` | 105,026 行原始名单（含 3 字母国家码在 raw_payload） |
| `data/batch_001/subset.jsonl` | 前 1,000 人试跑子集 |
| `data/batch_001/subset_source.sql` | 幂等 SQL，把 1,000 人导入 `source_scholar` 表 |
| `data/batch_001/candidates.jsonl` | 每人 top-5 作者候选（WP1，未自动定案） |
| `data/batch_001/works.jsonl` | 每作品一行，含作者机构与国家码（WP2） |
| `data/batch_001/checkpoint.json` | 断点续跑状态 |
| `data/batch_001/requests.jsonl` | 每次请求的 URL/时间/HTTP/sha256/字节数，字段对齐 `api_request` 表 |
| `data/batch_001/requests_import.sql` | 把成功请求导入 `api_request`（request_id 1001+，幂等） |

## 已完成的实机验证（2026-11-22）

- `wp0_import.py`：读取 105,026 数据行；生成 1,000 人子集；导入原生 PG17 后 `source_scholar` = 1,000 行，
  重复导入仍为 1,000 行（幂等），已入库三人的 src_id/source_row 与 POC 种子完全一致。
- 3 人极小样本（src_id 2/3/4，匿名额度）：WP1 各得 3/4/3 个作者候选；WP2 各抓 5 篇作品，
  作品记录含机构名与国家码（如 US/CH）；6 条请求留痕全部 HTTP 200，导入 `api_request` 得 6 行。
- 断点续跑：再次执行同一命令，已完成的学者全部跳过，不产生新请求。
- 修正过的两个 API 细节：filter 值必须用裸 ID（`A5109160404`，不是完整 URL）；
  `select` 不能点选 `authorships.author.id` 等嵌套字段，只能整块取 `authorships`。

## 试跑额度粗估

- 1,000 人 × (1 次作者检索 + 平均 3–5 页作品) ≈ 4,000–6,000 次 list 请求 ≈ **$0.4–0.6**，免费 key 一天预算够。
- 全量 105,026 人 ≈ **$40–60/天预算需求**，免费 key 需分多天跑，或升级 OpenAlex 付费计划。
- 真实作品数取决于每位学者的发文量；跑完 3 人样本后可精确外推。

## 设计边界

- WP1 只产出候选，**不自动接受匹配**——消歧评分（姓名/机构/学科/时间/论文/合作者）需甲方确认口径后再开 WP1b。
- WP2 只对 top-1 候选抓作品作为试跑；正式跑批应在消歧定案后按确认的 author_id 抓。
- `cntry` 列（CHAR(2)）暂留 NULL，ISO3 原值保存在 `raw_payload.cntry_iso3`，不猜测转换。
- 所有请求留痕按 `api_request` 表字段记录，key 不写入任何文件。
