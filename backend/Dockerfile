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
# 运行时由 docker-entrypoint.sh 经 `dsh plugin --profile sdk add` 以 link:
# 方式注册进 sdk profile（引用本路径，因此本目录在镜像内不可移动）。
COPY vendor/dsh-session-persistence-pg /opt/dsh-plugins/session-persistence-pg
RUN cd /opt/dsh-plugins/session-persistence-pg && npm ci --omit=dev

COPY requirements.txt .
RUN pip install -r requirements.txt

# ACP 客户端 agent-client-protocol 已随 requirements.txt 安装（dsh 基座阶段 2 起
# 桥接层改用标准 ACP，`dsh --profile acp`）。vendor/deepseek-harness-sdk/ 目录
# 保留无害（阶段 1 的 SDK 副本），但不再安装进镜像。

# Playwright 无头 Chromium（--with-deps 自动装系统依赖）+ 中文字体（PDF 渲染中文必需）
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

EXPOSE 8100
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
