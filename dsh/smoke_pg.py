#!/usr/bin/env python3
"""冒烟：Python SDK -> npm dsh CLI -> PG 会话持久化插件。

验证点：
1. initialize 成功（插件树挂载，patch 生效）；
2. dsh stderr 无 "did not activate" 警告（PG 插件真正加载）；
3. 会话事件落入 PG 的 dsh_ 前缀表。
无 DEEPSEEK_API_KEY 时跳过真实模型调用，只做 1+2 并尝试一轮失败调用产生事件。
"""

from __future__ import annotations

import os
import subprocess
import sys

from deepseek_harness import DeepSeekHarness, DeepSeekHarnessConfig

ROOT = r"D:\AI\CRMnKB\dsh"
PATCH = rf"{ROOT}\patches\pg-session-persistence.patch.yml"
# Windows 下 dsh_bin 指 .cmd  shim（Popen 可直接执行）；.bin/dsh 是 sh 脚本，不可用于 dsh_bin。
DSH_BIN = rf"{ROOT}\runtime\node_modules\.bin\dsh.cmd"
PG_URL = os.environ.get("DSH_PG_URL", "postgresql://crm:crm123@localhost:5433/crmnkb")

config = DeepSeekHarnessConfig(
    provider="deepseek-official",
    model="deepseek-v4-flash",
    cwd=ROOT,
    dsh_bin=DSH_BIN,
    dsh_home=rf"{ROOT}\home",
    profile="sdk",
    patches=(PATCH,),
    initialize_timeout_seconds=120.0,
    request_timeout_seconds=60.0,
    env={"DSH_PG_URL": PG_URL},
)

harness = DeepSeekHarness(config)
try:
    harness.start()
    print("[smoke] initialize OK（插件树已挂载）")
except Exception:
    harness.close()
    raise

boot_warnings = [
    line for line in list(getattr(harness.client, "_stderr_lines", []))
    if "did not activate" in line or "failed to import" in line
]
if boot_warnings:
    print("[smoke] 启动警告（插件未激活）:")
    print("\n".join(boot_warnings))
    harness.close()
    sys.exit(1)
print("[smoke] 无启动警告，PG 插件已激活")

if os.environ.get("DEEPSEEK_API_KEY"):
    result = harness.run("用一句话介绍你自己。")
    print("[smoke] run OK, final_response:", result.final_response[:120])
else:
    print("[smoke] 无 DEEPSEEK_API_KEY，尝试一轮调用以产生会话事件（预期失败）……")
    try:
        result = harness.run("hello")
        print("[smoke] run 返回：", (result.final_response or "")[:80])
    except Exception as error:  # noqa: BLE001
        print(f"[smoke] run 按预期失败（无 API key）：{type(error).__name__}")

harness.close()

# 校验 PG 落库：dsh_ 前缀两张表里有会话行与事件行。
check = subprocess.run(
    [
        "docker", "exec", "crmnkb-postgres",
        "psql", "-U", "crm", "-d", "crmnkb", "-Atc",
        "SELECT (SELECT count(*) FROM dsh_session_headers), "
        "(SELECT count(*) FROM dsh_session_events), "
        "(SELECT coalesce(string_agg(DISTINCT event->>'type', ','), '') FROM dsh_session_events)",
    ],
    capture_output=True, text=True, check=False,
)
print("[smoke] psql:", check.stdout.strip() or check.stderr.strip())
if check.returncode == 0 and check.stdout.strip() and not check.stdout.startswith("0|0"):
    print("[smoke] PG 持久化验证通过")
else:
    print("[smoke] PG 中暂无会话数据")
    sys.exit(1)
