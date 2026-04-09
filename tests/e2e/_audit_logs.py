"""LLM Debug Log Audit for test conversations"""
import json, os, sys, io, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DEBUG_DIR = r"D:\OpenAkita\data\llm_debug"

# Find the largest recent request files (these are main conversation requests)
files = glob.glob(os.path.join(DEBUG_DIR, "llm_request_*.json"))
files.sort(key=os.path.getmtime, reverse=True)

# Pick files with system prompt > 5000 chars (main conversation requests)
main_requests = []
for f in files[:50]:
    try:
        with open(f, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        system_msgs = [m for m in data.get("messages", []) if m.get("role") == "system"]
        system_len = sum(len(m.get("content", "")) for m in system_msgs)
        if system_len > 5000:
            main_requests.append((f, data, system_len))
    except Exception:
        pass

print(f"Found {len(main_requests)} main conversation requests in last 50 files")
print()

# Audit the most recent main request
if main_requests:
    f, data, slen = main_requests[0]
    print(f"=== Auditing: {os.path.basename(f)} (system_len={slen}) ===")
    
    # 1. Check system prompt sections
    system_text = ""
    for m in data.get("messages", []):
        if m.get("role") == "system":
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            system_text += content
    
    print("\n--- System Prompt Audit ---")
    
    # Check session metadata
    has_session = "当前会话" in system_text or "session" in system_text.lower()
    print(f"[{'OK' if has_session else 'MISSING'}] 当前会话 metadata")
    
    # Check system overview
    has_overview = "系统概况" in system_text or "powered by" in system_text.lower()
    print(f"[{'OK' if has_overview else 'MISSING'}] 系统概况 / powered by model")
    
    # Check conversation context rules
    has_context_rules = "对话上下文" in system_text or "context" in system_text.lower()
    print(f"[{'OK' if has_context_rules else 'MISSING'}] 对话上下文约定")
    
    # Check memory system
    has_memory = "记忆系统" in system_text or "memory" in system_text.lower()
    print(f"[{'OK' if has_memory else 'MISSING'}] 记忆系统")
    
    # Check for "仅供参考" (should NOT exist)
    has_fyr = "仅供参考" in system_text
    print(f"[{'OK' if not has_fyr else 'BAD'}] 无'仅供参考'字样")
    
    # 2. Check messages structure
    messages = data.get("messages", [])
    print(f"\n--- Messages Structure Audit ---")
    print(f"Total messages: {len(messages)}")
    
    user_msgs = [m for m in messages if m.get("role") == "user"]
    assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
    print(f"User messages: {len(user_msgs)}")
    print(f"Assistant messages: {len(assistant_msgs)}")
    
    # Check timestamps
    timestamp_pattern = False
    latest_marker = False
    double_timestamp = False
    
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        if not isinstance(content, str):
            continue
            
        import re
        if re.search(r'\[\d{2}:\d{2}\]', content):
            timestamp_pattern = True
        if "[最新消息]" in content:
            latest_marker = True
        if re.search(r'\[\d{2}:\d{2}\]\s*\[\d{2}:\d{2}\]', content):
            double_timestamp = True
    
    print(f"[{'OK' if timestamp_pattern else 'MISSING'}] 时间戳注入 [HH:MM]")
    print(f"[{'OK' if latest_marker else 'MISSING'}] [最新消息]标记")
    print(f"[{'OK' if not double_timestamp else 'BAD'}] 无双重时间戳")
    
    # 3. Check tool definitions
    tools = data.get("tools", data.get("tool_definitions", []))
    tool_names = [t.get("name", t.get("function", {}).get("name", "?")) for t in tools]
    print(f"\n--- Tool Definitions Audit ---")
    print(f"Total tools: {len(tools)}")
    
    has_session_ctx = "get_session_context" in tool_names
    print(f"[{'OK' if has_session_ctx else 'MISSING'}] get_session_context tool")
    
    has_delegate = any("delegate" in n for n in tool_names)
    print(f"[{'OK' if has_delegate else 'N/A'}] delegate_to_agent / delegate_parallel")
    
    # Check delegate tools for context param
    if has_delegate:
        for t in tools:
            tname = t.get("name", t.get("function", {}).get("name", ""))
            if "delegate" in tname:
                params = t.get("parameters", t.get("function", {}).get("parameters", {}))
                props = params.get("properties", {})
                has_ctx = "context" in props
                print(f"  [{tname}] has 'context' param: {has_ctx}")
    
    # 4. Print message order summary
    print(f"\n--- Message Order (last 10) ---")
    for m in messages[-10:]:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        if isinstance(content, str):
            preview = content[:80].replace("\n", " ")
        else:
            preview = str(content)[:80]
        print(f"  [{role}] {preview}")

    # 5. Check context compression indicators
    print(f"\n--- Context Analysis ---")
    total_content_len = 0
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        if isinstance(content, str):
            total_content_len += len(content)
    print(f"Total content length: {total_content_len} chars")
    
    # Check if early messages are present
    if len(user_msgs) >= 2:
        first_user = user_msgs[0].get("content", "")
        if isinstance(first_user, list):
            first_user = " ".join(b.get("text", "") for b in first_user if isinstance(b, dict))
        print(f"First user message preview: {str(first_user)[:100]}")

# Also audit a request from our test conversation (look for "Alpha-7" or "测试员小王")
print("\n\n=== Looking for test conversation requests ===")
test_requests = []
for f in files[:200]:
    try:
        with open(f, "r", encoding="utf-8") as fh:
            raw = fh.read()
        if "Alpha-7" in raw or "测试员小王" in raw:
            data = json.loads(raw)
            test_requests.append((f, data))
    except Exception:
        pass

print(f"Found {len(test_requests)} requests mentioning test conversation")
if test_requests:
    # Analyze the latest one
    f, data = test_requests[0]
    messages = data.get("messages", [])
    print(f"\nLatest: {os.path.basename(f)}")
    print(f"Messages: {len(messages)}")
    
    user_msgs = [m for m in messages if m.get("role") == "user"]
    print(f"User turns in this request: {len(user_msgs)}")
    
    # Check if early messages are preserved
    for i, m in enumerate(messages):
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        if isinstance(content, str) and "Alpha-7" in content:
            print(f"  Message {i} ({m.get('role','?')}): contains Alpha-7 - {content[:100]}")
        if isinstance(content, str) and "测试员小王" in content:
            print(f"  Message {i} ({m.get('role','?')}): contains 测试员小王 - {content[:100]}")
    
    # Check for context compression
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str) and ("compressed" in content.lower() or "摘要" in content):
            print(f"  COMPRESSED context found: {content[:100]}")
