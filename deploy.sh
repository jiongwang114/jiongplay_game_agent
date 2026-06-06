#!/usr/bin/env bash
# =============================================================================
#  Steam 游戏推荐 Agent — 一键部署脚本（云服务器端）
# =============================================================================
#  用法:
#      chmod +x deploy.sh
#      ./deploy.sh              # 首次部署 / 更新重启
#      ./deploy.sh --reset      # 完全重建（清除数据 & 镜像缓存）
# =============================================================================

set -euo pipefail

# ---- 配置（按需修改）----------------------------------------------------------
GITHUB_REPO="https://github.com/你的用户名/你的仓库名.git"   # ← 替换为你的仓库地址
PROJECT_DIR="$HOME/steam-game-agent"
BRANCH="main"

# ---- 颜色输出 -----------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ---- 参数解析 -----------------------------------------------------------------
RESET=false
if [[ "${1:-}" == "--reset" ]]; then
    RESET=true
    warn "完全重建模式：将清除数据库和 Docker 缓存"
fi

# ---- Step 1: 安装 Docker（如未安装）-------------------------------------------
if ! command -v docker &>/dev/null; then
    log "安装 Docker..."
    curl -fsSL https://get.docker.com | bash
    sudo systemctl enable docker --now
    sudo usermod -aG docker "$USER"
    log "Docker 安装完成（可能需要重新登录使权限生效）"
fi

if ! docker compose version &>/dev/null 2>&1; then
    log "安装 Docker Compose 插件..."
    sudo apt-get update -qq && sudo apt-get install -y -qq docker-compose-plugin
fi

# ---- Step 2: 拉取代码 ---------------------------------------------------------
if [[ -d "$PROJECT_DIR/.git" ]]; then
    log "更新已有代码..."
    cd "$PROJECT_DIR"
    git fetch origin "$BRANCH"
    git reset --hard "origin/$BRANCH"
else
    log "首次克隆仓库..."
    git clone --branch "$BRANCH" "$GITHUB_REPO" "$PROJECT_DIR"
    cd "$PROJECT_DIR"
fi

# ---- Step 3: 配置环境变量 -----------------------------------------------------
if [[ ! -f ".env" ]]; then
    if [[ -f ".env.example" ]]; then
        warn "未检测到 .env 文件！"
        warn "请编辑项目目录下的 .env 填入真实 API Key，然后重新运行本脚本："
        warn ""
        warn "    cd $PROJECT_DIR"
        warn "    cp .env.example .env"
        warn "    nano .env    # 填入 DEEPSEEK_API_KEY 和 STEAM_API_KEY"
        warn "    ./deploy.sh"
        warn ""
        # 首次自动创建 .env 占位，避免 docker compose 报错
        cp .env.example .env
        err "已创建 .env 占位文件，请编辑后重新运行"
    else
        err "缺少 .env.example 模板文件，请检查仓库"
    fi
else
    log "检测到 .env 配置文件 ✓"
fi

# ---- Step 4: 构建 & 启动 ------------------------------------------------------
if $RESET; then
    log "完全重建：清除旧容器、镜像、数据卷..."
    docker compose down -v --remove-orphans 2>/dev/null || true
    docker compose build --no-cache
else
    log "增量构建..."
    docker compose build
fi

log "启动服务..."
docker compose up -d

# ---- Step 5: 等待健康检查 -----------------------------------------------------
log "等待服务就绪..."
for i in $(seq 1 30); do
    if curl -sf http://localhost/health &>/dev/null; then
        log "服务已就绪 ✓"
        break
    fi
    sleep 2
done

# ---- Step 6: 输出状态 ---------------------------------------------------------
echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  部署完成！${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "  访问地址:  http://$(curl -s ifconfig.me 2>/dev/null || echo '你的服务器IP')"
echo ""
echo "  常用命令:"
echo "    cd $PROJECT_DIR"
echo "    docker compose logs -f          # 查看实时日志"
echo "    docker compose restart          # 重启服务"
echo "    docker compose down             # 停止服务"
echo "    docker compose up -d            # 后台启动"
echo "    ./deploy.sh                     # 更新代码并重建"
echo ""
