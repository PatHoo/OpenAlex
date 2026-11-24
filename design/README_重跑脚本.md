# OpenAlex 1,000 人重跑脚本说明

给同事的一页式说明：`pipeline/data/batch_001/works.jsonl` 等中间产物不在 Git 仓库里，
需要重新抓取并用同一口径重建数据库、分析与交付物时，用这套脚本一条命令跑完。

完整链路：**WP0 生成子集 → WP1/WP2 抓取（双 key 轮换）→ WP3–WP16 落库分析 → 重导出 Excel 与平台快照**。

---

## 一、文件清单

| 文件 | 作用 |
|---|---|
| `design/run_rerun_1000.sh` | 一键执行脚本（本文附完整内容，可单独复制） |
| `pipeline/rerun_1000.py` | 真正的编排脚本：成本闸门、key 轮换、断点续跑、DB 步骤 |
| `design/1000人重跑执行手册.md` | 逐步 runbook：预检、验收、回滚 |
| `pipeline/tests/test_rerun_1000.py` | 离线回归测试（不联网、不连库） |

---

## 二、快速开始（云主机）

### 1. 建立凭据文件（一次性）

```bash
sudo install -d -m 700 /etc/openalex
sudo tee /etc/openalex/api.env >/dev/null <<'EOF'
OPENALEX_API_KEY_1='第一把key'
OPENALEX_API_KEY_2='第二把key'
OA_COST_CAP_USD='2.20'
OA_RATE='30'
EOF
sudo chmod 600 /etc/openalex/api.env
```

`/etc/openalex/db.env` 由部署清单建立，至少包含：

```bash
OA_PATH_MODE='linux'
PGHOST='127.0.0.1'
PGPORT='5432'
PGUSER='postgres'
PGDATABASE='openalex'
PGPASSWORD='数据库密码'
```

> 原生 Linux 下不会自动读 `~/.openalex_db_pass`，`PGPASSWORD` 必须在这里给出。
> key 必须已在邮箱激活，否则会返回 `403 key_not_activated`。

### 2. 执行

```bash
chmod +x run_rerun_1000.sh
./run_rerun_1000.sh                # 默认 --stage all
```

脚本会先跑离线回归测试、备份已发布文件，再开始抓取；日志写到
`/var/log/openalex/rerun_1000_all_<时间戳>.log`。

### 3. 本地 WSL

```bash
cd /mnt/c/Users/Administrator/Desktop/OpenAlex/design
export OPENALEX_API_KEY_1='第一把key'
export OPENALEX_API_KEY_2='第二把key'
export PGPASSWORD=$(cat ~/.openalex_db_pass)
SKIP_ENV_FILES=1 OPENALEX_ROOT=/mnt/c/Users/Administrator/Desktop/OpenAlex \
  PY=$HOME/.venvs/openalex/bin/python \
  LOG_DIR=$HOME/logs BACKUP_DIR=$HOME/backups \
  bash run_rerun_1000.sh
```

---

## 三、脚本参数

| 参数 | 说明 |
|---|---|
| `--stage fetch\|db\|all` | 默认 `all`；`fetch` 只抓取，`db` 只落库分析 |
| `--fresh` | 从零重跑：旧 `batch_001` 先改名备份，再从 WP0 重新生成子集 |
| `--dry-run` | 只打印计划，不发请求、不写库、不建目录 |
| `--no-tests` | 跳过离线回归测试（不推荐） |
| `--no-backup` | 跳过已发布文件备份（不推荐） |
| `-h` / `--help` | 查看帮助 |

环境变量覆盖默认值：`OPENALEX_ROOT`（默认 `/srv/openalex`）、`PY`（`python3`）、
`ENV_DIR`（`/etc/openalex`）、`LOG_DIR`（`/var/log/openalex`）、`BACKUP_DIR`（`/var/backups`）、
`SKIP_ENV_FILES=1`（用当前 shell 环境变量，不读 env 文件）。

常用变体：

```bash
./run_rerun_1000.sh --stage fetch --dry-run   # 先看计划
./run_rerun_1000.sh --stage fetch --fresh     # 从零重抓
./run_rerun_1000.sh --stage db                # 抓取已完成，续跑落库
```

---

## 四、退出码与暂停续跑

| 退出码 | 含义 | 处理 |
|---:|---|---|
| 0 | 全部完成 | 做第六节验收 |
| 1 | 预检失败 | 回归测试未通过，未发任何请求 |
| 2 | 配置错误 | 检查 env 文件、key、`PGPASSWORD` |
| 3 | 干净暂停：额度耗尽 / 成本超限 / 请求失败 | 修好后**重跑同一条命令**，自动从当前学者续抓 |
| 其他 | 某个 DB 步骤失败 | 看日志 `[FATAL]` 行，修复后 `--stage db` |

暂停时当前学者不会标完成、不会写空候选或半截作品；成本账本
`pipeline/data/batch_001/cost_ledger.json` 已落盘，续跑不会重复计费。

- `AllKeysExhausted`：两把 key 当日额度都耗尽，换新 key 或等次日重置。
- `CostCapExceeded`：调高 `api.env` 里的 `OA_COST_CAP_USD`（如 `3.00`）再跑。

---

## 五、执行前必须确认

- [ ] 数据库是**已恢复的 1,000 人基线**或**只建了 schema 的空库**。
      若当前是三人小样 POC，先恢复基线，不要直接跑。
- [ ] 磁盘留足：抓取阶段约 **8.6 GB** 给 `works.jsonl`，另留 20 GB 给数据库和备份。
- [ ] 两把 OpenAlex key 已激活，网络可访问 `api.openalex.org`。
- [ ] 脚本会自动备份 `web/data` 与 `output/client_workbook` 到 `$BACKUP_DIR`。

---

## 六、执行后验收

```bash
cd /srv/openalex/pipeline
source /etc/openalex/db.env

python3 - <<'PY'
import json
cp = json.load(open('data/batch_001/checkpoint.json', encoding='utf-8'))
print('wp1:', sum(v.get('wp1') for v in cp.values()),
      'wp2:', sum(v.get('wp2') for v in cp.values()),
      'works:', sum(v.get('works_count', 0) for v in cp.values()))
PY
# 期望 wp1/wp2 接近 1000，works 约 922,627

PGPASSWORD="$PGPASSWORD" psql -h 127.0.0.1 -U postgres -d openalex -c \
  "SELECT (SELECT count(*) FROM scholar) AS scholar,
          (SELECT count(*) FROM work) AS work,
          (SELECT count(*) FROM authorship) AS authorship,
          (SELECT count(*) FROM mobility_event) AS events;"
# 基线：scholar≈896 / work≈865,111 / authorship≈846,012
```

同时检查 `web/data/snapshot.json` 的 `generated_at` 是否为本次时间，页面仍标注为
**1,000 人试跑快照**，不得冒充全量；DID 的 treated 只有 5 人，只证明流程可跑。

---

## 七、回滚

| 对象 | 位置 | 恢复 |
|---|---|---|
| 数据库 | `pipeline/data/backups/openalex_<时间戳>.dump`（写库前自动生成） | `pg_restore --clean --if-exists -h 127.0.0.1 -U postgres -d openalex <dump>` |
| 中间产物 | `pipeline/data/batch_001_bak_<时间戳>/`（仅 `--fresh`） | 改名回 `batch_001` |
| 已发布文件 | `/var/backups/openalex_web_<时间戳>.tar.gz` | 解压覆盖 `web/data` 与 `output/client_workbook` |

DB 步骤中途失败时不要手动跳过，先按日志修复，再 `--stage db`。

---

## 八、成本与口径

- 官方计费：`/authors?search=` **$0.001/次**，`/works?filter=` **$0.0001/次**，`rate-limit` 免费。
- 1,000 人实测约 **$1.98**（1,000 次 search + 9,759 次 list）；默认上限 **$2.20** 含缓冲。
- `OA_RATE=30` 是免费 key / prepaid 的官方上限，实测遇 429 可下调到 `8`；新账号首 $1 按 $0.10/5 分钟释放，提高速率不等于能立刻跑完。
- **重跑不是原快照的逐字节恢复**：OpenAlex 的消歧与检索排序会变，`author_id`、候选与
  请求 sha256 都可能不同。对外须标注新的抓取日期，不能声称与旧版完全一致。

---

## 九、脚本全文（`design/run_rerun_1000.sh`）

```bash
#!/usr/bin/env bash
# 1,000 人重跑一键执行脚本（默认 --stage all）
#
# 直接复制到云主机：
#   chmod +x run_rerun_1000.sh
#   ./run_rerun_1000.sh
#
# 可用环境变量覆盖默认值：
#   OPENALEX_ROOT  项目根目录，默认 /srv/openalex
#   PY             Python 解释器，默认 python3
#   ENV_DIR        凭据目录，默认 /etc/openalex（内含 db.env、api.env）
#   LOG_DIR        日志目录，默认 /var/log/openalex
#   BACKUP_DIR     交付物备份目录，默认 /var/backups
#   SKIP_ENV_FILES=1  不读 env 文件，直接使用当前 shell 已导出的环境变量（WSL 用）
#
# 参数：
#   --stage fetch|db|all   默认 all
#   --fresh                从零重跑（旧 batch 自动改名备份）
#   --dry-run              只打印计划，不发请求、不写库
#   --no-tests             跳过离线回归测试
#   --no-backup            跳过交付物备份
#   -h|--help              帮助

set -uo pipefail

ROOT="${OPENALEX_ROOT:-/srv/openalex}"
PY="${PY:-python3}"
ENV_DIR="${ENV_DIR:-/etc/openalex}"
LOG_DIR="${LOG_DIR:-/var/log/openalex}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups}"

STAGE="all"
DRY=0
RUN_TESTS=1
BACKUP_WEB=1
EXTRA=()

usage() {
    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage)
            [[ $# -ge 2 ]] || { echo "[ERR] --stage 缺少参数" >&2; exit 2; }
            STAGE="$2"
            shift 2
            ;;
        --fresh|--dry-run)
            EXTRA+=("$1")
            [[ "$1" == "--dry-run" ]] && DRY=1
            shift
            ;;
        --no-tests)   RUN_TESTS=0; shift ;;
        --no-backup)  BACKUP_WEB=0; shift ;;
        -h|--help)    usage; exit 0 ;;
        *)
            echo "[ERR] 未知参数：$1（用 --help 查看用法）" >&2
            exit 2
            ;;
    esac
done

case "$STAGE" in
    fetch|db|all) ;;
    *) echo "[ERR] --stage 只能是 fetch / db / all" >&2; exit 2 ;;
esac

# ---------- 凭据 ----------
if [[ "${SKIP_ENV_FILES:-0}" == "1" ]]; then
    echo "[ENV] 使用当前 shell 已导出的环境变量"
else
    for f in db.env api.env; do
        if [[ ! -r "$ENV_DIR/$f" ]]; then
            echo "[ERR] 缺少或不可读：$ENV_DIR/$f" >&2
            echo "      参考 design/1000人重跑执行手册.md 第 1.1 节建立" >&2
            exit 2
        fi
    done
    set -a
    # shellcheck disable=SC1090,SC1091
    source "$ENV_DIR/db.env"
    # shellcheck disable=SC1090,SC1091
    source "$ENV_DIR/api.env"
    set +a
    echo "[ENV] 已加载 $ENV_DIR/db.env 与 $ENV_DIR/api.env"
fi

if [[ $DRY -eq 0 ]]; then
    [[ -n "${PGPASSWORD:-}" ]] || {
        echo "[ERR] PGPASSWORD 未设置：原生 Linux 下 dbutil/pg_dump 不会自动读 ~/.openalex_db_pass" >&2
        exit 2
    }
    if [[ -z "${OPENALEX_API_KEY_1:-}" && -z "${OPENALEX_API_KEYS:-}" ]]; then
        echo "[ERR] 未设置 OPENALEX_API_KEY_1 / OPENALEX_API_KEYS" >&2
        exit 2
    fi
fi

cd "$ROOT/pipeline" || { echo "[ERR] 目录不存在：$ROOT/pipeline" >&2; exit 2; }
[[ -f rerun_1000.py ]] || { echo "[ERR] 找不到 $ROOT/pipeline/rerun_1000.py" >&2; exit 2; }

# ---------- 预检 ----------
if [[ $RUN_TESTS -eq 1 ]]; then
    echo "[PREFLIGHT] 离线回归测试（不联网、不连库）"
    if ! "$PY" -m unittest discover -s tests -q; then
        echo "[FATAL] 回归测试未通过，已停止，未发出任何 API 请求" >&2
        exit 1
    fi
fi

TS="$(date +%Y%m%d_%H%M%S)"

if [[ $BACKUP_WEB -eq 1 && $DRY -eq 0 ]]; then
    mkdir -p "$BACKUP_DIR"
    targets=()
    [[ -d "$ROOT/web/data" ]] && targets+=(web/data)
    [[ -d "$ROOT/output/client_workbook" ]] && targets+=(output/client_workbook)
    if [[ ${#targets[@]} -gt 0 ]]; then
        archive="$BACKUP_DIR/openalex_web_${TS}.tar.gz"
        tar czf "$archive" -C "$ROOT" "${targets[@]}"
        echo "[BACKUP] 已发布文件 -> $archive"
    fi
fi

# ---------- 执行 ----------
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/rerun_1000_${STAGE}_${TS}.log"
echo "[RUN] stage=$STAGE  extra=${EXTRA[*]:-无}  日志=$LOG"

"$PY" -u rerun_1000.py --stage "$STAGE" "${EXTRA[@]}" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}

case "$rc" in
    0)
        echo "[OK] 全部完成。验收步骤见 design/1000人重跑执行手册.md 第 4 节"
        ;;
    2)
        echo "[FAIL] 配置错误，请检查 env 文件与参数；日志：$LOG" >&2
        ;;
    3)
        echo "[PAUSED] 已干净暂停（额度/成本/请求失败）。修好后重跑同一条命令即可续跑；日志：$LOG" >&2
        ;;
    *)
        echo "[FAIL] 某步骤失败（退出码 $rc），看日志里的 [FATAL] 行；日志：$LOG" >&2
        ;;
esac

exit "$rc"
```
