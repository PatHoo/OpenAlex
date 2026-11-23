# OpenAlex 学者作品自助抓取工具（本地版·已存档）

> ⚠️ **本目录为旧版本地抓取方案的存档，不再是主交付。** 现行方案是纯前端网站 `web/index.html`（客户浏览器直连 OpenAlex，无需本地安装），线上以 web/ 为准。以下内容仅作历史保留与本地部署参考。
>
> （原说明）一个跑在**您自己电脑上**的本地网页工具：自己申请 OpenAlex API key、自己抓取，数据与额度都归您，本工具只固定「抓取方式与路径」，不代跑、不代付。

## 一、三步上手

### 1. 启动（自动装依赖，无需手动 pip install）

需要 Python 3.9+。首次运行会自动创建虚拟环境并安装依赖。

**Windows：** 双击 `启动自助抓取.bat`

**macOS / Linux（含 WSL）：**

```bash
bash start.sh
```

> 两个脚本都会**自动校验并创建** `.venv` 虚拟环境（缺依赖或"空壳" venv 会自动重建）、装依赖、启动服务并打开浏览器，全程无需手动 `pip install`。依赖已就绪时秒开。

浏览器打开 **http://127.0.0.1:8765**（脚本会自动打开），按页面四步走：

### 2. 申请 API key（约 2 分钟）

1. 打开 https://openalex.org/signup 注册（邮箱即可）
2. 打开 https://openalex.org/settings/api-key 复制 key
3. 到邮箱点击激活链接（否则报 `403 key_not_activated`）

免费额度：**$1/天（约 1,000 次检索请求）**，OpenAlex 限速上限 30 请求/秒、工具自限速 8/秒。换邮箱再申请 = 独立 $1/天。

### 3. 开始抓取

在页面里：

1. 粘贴 API key
2. 选择名单（预置 105,026 人高被引名单选子集，或粘贴自己的姓名名单）
3. 点「开始抓取」，实时看进度、请求数、作品数
4. 抓完一键导出

## 二、目录结构

```
deliver/
├── app.py                # 网页服务端（Flask）
├── fetch_service.py      # 抓取状态机与名单解析
├── oa_client.py          # OpenAlex API 客户端（限速/重试/留痕）
├── wp12_fetch.py         # WP1 检索 + WP2 翻页抓取逻辑
├── settings.py           # 配置（限速/超时/预置名单路径）
├── self_serve.html       # 网页前端
├── requirements.txt      # 依赖清单
├── 启动自助抓取.bat       # Windows 一键启动
├── start.sh              # macOS / Linux 一键启动
├── preset/               # 预置名单目录（本地运行时，需自行放入下列 xlsx）
│   # └─ Table-S1-career-2017.xlsx —— 105,026 名学者；原件在仓库 reference/ 下，不随分发包附带
└── data/                 # 抓取结果输出目录
```

## 三、抓取口径（固定不变）

| 项 | 口径 |
|---|---|
| 数据源 | OpenAlex API（`api.openalex.org`），key 走 `Authorization: Bearer` 头，不出现在 URL/日志 |
| 第一步 WP1 | 按姓名在 `/authors?search=` 检索候选，每人取 top-5，**不自动定案** |
| 第二步 WP2 | 对候选用 `/works?filter=authorships.author.id:X` + cursor 翻页抓全部作品 |
| 翻页上限 | 单作者最多 500 页；单页 100 条 |
| 断点续跑 | `checkpoint.json` 按 src_id 记录完成态，重跑只补未完成者 |
| 失败安全 | 额度耗尽(429 daily_budget)或请求失败时**停在当前学者**，不写半截数据、不误标完成 |
| 限速 | 默认 8 req/s，429 指数退避 |
| 留痕 | 每次请求写 `requests.jsonl`（URL/时间/HTTP/字节/sha256），**不含 key** |

## 四、成本预估（心理预期）

按前 1,000 名高被引学者实测强度外推（后段递减，以下为上限）：

| 规模 | 检索请求 | 免费额度(1 key) | 落盘 |
|---|---:|---:|---:|
| 100 人 | ~1,000 | <1 天 | ~0.9 GB |
| 1,000 人 | ~1.1 万 | ~1 天 | ~8.6 GB |
| 全量 10.5 万 | ~113 万 | ~57 天（可多 key 轮换） | ~230 GB |

**建议**：先抓 100 人看效果 → 确认口径 → 再决定是否跑全量、用几个 key、配多大磁盘。

## 五、数据安全

- 数据落盘于本机 `data/`，不经过任何第三方
- API key 只存内存（除非勾选"保存到本机"），**不写请求留痕、不写日志**
- 服务只监听 `127.0.0.1`，不对局域网/互联网暴露

## 六、抓完后如何接续分析

自助工具只做「抓取」（WP1+WP2），产出四个文件在 `data/self_serve/`：

| 文件 | 内容 | 对应下游 |
|---|---|---|
| `works.jsonl` | 每作品一行（含署名/机构） | 入库、署名、机构分析 |
| `candidates.jsonl` | 每人 top-5 身份候选 | 消歧定案 |
| `requests.jsonl` | 请求留痕 | 导入请求审计表 |
| `checkpoint.json` | 断点（完成态） | 续跑 |

把这四个文件交回即可，后续落库、消歧、事件分析、DID 建模由分析方完成。

## 七、边界

- 本工具只做「抓取」，不做消歧定案、事件引擎、DID 分析
- 预置名单为 Elsevier 2017 高被引名单，版权与使用范围请自行确认

## 八、常见问题

**Q：`pip install` 报 `externally-managed-environment`（PEP 668）？**

这是 Linux 发行版（Debian/Ubuntu，含 WSL）对系统 Python 的保护，不允许直接装到系统目录。**不要用 `--break-system-packages` 强装**，直接运行 `bash start.sh` 即可——脚本会自动创建独立的 `.venv` 虚拟环境，不影响系统。

**Q：启动时报 `No module named 'flask'`？**

说明 `.venv` 是"空壳"（软链指向了系统 Python、依赖没装进去）。重新运行 `bash start.sh`（或双击 bat），脚本会自动检测到 flask 缺失并**重建 venv + 补装依赖**。不要手动 `python app.py`——务必通过启动脚本走 venv 内的 Python。

**Q：装依赖时提示找不到 flask / 下载慢？**

脚本默认用官方 PyPI 源。若网络慢，可把 `start.sh`（或 `启动自助抓取.bat`）里的安装命令加 `-i https://pypi.tuna.tsinghua.edu.cn/simple` 换成国内镜像。

**Q：端口 8765 被占用？**

```bash
python app.py --port 8766   # 换端口启动，然后访问 http://127.0.0.1:8766
```

**Q：如何彻底卸载 / 清理？**

删除本目录即可。抓取数据在 `data/`，key 缓存在 `.self_serve_key`（仅当你勾选过"保存到本机"）。
