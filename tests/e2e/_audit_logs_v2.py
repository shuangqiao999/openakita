"""LLM Debug Log Audit v2 - correct structure"""
import json, os, sys, io, glob, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DEBUG_DIR = r"D:\OpenAkita\data\llm_debug"

files = sorted(glob.glob(os.path.join(DEBUG_DIR, "llm_request_*.json")),
               key=os.path.getmtime, reverse=True)

# Find test conversation requests
test_requests = []
for f in files[:300]:
    try:
        with open(f, "r", encoding="utf-8") as fh:
            raw = fh.read()
        if "Alpha-7" in raw or "test_chat_" in raw:
            data = json.loads(raw)
            test_requests.append((f, data))
    except Exception:
        pass

print(f"Found {len(test_requests)} test conversation request files")

# Also find large main requests for system prompt audit
main_requests = []
for f in files[:50]:
    try:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        llm_req = data.get("llm_request", {})
        system_text = llm_req.get("system", "")
        if isinstance(system_text, list):
            system_text = " ".join(s.get("text", "") if isinstance(s, dict) else str(s) for s in system_text)
        if len(system_text) > 5000:
            main_requests.append((f, data, len(system_text)))
    except Exception:
        pass

print(f"Found {len(main_requests)} main conversation requests (system > 5KB)")

# ====== System Prompt Audit ======
if main_requests:
    f, data, slen = main_requests[0]
    print(f"\n{'='*60}")
    print(f"SYSTEM PROMPT AUDIT: {os.path.basename(f)}")
    print(f"System prompt length: {slen} chars")
    print(f"{'='*60}")
    
    llm_req = data.get("llm_request", {})
    system_text = llm_req.get("system", "")
    if isinstance(system_text, list):
        system_text = " ".join(s.get("text", "") if isinstance(s, dict) else str(s) for s in system_text)
    
    stats = data.get("stats", {})
    print(f"Stats: {json.dumps(stats, ensure_ascii=False)}")
    
    # Check sections
    checks = {
        "当前会话": "当前会话" in system_text or "session_id" in system_text,
        "系统概况": "系统概况" in system_text or "powered by" in system_text.lower(),
        "对话上下文约定": "对话上下文" in system_text or "上下文约定" in system_text,
        "记忆系统": "记忆系统" in system_text or "你的记忆" in system_text,
        "无仅供参考": "仅供参考" not in system_text,
    }
    
    for name, ok in checks.items():
        print(f"  [{'OK' if ok else 'FAIL'}] {name}")
    
    # Print system prompt sections (first 2000 chars)
    print(f"\n--- System Prompt Preview (first 2000 chars) ---")
    print(system_text[:2000])
    print("...")
    
    # Check tools
    tools = llm_req.get("tools", [])
    tool_names = []
    for t in tools:
        if isinstance(t, dict):
            name = t.get("name", "")
            if not name and "function" in t:
                name = t["function"].get("name", "")
            tool_names.append(name)
    
    print(f"\n--- Tools ({len(tools)}) ---")
    print(f"Tool names: {tool_names}")
    has_session_ctx = "get_session_context" in tool_names
    print(f"[{'OK' if has_session_ctx else 'MISSING'}] get_session_context")
    has_delegate = any("delegate" in n for n in tool_names)
    print(f"[{'OK' if has_delegate else 'N/A'}] delegate tools")

# ====== Test Conversation Messages Audit ======
if test_requests:
    # Sort by timestamp to get chronological order
    test_requests.sort(key=lambda x: x[1].get("timestamp", ""))
    
    print(f"\n{'='*60}")
    print(f"TEST CONVERSATION MESSAGES AUDIT")
    print(f"{'='*60}")
    
    for i, (f, data) in enumerate(test_requests):
        llm_req = data.get("llm_request", {})
        msgs = llm_req.get("messages", [])
        stats = data.get("stats", {})
        timestamp = data.get("timestamp", "?")
        
        print(f"\n--- Request {i+1}: {os.path.basename(f)} ({timestamp}) ---")
        print(f"Messages: {stats.get('messages_count', len(msgs))}, Tokens: {stats.get('total_estimated_tokens', '?')}")
        
        user_msgs = [m for m in msgs if m.get("role") == "user"]
        asst_msgs = [m for m in msgs if m.get("role") == "assistant"]
        print(f"User msgs: {len(user_msgs)}, Assistant msgs: {len(asst_msgs)}")
        
        # Check timestamps in messages
        has_timestamps = False
        has_latest = False
        has_double = False
        has_compressed = False
        
        for m in msgs:
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            if not isinstance(content, str):
                continue
            if re.search(r'\[\d{2}:\d{2}\]', content):
                has_timestamps = True
            if "[最新消息]" in content:
                has_latest = True
            if re.search(r'\[\d{2}:\d{2}\]\s*\[\d{2}:\d{2}\]', content):
                has_double = True
            if "context_compressed" in content.lower() or "[上下文压缩]" in content or "compressed" in content.lower():
                has_compressed = True
        
        print(f"[{'OK' if has_timestamps else 'MISSING'}] Timestamps")
        print(f"[{'OK' if has_latest else 'MISSING'}] [最新消息] marker")
        print(f"[{'OK' if not has_double else 'BAD'}] No double timestamps")
        print(f"[{'INFO' if has_compressed else 'OK'}] Context compression: {'YES' if has_compressed else 'no'}")
        
        # Check if early facts survive
        all_content = ""
        for m in msgs:
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            if isinstance(content, str):
                all_content += content
        
        has_alpha7 = "Alpha-7" in all_content
        has_xiaowang = "小王" in all_content or "测试员" in all_content
        has_python_fact = "Python" in all_content
        
        print(f"[{'OK' if has_alpha7 else 'LOST'}] Alpha-7 in messages")
        print(f"[{'OK' if has_xiaowang else 'LOST'}] 测试员小王 in messages")
        
        # Print message order
        print(f"\nMessage order:")
        for j, m in enumerate(msgs):
            role = m.get("role", "?")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            if isinstance(content, str):
                preview = content[:80].replace("\n", " ")
            else:
                preview = str(content)[:80]
            print(f"  [{j}] {role}: {preview}")
        
        if i >= 4:
            print(f"\n... showing first 5 of {len(test_requests)} requests")
            break

# Server logs
print(f"\n{'='*60}")
print(f"SERVER LOG AUDIT")
print(f"{'='*60}")

log_dir = r"D:\OpenAkita\data"
for fname in os.listdir(log_dir):
    if fname.endswith(".log") or "log" in fname.lower():
        fpath = os.path.join(log_dir, fname)
        if os.path.isfile(fpath):
            print(f"\nLog file: {fname} ({os.path.getsize(fpath)} bytes)")

# Check if there's a logs subdirectory
logs_dir = os.path.join(log_dir, "logs")
if os.path.isdir(logs_dir):
    for fname in sorted(os.listdir(logs_dir), reverse=True)[:5]:
        fpath = os.path.join(logs_dir, fname)
        size = os.path.getsize(fpath)
        print(f"  {fname} ({size} bytes)")
        if fname.endswith(".log") and size > 0:
            with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            # Show last 20 lines with errors/warnings
            error_lines = [l.strip() for l in lines if "ERROR" in l or "WARNING" in l or "error" in l.lower()]
            if error_lines:
                print(f"  Recent errors ({len(error_lines)} total):")
                for el in error_lines[-5:]:
                    print(f"    {el[:200]}")
