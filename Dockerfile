# =============================================================================
#  Steam 游戏推荐 Agent — Docker 镜像
# =============================================================================
#  构建：  docker build -t steam-game-agent .
#  运行：  docker run -d -p 8000:8000 --env-file .env steam-game-agent
# =============================================================================

FROM python:3.11-slim-bookworm

# ---- 国内镜像加速（解决 PyPI / HuggingFace 被墙问题）----
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV HF_ENDPOINT=https://hf-mirror.com

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- 安装 Python 依赖（利用 Docker 层缓存）----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 预下载 sentence-transformers 模型（避免容器启动时下载）----
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# ---- 拷贝应用代码 ----
COPY . .

# 创建日志目录
RUN mkdir -p /app/data/logs

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

# 运行服务（生产模式，无 --reload）
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
