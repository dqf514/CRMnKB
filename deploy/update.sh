#!/usr/bin/env bash
# CRMnKB 系统更新脚本（裸机部署）——由「系统设置 → 系统更新」按钮触发
#
# 安装：
#   1. 按实际路径调整下方 REPO_DIR / SERVICE / FRONTEND_DIST
#   2. chmod +x deploy/update.sh
#   3. backend/.env 里配置 UPDATE_SCRIPT=/opt/crmnkb/deploy/update.sh
#   4. 服务用户需要免密重启权限（/etc/sudoers.d/crmnkb）：
#        crmnkb ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart crmnkb-backend
#
# 流程：git pull → 按需 pip install / 前端构建 → 延迟 3 秒重启后端
# （延迟重启是为了让触发本次更新的 HTTP 响应先返回）
set -euo pipefail

REPO_DIR=/opt/crmnkb
SERVICE=crmnkb-backend
# 后端托管前端模式（.env 的 FRONTEND_DIST 非空）时填该路径，否则留空跳过前端构建
FRONTEND_DIST=

cd "$REPO_DIR"
BEFORE=$(git rev-parse HEAD)

echo "== git pull =="
git pull --ff-only

AFTER=$(git rev-parse HEAD)
if [ "$BEFORE" = "$AFTER" ]; then
  echo "已是最新版本（$AFTER），无需更新"
  exit 0
fi
echo "更新 $BEFORE -> $AFTER"
CHANGED=$(git diff --name-only "$BEFORE" "$AFTER")

echo "== 后端依赖 =="
if echo "$CHANGED" | grep -q '^backend/requirements.txt$'; then
  backend/.venv/bin/pip install -r backend/requirements.txt
else
  echo "requirements.txt 未变化，跳过"
fi

echo "== 前端构建 =="
if [ -n "$FRONTEND_DIST" ] && echo "$CHANGED" | grep -q '^frontend/'; then
  (cd frontend && npm ci && npm run build)
  rsync -a --delete frontend/dist/ "$FRONTEND_DIST/"
else
  echo "前端未变化或未启用后端托管，跳过"
fi

echo "== 3 秒后重启后端（$SERVICE） =="
# 重启走 sudo（脚本由服务用户 crmnkb 触发，需 /etc/sudoers.d/crmnkb 免密授权）
nohup bash -c "sleep 3 && sudo systemctl restart $SERVICE" >/dev/null 2>&1 &
echo "更新完成，后端即将重启（页面稍后刷新即可）"
