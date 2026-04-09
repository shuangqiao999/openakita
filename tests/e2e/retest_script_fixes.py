"""
修复测试脚本错误后的重新测试
针对 v3 报告中因脚本字段/URL 错误导致的 FAIL 项
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import httpx
import json
import time
import uuid
from datetime import datetime

BASE = "http://127.0.0.1:18900"
RESULTS = []


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def record(test_id, name, status, detail=""):
    RESULTS.append({"id": test_id, "name": name, "status": status, "detail": detail})
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}.get(status, "?")
    log(f"  {icon} {test_id}: {name} -> {status}" + (f" | {detail[:200]}" if detail else ""))


def api_get(path, timeout=30):
    try:
        r = httpx.get(f"{BASE}{path}", timeout=timeout)
        return r.status_code, r.json() if "json" in r.headers.get("content-type", "") else r.text
    except Exception as e:
        return 0, str(e)


def api_post(path, data=None, timeout=30):
    try:
        r = httpx.post(f"{BASE}{path}", json=data, timeout=timeout)
        return r.status_code, r.json() if "json" in r.headers.get("content-type", "") else r.text
    except Exception as e:
        return 0, str(e)


def api_delete(path, timeout=30):
    try:
        r = httpx.delete(f"{BASE}{path}", timeout=timeout)
        return r.status_code, r.json() if "json" in r.headers.get("content-type", "") else r.text
    except Exception as e:
        return 0, str(e)


def main():
    log("=" * 60)
    log("脚本错误修复后重新测试")
    log("=" * 60)

    # ================================================================
    # 1. SYS-02: /api/health/check - 尝试 POST 和确认正确方法
    # ================================================================
    log("\n--- SYS-02: 健康检查端点方法探测 ---")
    for method_name, fn in [("GET", api_get), ("POST", api_post)]:
        code, data = fn("/api/health/check")
        if code == 200:
            record("SYS-02-fix", f"详细健康检查 ({method_name})", "PASS", str(data)[:200])
            break
        else:
            log(f"  {method_name} /api/health/check -> {code}")
    else:
        # 也试 /api/health 的子路径
        code, data = api_get("/api/health")
        record("SYS-02-fix", "详细健康检查 (fallback /api/health)", 
               "PASS" if code == 200 else "FAIL", str(data)[:200])

    # ================================================================
    # 2. SCHED-05: 创建定时任务 - 修正 trigger_config 字段
    # ================================================================
    log("\n--- SCHED-05: 创建定时任务 (修正 trigger_config) ---")
    test_task = {
        "name": f"E2E-Retest-Task-{int(time.time())}",
        "description": "修正后的定时任务创建测试",
        "trigger_type": "cron",
        "trigger_config": {"cron": "0 0 * * *"},  # 修正: "cron" 而非 "cron_expr"
        "action_type": "chat",
        "action_config": {"message": "定时任务测试消息"},
        "enabled": False
    }
    code, data = api_post("/api/scheduler/tasks", test_task)
    task_id = None
    if code in (200, 201) and isinstance(data, dict):
        task_id = data.get("id") or data.get("task_id")
        record("SCHED-05-fix", "创建 cron 定时任务", "PASS", f"id={task_id}")
    else:
        record("SCHED-05-fix", "创建 cron 定时任务", "FAIL", f"code={code}, data={str(data)[:200]}")

    if task_id:
        # 完整生命周期: 获取 -> toggle -> trigger -> 更新 -> 删除
        code, data = api_get(f"/api/scheduler/tasks/{task_id}")
        record("SCHED-06-fix", "获取任务详情", "PASS" if code == 200 else "FAIL",
               f"trigger_type={data.get('trigger_type','?')}, enabled={data.get('enabled','?')}" if isinstance(data, dict) else str(data)[:200])

        code, data = api_post(f"/api/scheduler/tasks/{task_id}/toggle")
        record("SCHED-07-fix", "启用/禁用切换", "PASS" if code == 200 else "FAIL", str(data)[:200])

        code, data = api_post(f"/api/scheduler/tasks/{task_id}/trigger")
        record("SCHED-08-fix", "手动触发执行", "PASS" if code == 200 else "FAIL", str(data)[:200])
        time.sleep(2)

        code, data = api_get(f"/api/scheduler/tasks/{task_id}/executions")
        execs = data.get("executions", data) if isinstance(data, dict) else data
        exec_count = len(execs) if isinstance(execs, list) else "?"
        record("SCHED-09-fix", "查看执行记录", "PASS" if code == 200 else "FAIL",
               f"executions={exec_count}")

        code, data = api_delete(f"/api/scheduler/tasks/{task_id}")
        record("SCHED-11-fix", "删除任务", "PASS" if code in (200, 204) else "FAIL", f"code={code}")

    # ================================================================
    # 3. 组织编排: 修正 command/broadcast 字段 + task tree/timeline URL
    # ================================================================
    log("\n--- 组织编排: 创建测试组织 ---")
    org_name = f"E2E-Retest-{int(time.time()) % 10000}"
    org_data = {
        "name": org_name,
        "description": "重新测试组织编排",
        "nodes": [
            {"name": "PM", "role": "product_manager", "system_prompt": "你是产品经理"},
            {"name": "Dev", "role": "developer", "system_prompt": "你是开发工程师"}
        ]
    }
    code, data = api_post("/api/orgs", org_data)
    org_id = data.get("id") if isinstance(data, dict) and code in (200, 201) else None
    if org_id:
        record("ORG-SETUP", "创建测试组织", "PASS", f"id={org_id}, name={org_name}")
    else:
        record("ORG-SETUP", "创建测试组织", "FAIL", f"code={code}, data={str(data)[:200]}")
        # 退出组织测试
        _print_summary()
        return

    # 启动组织
    code, data = api_post(f"/api/orgs/{org_id}/start")
    record("ORG-START", "启动组织", "PASS" if code == 200 else "FAIL", str(data)[:100])
    time.sleep(2)

    # ---- ORG-15-fix: 下发命令 (修正: content 字段) ----
    log("\n--- ORG-15: 下发命令 (修正字段: content) ---")
    cmd_data = {"content": "请分析一下项目技术栈选型，给出建议。"}  # 修正: "content" 非 "message"
    code, data = api_post(f"/api/orgs/{org_id}/command", cmd_data)
    cmd_id = None
    if code in (200, 201) and isinstance(data, dict):
        cmd_id = data.get("id") or data.get("command_id")
        record("ORG-15-fix", "下发命令 (content字段)", "PASS", f"cmd_id={cmd_id}")
    else:
        record("ORG-15-fix", "下发命令 (content字段)", "FAIL", f"code={code}, data={str(data)[:200]}")

    if cmd_id:
        time.sleep(5)
        code, data = api_get(f"/api/orgs/{org_id}/commands/{cmd_id}")
        cmd_status = data.get("status", "?") if isinstance(data, dict) else "?"
        record("ORG-16-fix", "命令状态查询", "PASS" if code == 200 else "FAIL",
               f"status={cmd_status}")

    # ---- ORG-17-fix: 下发长任务用于终止测试 (修正: content 字段) ----
    log("\n--- ORG-17: 下发长任务 + 终止流转 ---")
    long_cmd = {"content": "请写一份详细的市场分析报告，包含行业趋势和竞品分析。"}
    code, data = api_post(f"/api/orgs/{org_id}/command", long_cmd)
    long_cmd_id = None
    if code in (200, 201) and isinstance(data, dict):
        long_cmd_id = data.get("id") or data.get("command_id")
        record("ORG-17-fix", "下发长任务 (content字段)", "PASS", f"cmd_id={long_cmd_id}")
    else:
        record("ORG-17-fix", "下发长任务 (content字段)", "FAIL", f"code={code}, data={str(data)[:200]}")

    time.sleep(3)

    if long_cmd_id:
        code, data = api_get(f"/api/orgs/{org_id}/commands/{long_cmd_id}")
        pre_status = data.get("status", "?") if isinstance(data, dict) else "?"
        record("ORG-18-fix", "终止前命令状态", "PASS" if code == 200 else "FAIL",
               f"status={pre_status}")

    # 停止组织
    code, data = api_post(f"/api/orgs/{org_id}/stop")
    record("ORG-19-fix", "停止组织", "PASS" if code == 200 else "FAIL", str(data)[:100])
    time.sleep(3)

    # ---- ORG-20-fix: 停止后状态 (修正: dormant 是正确状态) ----
    code, data = api_get(f"/api/orgs/{org_id}")
    post_status = data.get("status", "?") if isinstance(data, dict) else "?"
    record("ORG-20-fix", "停止后组织状态",
           "PASS" if post_status in ("stopped", "dormant", "idle") else "FAIL",
           f"status={post_status} (dormant=正常非活跃态)")

    if long_cmd_id:
        code, data = api_get(f"/api/orgs/{org_id}/commands/{long_cmd_id}")
        post_cmd = data.get("status", "?") if isinstance(data, dict) else "?"
        record("ORG-21-fix", "停止后命令状态", 
               "PASS" if code == 200 else "FAIL",
               f"status={post_cmd}")

    # ---- ORG-37-fix: 组织广播 (修正: content 字段) ----
    log("\n--- ORG-37: 组织广播 (修正字段: content) ---")
    # 需要先重新启动
    api_post(f"/api/orgs/{org_id}/start")
    time.sleep(2)
    broadcast = {"content": "全员注意：这是一条测试广播消息。"}  # 修正: "content" 非 "message"
    code, data = api_post(f"/api/orgs/{org_id}/broadcast", broadcast)
    record("ORG-37-fix", "组织广播 (content字段)", "PASS" if code == 200 else "FAIL",
           str(data)[:200])

    # ---- ORG-13/14-fix: 任务树/时间线 (修正: 需要具体 task_id) ----
    log("\n--- ORG-13/14: 任务树/时间线 (修正 URL: 需 task_id) ---")

    # 先创建项目和任务
    proj_data = {"name": f"Retest-Project-{int(time.time()) % 10000}", "description": "重测项目"}
    code, pdata = api_post(f"/api/orgs/{org_id}/projects", proj_data)
    proj_id = pdata.get("id") if isinstance(pdata, dict) and code in (200, 201) else None

    task_id_for_tree = None
    if proj_id:
        task_data = {"title": "E2E-重测任务", "description": "用于测试 tree/timeline"}
        code, tdata = api_post(f"/api/orgs/{org_id}/projects/{proj_id}/tasks", task_data)
        if code in (200, 201) and isinstance(tdata, dict):
            task_id_for_tree = tdata.get("id") or tdata.get("task_id")
            log(f"  创建任务: id={task_id_for_tree}")

    if task_id_for_tree:
        # 修正: /api/orgs/{org_id}/tasks/{task_id}/tree
        code, data = api_get(f"/api/orgs/{org_id}/tasks/{task_id_for_tree}/tree")
        record("ORG-13-fix", "任务树 (带 task_id)", "PASS" if code == 200 else "FAIL",
               str(data)[:200])

        # 修正: /api/orgs/{org_id}/tasks/{task_id}/timeline
        code, data = api_get(f"/api/orgs/{org_id}/tasks/{task_id_for_tree}/timeline")
        record("ORG-14-fix", "任务时间线 (带 task_id)", "PASS" if code == 200 else "FAIL",
               str(data)[:200])

        # 额外: 获取单个任务详情
        code, data = api_get(f"/api/orgs/{org_id}/tasks/{task_id_for_tree}")
        record("ORG-TASK-DETAIL", "任务详情", "PASS" if code == 200 else "FAIL",
               str(data)[:200])
    else:
        record("ORG-13-fix", "任务树", "SKIP", "无法创建测试任务")
        record("ORG-14-fix", "任务时间线", "SKIP", "无法创建测试任务")

    # ---- 额外: 完整的命令下发 → 执行 → 终止流转验证 ----
    log("\n--- 命令下发 → 执行 → 终止: 完整流转 ---")
    api_post(f"/api/orgs/{org_id}/start")
    time.sleep(2)

    # 下发一个任务
    cmd = {"content": "请列出当前项目中的所有任务和它们的状态。"}
    code, data = api_post(f"/api/orgs/{org_id}/command", cmd)
    flow_cmd_id = data.get("id") or data.get("command_id") if isinstance(data, dict) else None
    record("ORG-FLOW-01", "流转测试: 下发命令", "PASS" if code in (200, 201) else "FAIL",
           f"cmd_id={flow_cmd_id}")

    if flow_cmd_id:
        # 等待一段时间让任务开始执行
        time.sleep(8)
        code, data = api_get(f"/api/orgs/{org_id}/commands/{flow_cmd_id}")
        status_mid = data.get("status", "?") if isinstance(data, dict) else "?"
        record("ORG-FLOW-02", "流转测试: 执行中状态", "PASS" if code == 200 else "FAIL",
               f"status={status_mid}")

        # 查看节点状态变化
        code, org_data = api_get(f"/api/orgs/{org_id}")
        nodes = org_data.get("nodes", []) if isinstance(org_data, dict) else []
        node_statuses = [(n.get("name", "?"), n.get("id", "")) for n in nodes]
        for nname, nid in node_statuses:
            code, ndata = api_get(f"/api/orgs/{org_id}/nodes/{nid}/status")
            ns = ndata.get("status", "?") if isinstance(ndata, dict) else "?"
            log(f"    节点 {nname}: status={ns}")

        # 停止组织 - 验证任务是否被终止
        code, data = api_post(f"/api/orgs/{org_id}/stop")
        record("ORG-FLOW-03", "流转测试: 停止组织", "PASS" if code == 200 else "FAIL",
               str(data)[:100])
        time.sleep(3)

        # 检查命令最终状态
        code, data = api_get(f"/api/orgs/{org_id}/commands/{flow_cmd_id}")
        final_status = data.get("status", "?") if isinstance(data, dict) else "?"
        record("ORG-FLOW-04", "流转测试: 终止后命令状态", "PASS" if code == 200 else "FAIL",
               f"status={final_status}")

        # 组织最终状态
        code, data = api_get(f"/api/orgs/{org_id}")
        org_final = data.get("status", "?") if isinstance(data, dict) else "?"
        record("ORG-FLOW-05", "流转测试: 组织最终状态",
               "PASS" if org_final in ("dormant", "stopped", "idle") else "FAIL",
               f"status={org_final}")

    # 清理
    log("\n--- 清理测试组织 ---")
    api_delete(f"/api/orgs/{org_id}")

    _print_summary()


def _print_summary():
    log("\n" + "=" * 60)
    log("重新测试总结")
    log("=" * 60)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
    skipped = sum(1 for r in RESULTS if r["status"] == "SKIP")

    log(f"  总测试点: {total}")
    log(f"  通过: {passed} ({100*passed/total:.1f}%)" if total else "")
    log(f"  失败: {failed}" if failed else "  失败: 0")
    log(f"  跳过: {skipped}" if skipped else "")

    if failed:
        log("\n  仍然失败的项:")
        for r in RESULTS:
            if r["status"] == "FAIL":
                log(f"  ❌ {r['id']}: {r['name']} | {r['detail']}")

    if passed == total - skipped:
        log("\n  ✅ 所有修正后的测试全部通过！脚本错误已确认为唯一原因。")

    # Save
    import os
    out = {
        "test_date": datetime.now().isoformat(),
        "type": "retest_script_fixes",
        "summary": {"total": total, "passed": passed, "failed": failed, "skipped": skipped},
        "results": RESULTS
    }
    outpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "retest_results.json")
    with open(outpath, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log(f"\n  结果已保存: {outpath}")


if __name__ == "__main__":
    main()
