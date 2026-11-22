# 学者作品自助抓取工具（Self-serve）

> 把全量抓取的额度与磁盘成本交给客户：客户自己申请 OpenAlex key、自己抓取、数据与额度都归自己。本工具只固定「抓取方式与路径」，不代跑、不代付。

## 一、这是什么

一个跑在**客户自己电脑上**的本地网页工具，用浏览器打开后：

1. 填入自己的 OpenAlex API key（免费、每天 $1 额度）
2. 选择名单：预置 105,027 人高被引名单选子集，或粘贴/上传自己的姓名名单
3. 点「开始抓取」，实时看进度、请求数、作品数、额度
4. 抓完一键导出 `works.jsonl`（作品）、`candidates.jsonl`（身份候选）、`requests.jsonl`（请求留痕）、`checkpoint.json`（断点）

## 二、客户三步上手

### 1. 申请 API key（2 分钟）

- 打开 https://openalex.org/signup 注册（邮箱即可）
- 打开 https://openalex.org/settings/api-key 复制 key
- 到邮箱点击激活链接（否则报 `403 key_not_activated`）

免费额度：**$1/天 ≈ 1 万次检索请求**，30 请求/秒。换邮箱再申请 = 独立 $1/天。

### 2. 安装依赖

只需 Python 3.9+ 和两个库：

```bash
pip install requests openpyxl
```

### 3. 启动并抓取

```bash
cd pipeline
python fetch_server.py
```

浏览器打开 **http://127.0.0.1:8765**，按页面四步走即可。

## 三、抓取方式与路径（固定的口径）

| 项 | 口径 |
|---|---|
| 数据源 | OpenAlex API（`api.openalex.org`），key 走 `Authorization: Bearer` 头，不出现在 URL/日志 |
| 第一步 WP1 | 按姓名在 `/authors?search=` 检索候选，每人取 top-5，**不自动定案** |
| 第二步 WP2 | 对候选用 `/works?filter=authorships.author.id:X` + cursor 翻页抓全部作品 |
| 翻页上限 | 单作者最多 500 页；单页 100 条 |
| 断点续跑 | `checkpoint.json` 按 src_id 记录 wp1/wp2 完成态，重跑只补未完成者 |
| 失败安全 | 额度耗尽(429 daily_budget)或请求失败时**停在当前学者**，不写半截数据、不误标完成 |
| 限速 | 默认 8 req/s（`OA_RATE` 可调），429 指数退避 |
| 留痕 | 每次请求写 `requests.jsonl`（URL/时间/HTTP/字节/sha256），**不含 key** |
| 编号 | 对外序号从 1 起（= `source_row`）；内部 `src_id` = Excel 行号（从 2 起，仅作主键与对照） |

## 四、成本估算（给客户的心理预期）

按前 1,000 名高被引学者实测强度外推（后段递减，以下为上限）：

| 规模 | 检索请求 | 免费额度(1 key) | 落盘 |
|---|---:|---:|---:|
| 100 人 | ~1,000 | <1 天 | ~0.9 GB |
| 1,000 人 | ~1.1 万 | ~1 天 | ~8.6 GB |
| 全量 10.5 万 | ~113 万 | ~57 天（可多 key 轮换） | ~230 GB |

**建议客户**：先抓 100 人看效果 → 确认口径 → 再决定是否跑全量、用几个 key、配多大磁盘。

## 五、数据去向

- 落盘目录：`pipeline/data/self_serve/`（客户本机，不经过我们）
- key 只存内存（除非勾选"保存到本机"），写进 `.self_serve_key`，**不写请求留痕、不写日志**
- 抓到的原始数据（works/candidates/requests）客户自行保留，也可交回我们跑 WP3–WP16 落库分析

## 六、边界

- 本工具只做「抓取」，不做消歧定案、事件引擎、DID 分析（那些在 WP3–WP16，需要 PostgreSQL）
- 预置名单为 Elsevier 2017 高被引名单，版权与使用范围由客户自行确认
