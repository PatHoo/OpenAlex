# 云端 Demo 部署清单（nginx + CDN + PostgreSQL）：1,000 人验收 → 一键扩到 5,000 人

适用对象：把 `web/platform.html` 给客户在线浏览，并把 1,000 人试跑数据库迁到云端；客户验收后
在云端一键扩到 **5,000 人**（终态）。**10 万人规模由客户另购服务器，不在本清单范围。**

目标配置（按 5,000 人终态，兼顾成本）：
- **起步（推荐）**：4 核 / 8 GB / 200 GB SSD / 10 Mbps 峰值——5,000 人库约 10.5 GB，
  PG 给 2–3 GB shared_buffers 足够；200 GB SSD 容纳 5 倍中间产物与每日备份。
- 若只在 1,000 人验收、暂不扩容：可先用 2 核 4 GB / 60 GB / 5 Mbps，扩到 5,000 人前再升配。
- 系统：Debian 12 或 Ubuntu 22.04。

> 核心判断：客户浏览路径**不查库**——页面读静态快照，数据库只服务于跑批、快照/工作簿重新导出。
> 因此主机规格按"5,000 人终态跑批"定（4 核 8 GB），而非按客户并发浏览人数。
> `setup_postgres.sh` 已改为按实际内存自动设置 shared_buffers/effective_cache_size，
> 2 核 4 GB 起步与 4 核 8 GB 扩容档都能直接用。10 万人另购服务器，不在本清单范围。

## 0. 目标架构

```
客户
  └─ HTTPS → 云主机 nginx（sharecode 门禁）
       ├─ /                  自助抓取工具（公开；API key 只存浏览器 localStorage）
       ├─ /web/              Demo：platform.html + data/*.js + data/*.json（静态快照）
       └─ /web/download/     鉴权后 302 → 私有 COS + CDN 签名 URL

云主机本机（仅 127.0.0.1/Unix socket，不对公网开放）
  └─ PostgreSQL 17 + PostGIS 3.5 · openalex 库（2.1 GB）
       ├─ pipeline 跑批 / DID / 队列证据
       ├─ export_platform_snapshot.py → 重新生成 web/data/
       └─ export_client_workbook.py  → 重新生成两份 xlsx → COS → CDN
```

配套文件（本目录 `deploy_assets/`）：
- `nginx-openalex.conf` — nginx 站点配置：门禁、地图 Key 注入、下载跳转
- `gate_service.py` — 标准库：sharecode 签发 Cookie、校验、生成 COS/CDN 签名 URL
- `setup_postgres.sh` — 安装 PG17 + PostGIS、建库、恢复转储、只监听本机
- `backup_postgres.sh` — 每日转储 + 上传 COS + 本地保留 7 天

## 1. 主机与域名

- [ ] 云主机 **4 核 / 8 GB / 200 GB SSD** / 10 Mbps 峰值带宽（5,000 人终态；若只在 1,000 人验收可先用 2 核 4 GB / 60 GB，扩容前升配）
- [ ] 建议数据单独挂一块 200 GB 云硬盘到 `/var/lib/postgresql`，系统盘 60 GB 即可（容纳 5,000 人终态约 43 GB 中间产物 + 备份）；10 万人规模由客户另购服务器，需预留 500 GB+
- [ ] 安全组只开放 22 / 80 / 443；**5432 绝不对公网开放**
- [ ] 域名解析 A 记录指向公网 IP，例如 `demo.example.com`

## 2. 同步文件

- [ ] 创建目录：`sudo mkdir -p /var/www/openalex /opt/openalex /srv/openalex`
- [ ] 上传 `web/`（4.6 MB）到 `/var/www/openalex/`；核对 `snapshot.js` 326 KB、`workbook.js` 41 KB、两份 xlsx 共约 2 MB
- [ ] 上传仓库代码到 `/srv/openalex/`（pipeline 在这里跑）；核对 `pipeline/config.py` 的 `ROOT` 指向正确
- [ ] **不要**上传 `output/openalex_audit_20261121/internal/`、`.workbuddy/`、`*.env`、任何 `*.key`
- [ ] `sudo chown -R www-data:www-data /var/www/openalex`

## 3. 安装 nginx 与 HTTPS

```bash
sudo apt update && sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d demo.example.com --agree-tos -m admin@example.com
sudo systemctl enable --now certbot.timer
```

- [ ] HTTPS 可访问，证书自动续期定时器已启用

## 4. 数据库上云

### 4.1 本地导出转储

先确认本地 PG17 里确实有 1,000 人试跑数据。**注意库名：本机 WSL 的试跑数据在 `openalex_1000` 库（2026-11-25 重跑完成），另一个 `openalex` 库只有 3 行、是早期空壳——务必从 `openalex_1000` 导出，别搬错库。** 云端恢复目标是 `openalex` 库（与 pipeline 默认连接参数一致），导出/导入库名不同是刻意设计。

```bash
# 在 WSL 里执行（核对源库 openalex_1000）
PGPASSWORD=$(cat ~/.openalex_db_pass) psql -h 127.0.0.1 -U postgres -d openalex_1000 \
  -tAc "SELECT (SELECT count(*) FROM source_scholar), (SELECT count(*) FROM scholar), (SELECT count(*) FROM work), (SELECT count(*) FROM authorship), (SELECT count(*) FROM institution), (SELECT count(*) FROM annual_panel), (SELECT count(*) FROM mobility_event), (SELECT count(*) FROM did_panel);"
```

- [ ] 应为 `1000|895|853423|833756|31856|42165|13357|3316`（2026-11-25 重跑实测）→ 才是有数据的试跑库
- [ ] 若为 `3|3|0|0`，说明连错到 `openalex` 空壳库，改用 `-d openalex_1000`

```bash
sudo mkdir -p /mnt/d/openalex_backup
PGPASSWORD=$(cat ~/.openalex_db_pass) pg_dump -h 127.0.0.1 -p 5432 -U postgres \
  -Fc openalex_1000 -f /mnt/d/openalex_backup/openalex_1000_$(date +%F).dump
ls -lh /mnt/d/openalex_backup/          # 应在 2 GB 量级
scp /mnt/d/openalex_backup/openalex_*.dump <user>@<host>:/opt/openalex/
```

### 4.2 云主机安装与恢复

- [ ] 把 `setup_postgres.sh` 放到 `/opt/openalex/setup_postgres.sh`
- [ ] 生成强密码并保存：`python3 -c 'import secrets; print(secrets.token_urlsafe(24))'`
- [ ] 执行安装 + 恢复（第二个参数是转储文件路径，脚本会自动只监听 127.0.0.1）：

```bash
sudo bash /opt/openalex/setup_postgres.sh '<数据库密码>' /opt/openalex/openalex_1000_2026-11-25.dump
```

- [ ] 验证：`sudo -u postgres psql -d openalex -c "SELECT PostGIS_Version();"`
- [ ] 验证行数：`PGPASSWORD='<密码>' psql -h 127.0.0.1 -U oa_app -d openalex -c "SELECT count(*) FROM work;"` → 应为 **853,423**（源库 `openalex_1000` 恢复到云端 `openalex` 库；若接近 0 说明导错库或恢复失败）
- [ ] 确认 5432 未监听公网：`sudo ss -lntp | grep 5432` → 只应看到 `127.0.0.1:5432`

### 4.3 pipeline 运行环境（已重构）

原先 5 个脚本把路径硬编码成 `"/mnt/c" + 绝对路径`，且用 `wsl -e bash` 调 psql、从 `~/.openalex_db_pass` 读密码，原生 Linux 上全部失效。现在已收敛：

- `config.py`：`path_mode()` 与 `pg_path()` 统一路径转换
- `dbutil.py`：`psql_command()` / `run_psql()` / `query_psql()` 统一 psql 调用与退出码检查
- `wp4/wp5/wp7/wp8`：改用上述公共函数；顺带去掉 `| tail`（该写法会吞掉 psql 失败退出码）
- `pipeline/tests/test_path_config.py`：两种模式的回归测试

- [ ] 云主机设置 `OA_PATH_MODE=linux`（写入 `/etc/openalex/db.env` 或在命令行 export）
- [ ] WSL 本机无需设置该变量：检测到 `WSL_DISTRO_NAME` 会自动走原逻辑；也可显式 `OA_PATH_MODE=wsl`
- [ ] 连接参数可用 `PGHOST` / `PGPORT` / `PGUSER` / `PGDATABASE` 覆盖，默认 `127.0.0.1:5432 / postgres / openalex`
- [ ] 跑测试确认：`cd /srv/openalex/pipeline && python3 -m unittest discover -s tests -v`（应全绿，不访问数据库）
- [ ] 若 pipeline 暂时留在本地 WSL：用 SSH 隧道连接云端库（`ssh -L 5432:127.0.0.1:5432 <host>`），加 `OA_PATH_MODE=wsl`，本地脚本无需改

### 4.4 凭据与备份

- [ ] 数据库密码写入 `/etc/openalex/db.env`（600，属主 root），不要让 pipeline 代码或仓库文件包含密码
- [ ] `db.env` 里同时包含 `OA_PATH_MODE=linux`；pipeline 运行时加载：`set -a; source /etc/openalex/db.env; set +a`
- [ ] 部署 `backup_postgres.sh` 到 `/opt/openalex/`，加 crontab 每日 03:00 执行
- [ ] 备份上传到与主站不同的 COS 桶，并开启生命周期（如保留 90 天）
- [ ] **每季度做一次恢复演练**：把备份恢复到临时库并核对 `work` 行数，没演练过的备份不算备份

## 5. sharecode 门禁

README 明确：原发布平台的 sharecode 校验不在前端代码里，自己搭主机必须补齐，否则任何链接都能看数据。

- [ ] 生成两个随机密钥（保存到密码管理器，不要进 Git）：

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'   # SHARECODE
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'   # COOKIE_SECRET
```

- [ ] 把 `gate_service.py` 放到 `/opt/openalex/gate_service.py`
- [ ] 创建 `/etc/openalex/gate.env`（权限 600）：

```bash
sudo mkdir -p /etc/openalex && sudo chmod 700 /etc/openalex
sudo tee /etc/openalex/gate.env >/dev/null <<'EOF'
SHARECODE=改成上一步生成的分享码
COOKIE_SECRET=改成上一步生成的密钥
COS_SECRET_ID=腾讯云API密钥ID
COS_SECRET_KEY=腾讯云API密钥Key
COS_BUCKET=你的私有桶名-1300000000
COS_REGION=ap-guangzhou
CDN_HOST=cdn.example.com
COS_PREFIX=openalex
EOF
sudo chmod 600 /etc/openalex/gate.env
```

- [ ] 用 systemd 常驻（`/etc/systemd/system/openalex-gate.service`）：

```ini
[Unit]
Description=OpenAlex sharecode gate
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/openalex
EnvironmentFile=/etc/openalex/gate.env
ExecStart=/usr/bin/python3 /opt/openalex/gate_service.py
Restart=always
RestartSec=2
User=www-data

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now openalex-gate
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/auth   # 应返回 401
```

- [ ] 客户流程：`/login?sharecode=<分享码>` → 服务端下发 HttpOnly Cookie → 302 到 `/web/platform.html`
- [ ] `/web/` 全部请求由 nginx `auth_request` 校验；无 Cookie 返回 302 到登录页
- [ ] `/login` 关闭访问日志，避免分享码写进 nginx log

## 6. 地图 Key（不硬编码进公开前端）

`platform.html` 目前把 `TMAP_KEY` 占位符直接拼进腾讯 GL JS 的 script 地址。处理办法：

- [ ] 在腾讯位置服务控制台申请 Key，设置**域名白名单**为 `demo.example.com`
- [ ] 把 `nginx-openalex.conf` 中的 `TMAP_KEY_PLACEHOLDER` 替换为真实 Key（该文件留在服务器，**不要提交到公开仓库**）：

```bash
sudo sed -i 's/TMAP_KEY_PLACEHOLDER/你的腾讯地图Key/g' /etc/nginx/conf.d/openalex.conf
sudo nginx -t && sudo systemctl reload nginx
```

- [ ] 仓库里的 `web/platform.html` 保持占位符不变；Key 只由 nginx 在响应时注入
- [ ] 说明：腾讯 GL JS 是浏览器 SDK，Key 最终必然到浏览器；"后端代理"的实际收益是仓库源码不含 Key、可按域名限制盗用。若暂时不用地图，跳过本节——四种非地图视图（条形/热力矩阵/流线/三维柱状）不依赖 Key

## 7. CDN（只加速下载，不放行数据）

为什么不全站 CDN：快照 `snapshot.js` 就是交付数据本身，全站 CDN 要么让数据裸露，要么需要边缘鉴权改造。而 99% 的出流量来自 1.8 MB 的 Excel，所以只把下载放到 CDN，收益最大、改动最小。

- [ ] 创建**私有**对象存储桶（腾讯云 COS 或阿里云 OSS）
- [ ] 上传两份 xlsx 到 `openalex/` 前缀下，文件名与 `web/download/` 保持一致：

```bash
coscmd config -a <SecretId> -s <SecretKey> -b <bucket> -r ap-guangzhou
coscmd upload "web/download/学者流动分析成果_1000人试跑版.xlsx" "openalex/"
coscmd upload "web/download/新三学者基线与增量对照数据_数据库复现版.xlsx" "openalex/"
```

- [ ] 绑定 CDN 加速域名 `cdn.example.com`，源站选私有 COS 桶，开启**私有桶回源**
- [ ] 缓存键配置：**忽略 `q-sign-*` 签名参数**，否则每次签名 URL 都是缓存未命中
- [ ] CDN 只开 GET/HEAD，关闭目录列举；配置 Referer 白名单 `https://demo.example.com`
- [ ] 在 `gate.env` 填入 COS 信息后重启 `openalex-gate`
- [ ] 页面 HTML 不用改：相对链接 `download/...` 解析到 `/web/download/`，由 nginx 接住

## 8. 部署 nginx 配置

- [ ] 复制 `nginx-openalex.conf` 到 `/etc/nginx/conf.d/openalex.conf`
- [ ] 替换 `demo.example.com`（server_name 与证书路径）
- [ ] 按第 6 节替换地图 Key 占位符
- [ ] `sudo nginx -t && sudo systemctl reload nginx`

## 9. 一键扩到 5,000 人（客户终态）

客户在云端验收 1,000 人后，用一条命令扩到 5,000 人（10 万人由客户另购服务器）。

- [ ] 先把云主机升配到 **4 核 8 GB / 200 GB**（若起步用的是 2 核 4 GB / 60 GB）
- [ ] 确认 `/etc/openalex/api.env` 的双 key 有足够额度：5,000 人约需 **$9.8** API 费
- [ ] 部署一键脚本：`design/run_rerun_5000.sh` → `/opt/openalex/run_rerun_5000.sh`，`chmod +x`
- [ ] 先预演：`./run_rerun_5000.sh --dry-run`（只打印计划，不发请求、不写库）
- [ ] 正式跑：`./run_rerun_5000.sh`（自动 `--fresh` 重建前 5,000 人子集 → 抓取 → 落库 → 分析 → 重导出交付物）
- [ ] 成本闸门默认 **$11.00**（`OA_COST_CAP_USD` 可覆盖）；额度/成本暂停时退出码 3，重跑同命令续跑
- [ ] 跑完同步：`web/` → `/var/www/openalex/`；两份 xlsx → COS；刷新 CDN 缓存
- [ ] 页面口径改为 5,000 人快照，重新标注抓取日期（不再是"1,000 人试跑"）

预期规模：学者 5,000 / works 约 427 万行 / authorship 约 417 万 / institution 约 16 万 /
入库约 10.5 GB / 中间产物约 43 GB（200 GB 盘可容纳）。

**"客户点击按钮"的落地方式**：上述命令是服务端操作。若要做成页面按钮，需在门禁后加一个
触发端（systemd timer 或受 sharecode 保护的 CGI），调用同一个 `run_rerun_5000.sh`；
按钮只做"触发 + 显示进度"，跑批逻辑不重复实现。本期先按命令行触发交付，按钮化作为可选项。

## 10. 验收清单

```bash
# 未登录访问 Demo → 302 到 /login
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' https://demo.example.com/web/platform.html

# 错误分享码 → 403；正确分享码 → 302 + Set-Cookie
curl -s -o /dev/null -w '%{http_code}\n' 'https://demo.example.com/login?sharecode=wrong'
curl -si 'https://demo.example.com/login?sharecode=<你的分享码>' | head -20

# 带 Cookie 访问 → 200，快照能取到
curl -s -o /dev/null -w '%{http_code}\n' -H 'Cookie: oa_access=<Cookie值>' https://demo.example.com/web/data/snapshot.js

# 下载 → 302 到 CDN 签名 URL；第二次应有 X-Cache: HIT
curl -si -H 'Cookie: oa_access=<Cookie值>' 'https://demo.example.com/web/download/%E5%AD%A6%E8%80%85%E6%B5%81%E5%8A%A8%E5%88%86%E6%9E%90%E6%88%90%E6%9E%9C_1000%E4%BA%BA%E8%AF%95%E8%B7%91%E7%89%88.xlsx' | grep -i '^location'

# 数据库：只监听本机，行数正确
sudo ss -lntp | grep 5432
PGPASSWORD='<密码>' psql -h 127.0.0.1 -U oa_app -d openalex -c "SELECT count(*) FROM work;"
```

- [ ] 浏览器实测：分享码登录 → 图表全部渲染 → DID/队列页有数 → 两份 Excel 能下载
- [ ] 手机浏览器实测一次（相对路径与下载跳转在移动端容易出问题）
- [ ] 无分享码的隐身窗口访问 `/web/platform.html` 被拦住
- [ ] 从公网尝试连接 5432 应被安全组拒绝

## 11. 运维与风险

- [ ] nginx 日志按周轮转；确认 `/login` 不记分享码
- [ ] 每月检查证书自动续期：`sudo certbot certificates`
- [ ] 数据库每日自动备份到独立 COS 桶；每季度做一次恢复演练
- [ ] 快照更新流程：云端跑 `pipeline/export_platform_snapshot.py` 与 `export_client_workbook.py` → 同步 `web/` → 更新 COS 上的 xlsx → 刷新 CDN 缓存
- [ ] 页面显著位置保留口径声明：**1,000 人试跑快照，占全量 105,026 人的 0.95%，不代表全量交付**；DID 处理组仅 5 人，不作政策结论
- [ ] 分享码通过内部渠道单独发给客户，不写进公开仓库（README 的明确要求）
- [ ] 10 万人规模由客户另购服务器（500 GB+ 数据盘、重新评估跑批时长）；本清单只到 5,000 人

## 12. 不做的理由

- **客户浏览路径不查数据库**：页面读静态快照，数据库放在 `127.0.0.1` 仅供跑批与导出，避免把 2.1 GB 库变成公网攻击面
- **不做全站 CDN**：交付数据在快照里，边缘缓存会带来权限口径问题；下载走 CDN 已解决带宽瓶颈
- **不把地图 Key 写进前端仓库**：README 与页面提示都禁止；用 nginx 响应时注入 + 域名白名单
- **不再用 `/mnt/c → /` 软链**：路径转换已在 `config.py` 按模式处理，软链会掩盖真实路径问题
