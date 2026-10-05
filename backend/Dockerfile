# 榜样CRM 后端生产镜像
# 包含：FastAPI + ffmpeg（视频抽音轨）+ Playwright Chromium（HTML报告导出PDF）+ 中文字体
#       + dsh agent 运行时（Node.js + @deepseek-ai/dsh + PG 会话持久化插件，阶段 1）
FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# ffmpeg：视频抽音轨；curl：健康检查；postgresql-client：远程 PG 的 pg_dump/psql（备份/恢复）
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Node.js 24（dsh 运行时要求 Node >=22.19 或 >=24）：直接从官方镜像复制 node
# 与全局库，避免引入 NodeSource 外部仓库。pnpm 是 dsh 插件管理器的硬依赖
# （dsh plugin 命令把参数原样转发给 pnpm，pnpm 不在 PATH 会报 exitCode 127）。
COPY --from=node:24-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:24-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install -g pnpm@10 \
    && node --version && pnpm --version

# dsh 运行时：npm 包 @deepseek-ai/dsh（版本与开发机锁定一致 0.1.7-rc.2），
# 装到固定目录 /opt/dsh-runtime，DSH_BIN 指向其中的 Linux shim。
# （Windows 开发机必须指 .cmd shim，Linux 容器用无后缀 shim。）
RUN mkdir -p /opt/dsh-runtime \
    && cd /opt/dsh-runtime \
    && npm init -y >/dev/null \
    && npm install @deepseek-ai/dsh@0.1.7-rc.2

# PG 会话持久化插件 @kbcrm/dsh-session-persistence-pg（源码在仓库
# dsh/session-persistence-pg，dist 构建产物 vendor 化进 backend/vendor/）。
# 只装生产依赖（pg 等）；对 dsh 的 peer 依赖由 dsh 运行时自身提供。
# 运行时由 docker-entrypoint.sh 经 `dsh plugin --profile acp add` 以 link:
# 方式注册进 acp profile（引用本路径，因此本目录在镜像内不可移动）。
COPY vendor/dsh-session-persistence-pg /opt/dsh-plugins/session-persistence-pg
RUN cd /opt/dsh-plugins/session-persistence-pg && npm ci --omit=dev

COPY requirements.txt .
RUN pip install -r requirements.txt

# ACP 客户端 agent-client-protocol 已随 requirements.txt 安装（dsh 基座阶段 2 起
# 桥接层改用标准 ACP，`dsh --profile acp`）。

# Playwright 无头 Chromium（--with-deps 自动装系统依赖）+ 中文字体（PDF 渲染中文必需）。
# 浏览器安装路径显式固定为 /app/data/ms-playwright：app/config.py 运行时会
# setdefault PLAYWRIGHT_BROWSERS_PATH 指向同一位置，构建期不设置该变量的话浏览器会
# 装进 /root/.cache（root 专属路径），运行时找不到且非 root 用户无权读取。
ENV PLAYWRIGHT_BROWSERS_PATH=/app/data/ms-playwright
RUN python -m playwright install --with-deps chromium \
    && apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app

# dsh base patch 母版：容器首启动时由 entrypoint 播种到 $DSH_PATCHES_DIR/base.yml
COPY docker/dsh-base.yml /opt/dsh-patches/base.yml
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# dsh 可执行文件默认路径（compose 可覆盖）；DSH_AGENT_ENABLED 等开关在 compose 里配
ENV DSH_BIN=/opt/dsh-runtime/node_modules/.bin/dsh

# 非 root 运行：建 app 用户（HOME=/app，pnpm/npm/dsh 的缓存与 store 都写 HOME，
# 必须可写），预建全部运行时数据目录并整体 chown。
# named volume（uploads/brand/backups/avatars/sync_app/dsh-home，见 docker-compose.prod.yml）
# 首次挂载时会从镜像对应目录复制内容与属主，因此镜像内预建 + chown 即可保证卷可写；
# entrypoint 的播种 base.yml / dsh plugin add 也只写 /app/data/dsh，非 root 下可跑。
# 注意：若改用 bind mount 挂 /app/data/*，宿主机目录属主不会自动修正（entrypoint 非 root
# 无法 chown），需宿主机侧先把目录 chown 成容器内 app 用户的 uid（id app 可查）。
# 升级注意：从 root 运行的旧版本升级时，已有 named volume 里的文件属主仍是 root，
# 需一次性修复，例如：
#   docker compose -f docker-compose.prod.yml run --rm --user root backend \
#     chown -R app:app /app/data
RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app app \
    && mkdir -p /app/data/uploads /app/data/brand /app/data/backups \
               /app/data/avatars /app/data/sync_app \
               /app/data/dsh/home /app/data/dsh/patches /app/data/dsh/workspace \
               /app/data/mcp-workspace /app/data/ocr-models \
    && chown -R app:app /app
ENV HOME=/app
USER app

# 健康检查：/health 含 DB ping，DB 不可达返回 503 即判定 unhealthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8100/health || exit 1

EXPOSE 8100
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
