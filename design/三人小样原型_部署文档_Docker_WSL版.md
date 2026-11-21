# 三人小样本地原型 · 部署文档（Docker + Ubuntu WSL 版）

版本：v1.1 · 2026-11-21（修复口令/路径硬编码、图重建保护与核对流程）
适用环境：客户机器已安装 Docker 与 WSL Ubuntu（AGE 可正常使用）
目标：起 PostgreSQL 16 + PostGIS + Apache AGE，导入三人真实小样，跑通关系库查询与图查询
配套文件：
- `schema.sql` — 22 张表 + 视图 + 触发器
- `三人小样_seed.sql` — 从 1030 版 Excel 真实抽取的 9 类数据
- `三人小样_AGE投影.sql` — AGE 图投影 + 5 条验证查询
- `verify_poc.sql` — 导入后一致性/完整性核对与阈值演示
- `api_request_seed.sql` — 从 `A基线请求清单` 自动提取的 64 条 API 请求日志（幂等）
- `api_request_crossref_seed.sql` — 从 `A补查Crossref` 提取的 156 条 Crossref 请求日志（幂等）
- `event_evidence_seed.sql` — 6 个事件 × 5 项判据 = 30 条事件证据（幂等）
- `rule_test_seed.sql` — 19 项规则测试（全部 passed，幂等）
- `关系库+AGE_图模式设计.md` — 设计说明

> 与主部署文档（`三人小样原型_部署文档.md`）的区别：本版假设 Docker/WSL 可用，因此**图查询部分是必做项**，不是可选项。

---

## 1. 环境确认

在 WSL Ubuntu 终端执行：

```bash
docker --version
docker ps
python3 --version
```

若 `docker ps` 报权限错误，把当前用户加入 docker 组：

```bash
sudo usermod -aG docker $USER
newgrp docker
```

---

## 2. 选择部署方式

| 方式 | 说明 | 推荐度 |
|---|---|---|
| **2A Docker 容器** | 镜像自带 PG+PostGIS+AGE，一条命令起，最省事 | ★★★ 推荐 |
| **2B WSL Ubuntu 原生** | apt 装 PG+PostGIS，AGE 从源码编译 | ★★☆ 备选 |

---

## 3. 方式 2A：Docker 部署（推荐）

### 3.1 拉取镜像

Apache AGE 官方镜像（PG 16 对应版本）：

```bash
docker pull apache/age:release_PG16_1.6.0
```

> **tag 变更**：旧文档写的 `PG16_latest` 已不存在。官方当前 PG16 可用 tag 为 `release_PG16_1.6.0` / `release_PG16_1.5.0` / `dev_snapshot_PG16`，可用下列命令核实：
>
> ```bash
> curl -s https://docker.1ms.run/v2/apache/age/tags/list | tr ',' '\n' | grep PG16
> ```

若 Docker Hub 直连超时，可走国内镜像站拉取后重命名，后续命令无需改动：

```bash
docker pull docker.1ms.run/apache/age:release_PG16_1.6.0
docker tag docker.1ms.run/apache/age:release_PG16_1.6.0 apache/age:release_PG16_1.6.0
```

本机已采用第二条路径，并给 Docker 配置了 `https://docker.m.daocloud.io` 加速器；该加速器配置在 `/etc/docker/daemon.json`。

### 3.2 启动容器

```bash
export OPENALEX_DB_PASSWORD='<请设置强密码>'
export PGPASSWORD="$OPENALEX_DB_PASSWORD"
docker run -d --name openalex-pg \
  -e POSTGRES_PASSWORD="$OPENALEX_DB_PASSWORD" \
  -e POSTGRES_DB=openalex \
  -p 5433:5432 \
  -v openalex-pgdata:/var/lib/postgresql/data \
  apache/age:release_PG16_1.6.0
```

参数说明：
- `-p 5433:5432`：宿主机 5433 映射到容器 5432；若 WSL 内没有原生 PostgreSQL 占用 5432，也可改成 `5432:5432`
- `-v openalex-pgdata:...`：数据卷持久化，容器删了数据还在
- 本机同时运行原生 PostgreSQL 17（5432），因此 AGE 容器使用 5433

### 3.3 验证容器与扩展

```bash
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -c "
CREATE EXTENSION IF NOT EXISTS age;
SELECT extname FROM pg_extension ORDER BY extname;
"
```

预期输出包含 `age`、`plpgsql`。

**重要**：AGE 官方镜像**不带 PostGIS**，而 `schema.sql` 的 `institution.geom` 列使用 `GEOGRAPHY` 类型，必须先补装。镜像内 Debian 13 + PG 16.10，PGDG 的 PostGIS 3.7 存在 `heap_datum_check` 符号不兼容，需固定到 3.6.4：

```bash
docker exec openalex-pg bash -lc 'sed -i "s|http://deb.debian.org/debian|http://mirrors.aliyun.com/debian|g; s|http://deb.debian.org/debian-security|http://mirrors.aliyun.com/debian-security|g" /etc/apt/sources.list.d/debian.sources && apt-get update -y && DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-16-postgis-3=3.6.4+dfsg-2.pgdg13+1 postgresql-16-postgis-3-scripts=3.6.4+dfsg-2.pgdg13+1'
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -c 'CREATE EXTENSION IF NOT EXISTS postgis;'
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -tAc "SELECT string_agg(extname, ',' ORDER BY extname) FROM pg_extension;"
```

预期最后一行输出 `age,plpgsql,postgis`。

### 3.4 把脚本复制进容器

假设工作目录在 Windows 的 `C:\Users\<你的用户名>\Desktop\OpenAlex\design`，WSL 中通常挂在 `/mnt/c/Users/<你的用户名>/Desktop/OpenAlex/design`：

```bash
cd /mnt/c/Users/<你的用户名>/Desktop/OpenAlex/design
docker cp schema.sql openalex-pg:/tmp/schema.sql
docker cp 三人小样_seed.sql openalex-pg:/tmp/seed.sql
docker cp 三人小样_AGE投影.sql openalex-pg:/tmp/graph.sql
docker cp verify_poc.sql openalex-pg:/tmp/verify.sql
docker cp api_request_seed.sql openalex-pg:/tmp/api_seed.sql
docker cp api_request_crossref_seed.sql openalex-pg:/tmp/crossref_seed.sql
docker cp event_evidence_seed.sql openalex-pg:/tmp/ee_seed.sql
docker cp rule_test_seed.sql openalex-pg:/tmp/rt_seed.sql
```

---

## 4. 方式 2B：WSL Ubuntu 原生部署（备选）

### 4.1 安装 PostgreSQL + PostGIS

```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib postgis postgresql-16-postgis-3
sudo service postgresql start
read -rsp '请设置 PostgreSQL 密码: ' PW; echo
sudo -u postgres psql -c "ALTER USER postgres PASSWORD '$PW';"
export PGPASSWORD="$PW"; unset PW
sudo -u postgres psql -c "CREATE DATABASE openalex;"
```

### 4.2 编译安装 Apache AGE

AGE 在 Ubuntu 下需从源码编译，步骤：

```bash
sudo apt install -y build-essential libreadline-dev zlib1g-dev \
                    flex bison libssl-dev libpq-dev git
git clone --depth 1 https://github.com/apache/age.git /tmp/age
cd /tmp/age
make PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config
sudo make PG_CONFIG=/usr/lib/postgresql/16/bin/pg_config install
```

在目标库启用：

```bash
sudo -u postgres psql -d openalex -c "CREATE EXTENSION age;"
sudo service postgresql restart
```

验证：

```bash
sudo -u postgres psql -d openalex -c "LOAD 'age'; SELECT create_graph('smoke');"
```

> 若编译报错，优先改用方式 2A。AGE 编译依赖本地 PG 开发头文件版本严格匹配。

### 4.3 导入脚本

```bash
cd /mnt/c/Users/<你的用户名>/Desktop/OpenAlex/design
sudo -u postgres psql -d openalex -f schema.sql
sudo -u postgres psql -d openalex -f 三人小样_seed.sql
sudo -u postgres psql -d openalex -f 三人小样_AGE投影.sql
```

---

## 5. 导入数据（方式 2A）

### 5.1 建表

```bash
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -f /tmp/schema.sql
```

预期：一串 `CREATE TABLE` / `CREATE INDEX` 成功，无 ERROR。

若报 `type "geography" does not exist`：说明镜像无 PostGIS，执行 3.3 的安装命令后重试。

### 5.2 导入种子数据

```bash
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -f /tmp/seed.sql
```

预期：`BEGIN` → 若干 `INSERT 0 1` → `COMMIT`。

### 5.2b 导入 API 请求日志（可选但推荐）

```bash
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -f /tmp/api_seed.sql
```

预期：`INSERT 0 64` → `setval 64` → `COMMIT`。按 `request_id` 1..64 幂等更新，重复执行不产生重复行。

继续导入 Crossref 补查请求（URL 按 DOI 推导）：

```bash
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -f /tmp/crossref_seed.sql
```

预期：`INSERT 0 156` → `setval 220` → `COMMIT`。`backend_path` 取原表 `raw_path`，时间戳 / sha256 / 字节数保留 NULL。

继续导入事件证据与规则测试：

```bash
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -f /tmp/ee_seed.sql
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -f /tmp/rt_seed.sql
```

预期：事件证据 `INSERT 0 30` / `setval 30`，规则测试 `INSERT 0 19`。

### 5.3 行数核对

```bash
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -c "
SELECT 'scholar' t, count(*) FROM scholar
UNION ALL SELECT 'institution', count(*) FROM institution
UNION ALL SELECT 'annual_panel', count(*) FROM annual_panel
UNION ALL SELECT 'affiliation_share', count(*) FROM affiliation_share
UNION ALL SELECT 'mobility_event', count(*) FROM mobility_event
UNION ALL SELECT 'career_fact', count(*) FROM career_fact
UNION ALL SELECT 'mobility_class', count(*) FROM mobility_class
UNION ALL SELECT 'identity_map', count(*) FROM identity_map
UNION ALL SELECT 'source_scholar', count(*) FROM source_scholar
UNION ALL SELECT 'api_request', count(*) FROM api_request
UNION ALL SELECT 'event_evidence', count(*) FROM event_evidence
UNION ALL SELECT 'rule_test', count(*) FROM rule_test;
"
```

**预期结果**：

| 表 | 行数 |
|---|---|
| scholar | 3 |
| institution | 11 |
| annual_panel | 139 |
| affiliation_share | 126 |
| mobility_event | 6 |
| career_fact | 4 |
| mobility_class | 9 |
| identity_map | 6 |
| source_scholar | 3 |
| api_request | 220（64+156，两份 API 种子都执行后） |
| event_evidence | 30（6 事件 × 5 判据） |
| rule_test | 19 |

导入后执行完整核对（含外键、字典、证据字段与阈值演示）：

```bash
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -f /tmp/verify.sql
```

预期：所有 CHECK 查询返回 `OK` 或 0 行，不出现 `FAIL`。

---

## 6. 跑通关系库查询

以下 6 条为主部署文档 5.1–5.6 的精简版，建议逐条执行。为方便在 Docker 中跑，先进入 psql：

```bash
docker exec -it -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex
```

### 6.1 三人分类现状

```sql
SELECT s.display_name, c.rule_version, c.D, c.I, c.S,
       c.observed_class,
       CASE WHEN c.formal_class IS NULL THEN '留空(不确定)' ELSE c.formal_class END formal
FROM mobility_class c JOIN scholar s USING (scholar_id)
WHERE c.rule_version='A_base_060' ORDER BY s.scholar_id;
```

**预期**：Becke/Perdew → `M6 / 留空(不确定)`；Newman → `M8 / M8`。

### 6.2 六条迁移事件

```sql
SELECT s.display_name, e.event_type, e.paper_year,
       coalesce(e.actual_year::text,'NULL') actual_year,
       e.gap_years, e.evidence_level, e.year_source
FROM mobility_event e JOIN scholar s USING (scholar_id)
WHERE e.rule_version='A_base_060' ORDER BY e.event_id;
```

**预期**：6 行；Becke 2 行 `actual_year=NULL / Possible / signature_proxy`；其余 4 行 `Confirmed / external_actual / gap=1`。

### 6.3 A/B 双轨

```sql
SELECT s.display_name, c.rule_version, c.D, c.I, c.S,
       coalesce(c.formal_class,'—') formal
FROM mobility_class c JOIN scholar s USING (scholar_id)
ORDER BY s.scholar_id, c.rule_version;
```

**预期**：9 行。`B_sens_070` 下 Becke 的 possible_migrations 变化（1→2），但 A 轨不受影响。

### 6.4 A 轨不可变

```sql
UPDATE mobility_class SET formal_class='M8'
WHERE scholar_id=1 AND rule_version='A_base_060';
```

**预期**：ERROR `baseline rule_version A_base_060 is immutable`。这是设计意图。

### 6.5 Newman 轨迹

```sql
SELECT ap.year, ap.primary_country country, i.canonical_name inst,
       i.city, i.lat, i.lon, ap.note event
FROM annual_panel ap LEFT JOIN institution i USING (institution_id)
WHERE ap.scholar_id=3 AND ap.primary_institution_id IS NOT NULL
ORDER BY ap.year;
```

**预期**：1991 Oxford(GB) → 1992 Cornell(US) → 1997 Santa Fe → 2003 Michigan；note 列含 `C2/C1/C3/C1/C3`。

### 6.6 LOCF 禁用

```sql
SELECT count(*) FROM mobility_event e
JOIN annual_panel ap ON ap.scholar_id=e.scholar_id AND ap.year=e.paper_year
WHERE ap.is_imputed;
```

**预期**：0。

---

## 7. 跑通 AGE 图查询（本版必做）

退出 psql（`\q`），执行图投影：

```bash
docker exec -e PGPASSWORD="$OPENALEX_DB_PASSWORD" openalex-pg psql -U postgres -d openalex -f /tmp/graph.sql
```

预期：图存在时才 `drop_graph` → `create_graph` → 标签创建 → 投影块 / 验证查询依次输出。

> **AGE 1.6 兼容调整（2026-11-22 实机验证）**：`三人小样_AGE投影.sql` 已按镜像内 AGE 1.6.0 调整——属性索引语句默认注释（旧 agtype 下标语法会报 `invalid input syntax for type agtype`）；`HAS_FACT` / `EXPERIENCED` 边拆成独立语句（同一语句内返回刚被边引用的顶点会报 `vertex assigned to variable ... was deleted`）；`Queen's` 用反斜杠转义；V3 的 `ORDER BY` 使用原表达式而非别名。上述改动不影响图结构与查询结果。

本机实跑结果：V1=3、V2=4 行、V3=6 行、V4=Newman/1992/1991/英国/美国、V5=6，与下表一致；`verify_poc.sql` 全部 CHECK 为 `OK`。

### 7.1 验证查询预期结果

| 查询 | 预期 |
|---|---|
| V1 学者数 | 3 |
| V2 Newman 轨迹 | 4 行：1991 Oxford / 1992 Cornell / 1997 Santa Fe / 2003 Michigan，含经纬度 |
| V3 事件全景 | 6 行；Becke 2 行 `actual_year` 为空、`level=Possible`、无证据 URL；其余 4 行有履历 |
| V4 Newman C2 | `Newman, 1992, 1991, 英国, 美国` |
| V5 事件数 | 6 |

### 7.2 图 vs 关系库差异解读

- 图里 `AFFILIATED` 边只有 14 条（机构变化拐点），关系库 `annual_panel` 有 139 行（逐年完整面板）。**这是故意的**：图做轨迹展示，关系库做统计口径。
- 图里 `Event.actual_year=null` 的 2 条对应 Becke 的 `signature_proxy`——报告里"任职年份缺失"在图上也必须留空，不能补。
- 图里 `CORROBORATED_BY` 边只有 4 条，正好对应 4 个 `external_actual` 履历证据。

---

## 8. 故障排查（Docker / WSL 特有）

| 症状 | 原因 | 解决 |
|---|---|---|
| `docker: Cannot connect to daemon` | Docker Desktop 未启动 / WSL 集成未开 | 启动 Docker Desktop，Settings → Resources → WSL Integration 勾选 Ubuntu |
| `docker ps` 权限拒绝 | 用户不在 docker 组 | `sudo usermod -aG docker $USER && newgrp docker` |
| Windows 访问 `localhost:5432` 不通 | WSL2 端口转发问题，或容器实际映射到 5433 | 用 `PGPASSWORD=... psql -h 127.0.0.1 -p 5433 -U postgres -d openalex` 连接 AGE 容器 |
| 中文乱码 | 客户端编码 | psql 内 `\encoding UTF8`；WSL 终端 `export LANG=zh_CN.UTF-8` |
| `/mnt/c/...` 路径读不到 | WSL 未挂载 C 盘 | `ls /mnt/c/Users/<你的用户名>/Desktop/OpenAlex/design` 确认 |
| AGE `LOAD 'age'` 失败 | 镜像无 AGE 或 search_path 未设 | 换 `apache/age` 镜像；单独跑查询时先加 `LOAD 'age'; SET search_path = ag_catalog, "$user", public;` |
| `type "geography" does not exist` | 镜像无 PostGIS | 按 3.3 节装入 PostGIS 3.6.4 并执行 `CREATE EXTENSION postgis;`；3.7 与 PG16 不兼容 |
| 图投影 `CREATE` 报序列化失败 | AGE 并发写 | 单线程顺序执行 graph.sql，不要并行跑多个 psql |

---

## 9. 验收清单

- [ ] Docker 容器运行，PG + AGE 扩展可用
- [ ] `schema.sql` 执行无 ERROR
- [ ] `三人小样_seed.sql` 导入，8 张表行数与第 5.3 节一致
- [ ] 6.1 Becke/Perdew formal 留空、Newman=M8
- [ ] 6.2 六条事件、2 条 signature_proxy、4 条 external_actual 且 gap=1
- [ ] 6.4 A 轨不可变触发器生效
- [ ] 6.5 Newman 轨迹与 Excel 一致
- [ ] 7.1 五条图查询全部符合预期

---

## 10. 项目目录

```text
design/
├── 关系库+AGE_图模式设计.md          # 完整设计与 C1–C5 双模型对照
├── 三人小样原型_部署文档.md          # 通用版（本机无 Docker/WSL 时）
├── 三人小样原型_部署文档_Docker_WSL版.md  # 本文档
├── schema.sql                        # 22 张表 + 视图 + 触发器
├── 三人小样_seed.sql                  # 真实抽取数据（含 3 条源名单行）
├── 三人小样_AGE投影.sql               # 图投影 + 5 条验证查询
└── verify_poc.sql                    # 导入后一致性/完整性核对与阈值演示
```

全部数据来自 `1030版_新三学者基线与增量对照数据.xlsx`，非臆造示例。
