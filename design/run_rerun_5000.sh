#!/usr/bin/env bash
# 5,000 人一键扩容执行脚本（默认 --stage all，从零重建子集）
#
# 目标：在 1,000 人试跑验证通过的基础上，把学者数一键扩到 5,000 人（客户终态）。
# 依赖 run_rerun_1000.sh 的全部约定；本脚本只是把参数锁定到 5,000 人规模。
#
# 直接复制到云主机：
#   chmod +x run_rerun_5000.sh
#   ./run_rerun_5000.sh                 # 正式跑（--fresh，重建 5000 人子集 + 全链路）
#   ./run_rerun_5000.sh --dry-run       # 只打印计划，不发请求、不写库
#   ./run_rerun_5000.sh --no-fresh      # 续跑：不重建子集，接着已有 checkpoint 跑
#
# 规模与成本口径（按 1000 人实测线性外推）：
#   学者 5,000 人 / 请求约 51,810 次 / API 费约 $9.8 / works 约 427 万行 / 入库约 10.5 GB
#   成本闸门默认 $11.00（含约 12% 缓冲）；可用 OA_COST_CAP_USD 覆盖。
#   注意：--fresh 会重建子集、清空 checkpoint，全部 5,000 人重抓（含原 1000 人），
#         不是只补 4,000 人；$9.8 是 5,000 人全量费用。原 1000 人结果会先备份。
#
# 可用环境变量覆盖默认值：
#   OPENALEX_ROOT  项目根目录，默认 /srv/openalex
#   PY             Python 解释器，默认 python3
#   ENV_DIR        凭据目录，默认 /etc/openalex（内含 db.env、api.env）
#   LOG_DIR        日志目录，默认 /var/log/openalex
#   BACKUP_DIR     交付物备份目录，默认 /var/backups
#   OA_COST_CAP_USD 成本闸门美元上限，默认 11.00
#   OA_SUBSET_SIZE 子集人数，固定 5000（一般不要改）
#   SKIP_ENV_FILES=1  不读 env 文件，直接使用当前 shell 已导出的环境变量（WSL 用）
#
# 参数：
#   --stage fetch|db|all   默认 all
#   --no-fresh             不重建子集（续跑模式）
#   --dry-run              只打印计划
#   --no-tests             跳过离线回归测试
#   --no-backup            跳过交付物备份
#   -h|--help              帮助

set -uo pipefail

ROOT="${OPENALEX_ROOT:-/srv/openalex}"
PY="${PY:-python3}"
ENV_DIR="${ENV_DIR:-/etc/openalex}"
LOG_DIR="${LOG_DIR:-/var/log/openalex}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups}"
SUBSET_SIZE="${OA_SUBSET_SIZE:-5000}"
COST_CAP="${OA_COST_CAP_USD:-11.00}"

STAGE="all"
FRESH=1
DRY=0
RUN_TESTS=1
BACKUP_WEB=1
EXTRA=()

usage() {
    sed -n '2,42p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage)
            [[ $# -ge 2 ]] || { echo "[ERR] --stage 缺少参数" >&2; exit 2; }
            STAGE="$2"
            shift 2
            ;;
        --no-fresh)   FRESH=0; shift ;;
        --dry-run)
            EXTRA+=("$1"); DRY=1; shift
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

# 规模与成本闸门固定为 5000 人档，导出给 wp0_import.py 与 rerun_1000.py
export OA_SUBSET_SIZE="$SUBSET_SIZE"
export OA_COST_CAP_USD="$COST_CAP"

echo "[SCALE] 学者 $SUBSET_SIZE 人；成本闸门 \$$COST_CAP（按 1000 人实测 \$1.96 线性外推 \$9.81 + 缓冲）"

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

# env 文件里若有 OA_COST_CAP_USD，以本脚本的 5000 人档为准（source 之后重新导出）
export OA_COST_CAP_USD="$COST_CAP"

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
[[ -f wp0_import.py ]] || { echo "[ERR] 找不到 $ROOT/pipeline/wp0_import.py" >&2; exit 2; }

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
        archive="$BACKUP_DIR/openalex_web_5000_${TS}.tar.gz"
        tar czf "$archive" -C "$ROOT" "${targets[@]}"
        echo "[BACKUP] 已发布文件 -> $archive"
    fi
fi

# ---------- 执行 ----------
if [[ $FRESH -eq 1 ]]; then
    EXTRA+=(--fresh)
    echo "[FRESH] 将备份旧 batch_001 并从 105,026 人名单重建前 $SUBSET_SIZE 人子集"
    if [[ $DRY -eq 1 ]]; then
        echo "[DRY-RUN] dry-run 不执行 WP0，下面 [PLAN] 读的是现有 subset.jsonl（可能是旧人数）；正式跑会重建为 $SUBSET_SIZE 人"
    fi
fi

if ! mkdir -p "$LOG_DIR" 2>/dev/null; then
    LOG_DIR="${HOME}/openalex-logs"
    mkdir -p "$LOG_DIR"
    echo "[ENV] 默认日志目录不可建，改存 $LOG_DIR"
fi
LOG="$LOG_DIR/rerun_5000_${STAGE}_${TS}.log"
echo "[RUN] stage=$STAGE  extra=${EXTRA[*]}  日志=$LOG"

"$PY" -u rerun_1000.py --stage "$STAGE" "${EXTRA[@]}" 2>&1 | tee "$LOG"
rc=${PIPESTATUS[0]}

case "$rc" in
    0)
        echo "[OK] 5,000 人扩容完成。快照与工作簿已重新导出到 web/ 与 output/client_workbook/"
        echo "     记得同步 web/ 到 /var/www/openalex 并更新 COS 上的 xlsx、刷新 CDN"
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
