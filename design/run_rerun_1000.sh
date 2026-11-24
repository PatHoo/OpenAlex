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
