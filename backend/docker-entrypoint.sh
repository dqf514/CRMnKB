#!/bin/sh
# 后端容器入口：启动前完成 dsh 运行环境的一次性初始化（幂等），然后 exec 主进程。
# 仅当 DSH_AGENT_ENABLED=true 时执行；其余情况直接透传启动命令。
set -e

if [ "$DSH_AGENT_ENABLED" = "true" ]; then
  DSH_HOME="${DSH_HOME:-/app/data/dsh/home}"
  DSH_PATCHES_DIR="${DSH_PATCHES_DIR:-/app/data/dsh/patches}"
  DSH_BIN="${DSH_BIN:-/opt/dsh-runtime/node_modules/.bin/dsh}"
  mkdir -p "$DSH_HOME" "$DSH_PATCHES_DIR"

  # 1) 播种 base.yml（PG 会话持久化 patch）：仅当文件不存在时，
  #    已存在的文件不覆盖，允许运维按环境手工调整。
  if [ ! -f "$DSH_PATCHES_DIR/base.yml" ]; then
    cp /opt/dsh-patches/base.yml "$DSH_PATCHES_DIR/base.yml"
    echo "[entrypoint] 已播种 dsh base patch: $DSH_PATCHES_DIR/base.yml"
  fi

  # 2) 把 PG 会话持久化插件注册进 acp profile（阶段 2 起桥接走 `dsh --profile acp`；
  #    旧 sdk profile 的注册态不再使用，DSH_HOME 沿用旧卷时互不干扰）。
  #    dsh plugin 命令把参数原样转发给 pnpm（profile 目录首次使用自动初始化）；
  #    link: 方式引用 /opt/dsh-plugins 下的插件目录，其生产依赖已在构建期装好，
  #    无需访问 npm registry。以标记文件保证幂等；
  #    失败仅告警不阻塞后端启动（agent 功能首次使用时才会暴露错误）。
  if [ ! -f "$DSH_HOME/.pg-plugin-installed-acp" ]; then
    if "$DSH_BIN" plugin --profile acp add /opt/dsh-plugins/session-persistence-pg; then
      touch "$DSH_HOME/.pg-plugin-installed-acp"
      echo "[entrypoint] dsh PG 持久化插件已注册进 acp profile"
    else
      echo "[entrypoint] 警告：dsh 插件注册失败，agent 会话将无法持久化到 PG。" >&2
      echo "[entrypoint] 手工重试：docker exec crmnkb-backend $DSH_BIN plugin --profile acp add /opt/dsh-plugins/session-persistence-pg" >&2
    fi
  fi
fi

exec "$@"
