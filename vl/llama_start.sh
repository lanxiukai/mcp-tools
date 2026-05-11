#!/usr/bin/env bash
#
# llama.cpp 一键启动脚本
# =====================
# 支持两种运行模式：
#   1) server  — 启动 OpenAI 兼容的 API 服务（默认后台运行）
#   2) cli     — 启动交互式命令行对话（前台运行）
#
# 用法:
#   ./llama_start.sh                  # 交互式选择模式和模型
#   ./llama_start.sh server           # 后台启动 API 服务
#   ./llama_start.sh server --fg      # 前台启动 API 服务
#   ./llama_start.sh cli              # 启动命令行对话（始终前台）
#   ./llama_start.sh stop             # 停止后台服务
#   ./llama_start.sh status           # 查看服务状态
#   ./llama_start.sh log              # 实时查看服务日志
#   ./llama_start.sh restart          # 重启服务
#

set -euo pipefail

# ============================================================
# 配置区域（按需修改）
# ============================================================

# llama.cpp 可执行文件目录
LLAMA_BIN_DIR="$HOME/llama.cpp/build/bin"

# 模型搜索目录（会递归查找所有 .gguf 文件）
# 固定为脚本所在目录下的 models/gguf/ 子目录，仓库移动/重命名不受影响
MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/models/gguf"

# 日志和 PID 文件存放目录
RUN_DIR="$HOME/.llama"

# GPU 卸载的Dense层数（999 = 尽可能全部卸载到 GPU）
GPU_LAYERS=999

# MoE Expert 权重放置策略（适用于 MoE 架构模型，Dense 模型设为 false 即可）：
# - false  : Expert 权重全部卸到 GPU（显存足够时最快）
# - all    : 全部 Expert 留在 CPU 内存（最省显存，适合显存极度不足时）
# - 数字 N : 前 N 层的 Expert 留在 CPU，其余层的 Expert 卸到 GPU（折中方案）
#            用于充分利用剩余显存提升速度，从大往小调整 N 直到不 OOM
#            Qwen3.5-35B-A3B（40层，block_count=40）+ 12GB 显存
#            Q4 建议 N=26 (禁用视觉功能) N=27 (启用视觉功能) 
#            Q6 建议 N=30 (禁用视觉功能) N=31 (启用视觉功能) 
CPU_MOE=27

# 上下文长度（单位：K Tokens，如 8 = 8K = 8192 tokens）
CTX_SIZE=8

# mmproj 视觉投影文件路径（用于图像/视觉理解功能）
# - 留空 : 自动在模型同目录下查找 mmproj*.gguf；找不到则纯文本模式运行
# - 填路径: 强制使用指定的 mmproj 文件
# - "none": 禁用视觉功能（即使同目录存在 mmproj 文件）
MMPROJ_FILE=""

# Flash Attention 模式（需要 llama.cpp 编译时已支持 CUDA/Metal）：
# - auto  : 由 llama.cpp 自动判断是否启用（推荐，默认值）
# - on    : 强制开启，可大幅降低长上下文显存占用并提升速度
# - off   : 强制关闭，使用标准 Attention（兼容性最佳）
FLASH_ATTN=on

# 思考 / 推理模式（适用于 Qwen3.5 / Qwen3.6 等支持 Thinking 的模型）：
# - on   : 启用思考模式（深度推理，质量更高但 token 消耗大、速度慢）
# - off  : 关闭思考模式（直接回答，速度快，适合图像描述等不需要推理的场景）
# - auto : 由模型/客户端自行决定（默认）
REASONING=off

# 使用的 CPU 线程数（默认取系统核心数的一半，留一半给其他任务）
_NPROC=$(nproc)
THREADS=$(( _NPROC / 2 > 0 ? _NPROC / 2 : 1 ))

# ---- Server 模式专用 ----
HOST="0.0.0.0"
PORT=8080
# 最大并发请求数
PARALLEL=1

# ---- CLI 模式专用 ----
# 对话模板（留空则自动检测）
CHAT_TEMPLATE=""
# 采样温度
TEMPERATURE=0.6
# Top-P 采样
TOP_P=0.95
# 每次生成的最大 token 数（-1 = 无限制）
MAX_TOKENS=-1

# ============================================================
# 运行时文件路径（自动生成，无需修改）
# ============================================================
# CTX_SIZE 单位换算：K Tokens → tokens（供 llama.cpp --ctx-size 使用）
CTX_SIZE_TOKENS=$(( CTX_SIZE * 1024 ))
PID_FILE="$RUN_DIR/llama-server.pid"
LOG_FILE="$RUN_DIR/llama-server.log"
MODEL_FILE="$RUN_DIR/llama-server.model"

# ============================================================
# 颜色定义
# ============================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # 恢复默认

# ============================================================
# 辅助函数
# ============================================================

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

banner() {
    echo -e "${CYAN}"
    echo "╔══════════════════════════════════════════════╗"
    echo "║        llama.cpp 一键启动脚本                ║"
    echo "╚══════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# 确保运行目录存在
ensure_run_dir() {
    mkdir -p "$RUN_DIR"
}

# 追加 GPU 相关参数（Flash Attention、MoE 策略）到命令数组
append_gpu_args() {
    local -n _arr=$1
    if [[ "$FLASH_ATTN" =~ ^(on|off|auto)$ ]]; then
        _arr+=(--flash-attn "$FLASH_ATTN")
    fi
    if [[ "$CPU_MOE" == "all" ]]; then
        _arr+=(--cpu-moe)
    elif [[ "$CPU_MOE" =~ ^[0-9]+$ ]] && (( CPU_MOE > 0 )); then
        _arr+=(--n-cpu-moe "$CPU_MOE")
    fi
}

# 检查必要的可执行文件
check_binaries() {
    local missing=0
    for bin in llama-server llama-cli llama-mtmd-cli; do
        if [[ ! -x "$LLAMA_BIN_DIR/$bin" ]]; then
            error "找不到 $LLAMA_BIN_DIR/$bin"
            missing=1
        fi
    done
    if [[ $missing -eq 1 ]]; then
        error "请先编译 llama.cpp: cd ~/llama.cpp && cmake -B build -DGGML_CUDA=ON -DBUILD_SHARED_LIBS=OFF && cmake --build build -j\$(nproc) --target llama-cli llama-mtmd-cli llama-server llama-gguf-split"
        exit 1
    fi
}

# 扫描并列出可用模型（自动排除 mmproj*.gguf 视觉投影文件）
scan_models() {
    mapfile -t MODELS < <(find "$MODEL_DIR" -name "*.gguf" -type f \
        ! -path "*/.cache/*" \
        ! -name "mmproj*.gguf" \
        2>/dev/null | sort)
    if [[ ${#MODELS[@]} -eq 0 ]]; then
        error "在 $MODEL_DIR 下未找到任何 .gguf 模型文件"
        error "请先下载模型，例如:"
        error "  python hf_hub_download.py"
        exit 1
    fi
}

# 解析 mmproj 文件路径：自动检测或使用配置值
resolve_mmproj() {
    # "none" 关键字：显式禁用视觉功能
    if [[ "$MMPROJ_FILE" == "none" ]]; then
        MMPROJ_FILE=""
        info "视觉功能已禁用 (MMPROJ_FILE=none)"
        return
    fi

    # 已填写路径：校验文件是否存在
    if [[ -n "$MMPROJ_FILE" ]]; then
        if [[ -f "$MMPROJ_FILE" ]]; then
            info "使用指定 mmproj: $(basename "$MMPROJ_FILE")"
        else
            warn "指定的 mmproj 文件不存在，已忽略: $MMPROJ_FILE"
            MMPROJ_FILE=""
        fi
        return
    fi

    # 未填写：在模型同目录下自动查找 mmproj*.gguf（优先 F16）
    local model_dir
    model_dir=$(dirname "$SELECTED_MODEL")
    local found=""
    # 优先查找推荐的 F16 版本
    found=$(find "$model_dir" -maxdepth 1 -name "mmproj*F16*.gguf" -type f 2>/dev/null | head -1)
    # 未找到 F16 则取任意 mmproj 文件
    if [[ -z "$found" ]]; then
        found=$(find "$model_dir" -maxdepth 1 -name "mmproj*.gguf" -type f 2>/dev/null | sort | head -1)
    fi
    if [[ -n "$found" ]]; then
        MMPROJ_FILE="$found"
        info "自动检测到 mmproj 文件: $(basename "$MMPROJ_FILE")"
    fi
}

# 让用户选择模型
select_model() {
    if [[ ${#MODELS[@]} -eq 1 ]]; then
        SELECTED_MODEL="${MODELS[0]}"
        info "自动选择唯一模型: $(basename "$SELECTED_MODEL")"
        return
    fi

    echo -e "\n${BOLD}可用模型:${NC}"
    for i in "${!MODELS[@]}"; do
        local model_name
        model_name=$(basename "${MODELS[$i]}")
        local model_size
        model_size=$(du -h "${MODELS[$i]}" 2>/dev/null | cut -f1)
        printf "  ${BLUE}[%d]${NC} %-50s ${YELLOW}(%s)${NC}\n" "$((i + 1))" "$model_name" "$model_size"
    done

    echo ""
    while true; do
        read -rp "$(echo -e "${BOLD}请选择模型 [1-${#MODELS[@]}]:${NC} ")" choice
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#MODELS[@]} )); then
            SELECTED_MODEL="${MODELS[$((choice - 1))]}"
            break
        fi
        warn "无效输入，请输入 1 到 ${#MODELS[@]} 之间的数字"
    done
    info "已选择: $(basename "$SELECTED_MODEL")"
}

# 让用户选择运行模式
select_mode() {
    echo -e "\n${BOLD}运行模式:${NC}"
    echo -e "  ${BLUE}[1]${NC} server — API 服务模式 (OpenAI 兼容, 端口 $PORT, 后台运行)"
    echo -e "  ${BLUE}[2]${NC} cli    — 交互式命令行对话 (前台运行)"
    echo ""
    while true; do
        read -rp "$(echo -e "${BOLD}请选择模式 [1/2]:${NC} ")" choice
        case "$choice" in
            1) MODE="server"; break ;;
            2) MODE="cli"; break ;;
            *) warn "无效输入，请输入 1 或 2" ;;
        esac
    done
}

# 显示启动配置摘要
show_config() {
    local run_type="后台"
    [[ "$FOREGROUND" == "true" ]] && run_type="前台"
    [[ "$MODE" == "cli" ]] && run_type="前台"

    echo ""
    echo -e "${CYAN}────────────────── 启动配置 ──────────────────${NC}"
    echo -e "  模式:       ${BOLD}$MODE${NC} (${run_type}运行)"
    echo -e "  模型:       ${BOLD}$(basename "$SELECTED_MODEL")${NC}"
    if [[ -n "$MMPROJ_FILE" ]]; then
        echo -e "  视觉投影:   ${BOLD}$(basename "$MMPROJ_FILE")${NC} ${GREEN}[视觉已启用]${NC}"
    else
        echo -e "  视觉投影:   ${YELLOW}未配置（纯文本模式）${NC}"
    fi
    echo -e "  GPU 层数:   ${BOLD}$GPU_LAYERS${NC}"
    echo -e "  上下文长度: ${BOLD}${CTX_SIZE}K tokens${NC}"
    echo -e "  Flash Attn: ${BOLD}$FLASH_ATTN${NC}"
    echo -e "  推理模式:   ${BOLD}$REASONING${NC}"
    if [[ "$CPU_MOE" == "all" ]]; then
        echo -e "  CPU MoE:    ${BOLD}all layers (--cpu-moe)${NC}"
    elif [[ "$CPU_MOE" =~ ^[0-9]+$ ]] && (( CPU_MOE > 0 )); then
        echo -e "  CPU MoE:    ${BOLD}first ${CPU_MOE} layers (--n-cpu-moe)${NC}"
    else
        echo -e "  CPU MoE:    ${BOLD}off${NC}"
    fi
    echo -e "  线程数:     ${BOLD}$THREADS${NC}"
    if [[ "$MODE" == "server" ]]; then
        echo -e "  监听地址:   ${BOLD}$HOST:$PORT${NC}"
        echo -e "  并发数:     ${BOLD}$PARALLEL${NC}"
        if [[ "$FOREGROUND" != "true" ]]; then
            echo -e "  日志文件:   ${BOLD}$LOG_FILE${NC}"
            echo -e "  PID 文件:   ${BOLD}$PID_FILE${NC}"
        fi
    else
        echo -e "  温度:       ${BOLD}$TEMPERATURE${NC}"
        echo -e "  Top-P:      ${BOLD}$TOP_P${NC}"
        if [[ "$MAX_TOKENS" -eq -1 ]]; then
            echo -e "  最大生成:   ${BOLD}无限制${NC}"
        else
            echo -e "  最大生成:   ${BOLD}${MAX_TOKENS} tokens${NC}"
        fi
    fi
    echo -e "${CYAN}───────────────────────────────────────────────${NC}"
    echo ""
}

# ============================================================
# 进程管理函数
# ============================================================

# 获取正在运行的服务 PID（如果有的话）
get_running_pid() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(<"$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            # 验证该进程确实是 llama-server（防止 PID 被复用）
            local proc_name=""
            if [[ -r "/proc/$pid/comm" ]]; then
                proc_name=$(<"/proc/$pid/comm")
            fi
            if [[ "$proc_name" == "llama-server" ]] || [[ -z "$proc_name" ]]; then
                echo "$pid"
                return 0
            else
                rm -f "$PID_FILE"
            fi
        else
            # PID 文件过期，清理
            rm -f "$PID_FILE"
        fi
    fi
    return 1
}

# 检查服务是否正在运行
is_running() {
    get_running_pid > /dev/null 2>&1
}

# 释放 Linux 页缓存（mmap'd 模型文件在进程退出后仍留在 page cache 中）
# WSL2 下不主动释放会导致 VmmemWSL 持续占用大量内存
drop_page_cache() {
    if [[ -w /proc/sys/vm/drop_caches ]]; then
        sync
        echo 3 > /proc/sys/vm/drop_caches
        info "已释放页缓存（drop_caches）"
    elif command -v sudo &>/dev/null; then
        sync
        echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null 2>&1 \
            && info "已释放页缓存（drop_caches via sudo）" \
            || warn "释放页缓存失败（需要 sudo 权限），可手动执行: sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'"
    else
        warn "无法释放页缓存（需要 root 权限），可手动执行: sudo sh -c 'echo 3 > /proc/sys/vm/drop_caches'"
    fi
}

# 停止服务
do_stop() {
    ensure_run_dir
    local pid
    if pid=$(get_running_pid); then
        info "正在停止 llama-server (PID: $pid) ..."
        kill "$pid" 2>/dev/null || true
        # 等待进程退出，最多 10 秒
        local count=0
        while kill -0 "$pid" 2>/dev/null && (( count < 20 )); do
            sleep 0.5
            (( count++ )) || true
        done
        if kill -0 "$pid" 2>/dev/null; then
            warn "进程未响应，发送 SIGKILL ..."
            kill -9 "$pid" 2>/dev/null || true
            sleep 1
        fi
        rm -f "$PID_FILE"
        info "服务已停止"
        drop_page_cache
    else
        warn "没有正在运行的 llama-server 服务"
    fi
}

# 查看服务状态
do_status() {
    ensure_run_dir
    local pid
    if pid=$(get_running_pid); then
        echo -e "${GREEN}[运行中]${NC} llama-server (PID: $pid)"
        # 显示进程信息
        echo ""
        echo -e "${BOLD}进程详情:${NC}"
        ps -p "$pid" -o pid,ppid,%cpu,%mem,etime,args --no-headers 2>/dev/null | while read -r line; do
            echo "  $line"
        done
        echo ""
        echo -e "${BOLD}端口监听:${NC}"
        local port_info
        port_info=$(ss -tlnp 2>/dev/null | grep ":$PORT ") || true
        if [[ -n "$port_info" ]]; then
            echo "$port_info" | while read -r line; do echo "  $line"; done
        else
            echo "  (端口 $PORT 暂未监听，服务可能仍在加载中)"
        fi
        echo ""
        info "日志文件: $LOG_FILE"
        info "查看日志: $0 log"
        # 显示最后几行日志
        if [[ -f "$LOG_FILE" ]]; then
            echo ""
            echo -e "${BOLD}最近日志 (最后 5 行):${NC}"
            tail -5 "$LOG_FILE" | while read -r line; do
                echo "  $line"
            done
        fi
    else
        echo -e "${RED}[未运行]${NC} llama-server 服务未启动"
        info "启动服务: $0 server"
    fi
}

# 查看日志
do_log() {
    ensure_run_dir
    if [[ -f "$LOG_FILE" ]]; then
        info "实时日志输出 (按 Ctrl+C 退出查看) ..."
        echo ""
        tail -f "$LOG_FILE"
    else
        warn "日志文件不存在: $LOG_FILE"
        warn "服务可能还未启动过"
    fi
}

# ============================================================
# 启动函数
# ============================================================

# 构建 Server 启动命令（前台/后台共用，通过 nameref 填充调用方的数组）
build_server_cmd() {
    local -n _ref=$1
    _ref=(
        "$LLAMA_BIN_DIR/llama-server"
        --model "$SELECTED_MODEL"
        --host "$HOST"
        --port "$PORT"
        --ctx-size "$CTX_SIZE_TOKENS"
        --n-gpu-layers "$GPU_LAYERS"
        --threads "$THREADS"
        --parallel "$PARALLEL"
    )
    if [[ -n "$MMPROJ_FILE" ]]; then
        _ref+=(--mmproj "$MMPROJ_FILE")
    fi
    if [[ "$REASONING" == "off" ]]; then
        _ref+=(--chat-template-kwargs '{"enable_thinking":false}')
    fi
    append_gpu_args _ref
}

# 启动 Server 模式（后台）
start_server_background() {
    ensure_run_dir

    # 检查是否已有服务在运行
    local pid
    if pid=$(get_running_pid); then
        warn "llama-server 已在运行 (PID: $pid)"
        warn "如需重启，请先执行: $0 stop"
        exit 1
    fi

    # 检查端口是否已被占用
    if ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
        error "端口 $PORT 已被占用，请先释放该端口或修改 PORT 配置"
        ss -tlnp 2>/dev/null | grep ":$PORT " | while read -r line; do echo "  $line"; done
        exit 1
    fi

    local cmd
    build_server_cmd cmd

    # 打印实际执行的命令
    info "执行命令:"
    echo -e "  ${BOLD}${cmd[*]}${NC}"
    echo ""

    # 设置库路径
    export LD_LIBRARY_PATH="$LLAMA_BIN_DIR:${LD_LIBRARY_PATH:-}"

    # 后台启动，日志重定向到文件
    info "正在后台启动 llama-server ..."
    nohup "${cmd[@]}" > "$LOG_FILE" 2>&1 &
    local server_pid=$!
    echo "$server_pid" > "$PID_FILE"

    # 轮询端口就绪，最多等待 120 秒（大模型加载耗时较长）
    info "等待服务就绪（最多 120 秒）..."
    local max_wait=120
    local elapsed=0
    local ready=false
    while (( elapsed < max_wait )); do
        sleep 1
        (( elapsed++ )) || true
        if ! kill -0 "$server_pid" 2>/dev/null; then
            break
        fi
        if ss -tlnp 2>/dev/null | grep -q ":$PORT "; then
            ready=true
            break
        fi
    done

    if [[ "$ready" == "true" ]]; then
        echo "$SELECTED_MODEL" > "$MODEL_FILE"
        echo ""
        info "服务启动成功！(${elapsed}s)"
        echo -e "  PID:      ${BOLD}$server_pid${NC}"
        echo -e "  API 地址: ${BOLD}http://$HOST:$PORT${NC}"
        [[ "$HOST" == "0.0.0.0" ]] && echo -e "            ${BOLD}http://localhost:$PORT${NC} (本机访问)"
        echo -e "  API 文档: ${BOLD}http://$HOST:$PORT/docs${NC}"
        echo -e "  日志文件: ${BOLD}$LOG_FILE${NC}"
        echo ""
        echo -e "${CYAN}常用管理命令:${NC}"
        echo -e "  查看状态: ${BOLD}$0 status${NC}"
        echo -e "  查看日志: ${BOLD}$0 log${NC}"
        echo -e "  停止服务: ${BOLD}$0 stop${NC}"
        echo -e "  重启服务: ${BOLD}$0 restart${NC}"
    else
        if ! kill -0 "$server_pid" 2>/dev/null; then
            error "服务进程已退出（参数错误或模型加载失败）"
            rm -f "$PID_FILE"
        else
            echo "$SELECTED_MODEL" > "$MODEL_FILE"
            error "等待服务就绪超时（${max_wait}s），进程仍在运行 (PID: $server_pid)"
            error "模型可能仍在加载中，请稍后使用 '$0 status' 检查"
        fi
        if [[ -f "$LOG_FILE" ]]; then
            echo ""
            error "最后 15 行日志:"
            tail -15 "$LOG_FILE" | while IFS= read -r line; do echo "  $line"; done
        fi
        exit 1
    fi
}

# 启动 Server 模式（前台）
start_server_foreground() {
    local cmd
    build_server_cmd cmd

    info "正在前台启动 llama-server ..."
    info "API 地址: http://$HOST:$PORT"
    [[ "$HOST" == "0.0.0.0" ]] && info "本机访问: http://localhost:$PORT"
    info "API 文档: http://$HOST:$PORT/docs"
    echo -e "${YELLOW}提示: 按 Ctrl+C 停止服务${NC}"
    echo ""

    info "执行命令:"
    echo -e "  ${BOLD}${cmd[*]}${NC}"
    echo ""

    export LD_LIBRARY_PATH="$LLAMA_BIN_DIR:${LD_LIBRARY_PATH:-}"
    exec "${cmd[@]}"
}

# 启动 CLI 模式（始终前台）
start_cli() {
    # 视觉模式使用专用的 llama-mtmd-cli，纯文本使用 llama-cli
    local cli_bin="$LLAMA_BIN_DIR/llama-cli"
    if [[ -n "$MMPROJ_FILE" ]]; then
        cli_bin="$LLAMA_BIN_DIR/llama-mtmd-cli"
        info "检测到 mmproj，使用多模态 CLI: llama-mtmd-cli"
        echo -e "${YELLOW}提示: 视觉模式下可通过 --image /path/to/image.jpg 传入图片${NC}"
    fi

    info "正在启动交互式对话 ..."
    echo -e "${YELLOW}提示: 输入文本后按回车发送，输入 /bye 或按 Ctrl+C 退出${NC}"
    echo ""

    local cmd=(
        "$cli_bin"
        --model "$SELECTED_MODEL"
        --ctx-size "$CTX_SIZE_TOKENS"
        --n-gpu-layers "$GPU_LAYERS"
        --threads "$THREADS"
        --temp "$TEMPERATURE"
        --top-p "$TOP_P"
        --predict "$MAX_TOKENS"
    )
    if [[ -n "$MMPROJ_FILE" ]]; then
        cmd+=(--mmproj "$MMPROJ_FILE")
    else
        # llama-mtmd-cli 不支持 --conversation / --color，且默认即为对话模式
        cmd+=(--conversation --color)
    fi
    append_gpu_args cmd

    if [[ -n "$CHAT_TEMPLATE" ]]; then
        cmd+=(--chat-template "$CHAT_TEMPLATE")
    fi
    if [[ "$REASONING" == "off" ]]; then
        cmd+=(--reasoning off)
    fi

    info "执行命令:"
    echo -e "  ${BOLD}${cmd[*]}${NC}"
    echo ""

    export LD_LIBRARY_PATH="$LLAMA_BIN_DIR:${LD_LIBRARY_PATH:-}"
    exec "${cmd[@]}"
}

# ============================================================
# 帮助信息
# ============================================================

show_help() {
    echo -e "${BOLD}用法:${NC}"
    echo "  $0 [命令] [选项] [模型路径]"
    echo ""
    echo -e "${BOLD}命令:${NC}"
    echo "  server [--fg] [模型]   启动 API 服务（默认后台运行，--fg 前台运行）"
    echo "  cli [模型]             启动交互式命令行对话（前台运行）"
    echo "  stop                   停止后台运行的服务"
    echo "  status                 查看服务运行状态"
    echo "  log                    实时查看服务日志"
    echo "  restart                重启服务"
    echo "  help                   显示此帮助信息"
    echo ""
    echo -e "${BOLD}示例:${NC}"
    echo "  $0                     # 交互式选择"
    echo "  $0 server              # 后台启动 API 服务"
    echo "  $0 server --fg         # 前台启动 API 服务"
    echo "  $0 cli                 # 启动命令行对话"
    echo "  $0 stop                # 停止服务"
    echo "  $0 status              # 查看状态"
    echo "  $0 log                 # 查看日志"
    echo "  $0 restart             # 重启服务"
}

# ============================================================
# 主流程
# ============================================================

main() {
    banner
    ensure_run_dir

    # 解析第一个参数
    local action="${1:-}"
    local _saved_model=""
    local _restart_mode=""

    # 处理管理命令（无需检查模型和二进制）
    case "$action" in
        stop)
            do_stop
            exit 0
            ;;
        status)
            do_status
            exit 0
            ;;
        log)
            do_log
            exit 0
            ;;
        restart)
            do_stop
            # 读取上次运行的模型路径，实现原地重启（无需重新选择模型）
            if [[ -f "$MODEL_FILE" ]]; then
                _saved_model=$(<"$MODEL_FILE")
                [[ -f "$_saved_model" ]] || _saved_model=""
            fi
            shift
            action="server"
            _restart_mode="server"
            ;;
        help|--help|-h)
            show_help
            exit 0
            ;;
    esac

    check_binaries
    scan_models

    # 解析模式和选项（restart 时保留已设置的 server 模式）
    MODE="${_restart_mode}"
    FOREGROUND="false"
    MODEL_PATH=""

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case "$1" in
            server)
                MODE="server"
                shift
                ;;
            cli)
                MODE="cli"
                shift
                ;;
            --fg|--foreground)
                FOREGROUND="true"
                shift
                ;;
            *)
                # 其他参数视为模型路径
                MODEL_PATH="$1"
                shift
                ;;
        esac
    done

    # 如果没传 action 但也没进管理命令分支，重新赋值
    [[ -z "$MODE" && -n "$action" && "$action" != "server" && "$action" != "cli" ]] && MODEL_PATH="${MODEL_PATH:-$action}"

    # 优先级：命令行指定 > restart 时记录的上次模型 > 交互式选择
    if [[ -n "$MODEL_PATH" ]]; then
        if [[ -f "$MODEL_PATH" ]]; then
            SELECTED_MODEL="$MODEL_PATH"
            info "使用指定模型: $(basename "$SELECTED_MODEL")"
        else
            error "指定的模型文件不存在: $MODEL_PATH"
            exit 1
        fi
    elif [[ -n "$_saved_model" ]]; then
        SELECTED_MODEL="$_saved_model"
        info "重启: 沿用上次模型: $(basename "$SELECTED_MODEL")"
    else
        select_model
    fi

    # 解析 mmproj 视觉投影文件（模型确定后才能自动检测同目录）
    resolve_mmproj

    # 如果没有通过命令行指定模式，交互式选择
    if [[ -z "$MODE" ]]; then
        select_mode
    fi

    show_config

    case "$MODE" in
        server)
            if [[ "$FOREGROUND" == "true" ]]; then
                start_server_foreground
            else
                start_server_background
            fi
            ;;
        cli)
            start_cli
            ;;
    esac
}

main "$@"
