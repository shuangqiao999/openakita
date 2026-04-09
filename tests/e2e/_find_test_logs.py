import json, sys, io, os, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

DEBUG_DIR = r"D:\OpenAkita\data\llm_debug"
files = sorted(glob.glob(os.path.join(DEBUG_DIR, "llm_request_*.json")),
               key=os.path.getmtime, reverse=True)

print(f"Searching {len(files)} files for test_chat_ conversations...")
found = []
for f in files[:500]:
    try:
        with open(f, "r", encoding="utf-8") as fh:
            raw = fh.read()
        if "test_chat_" in raw or "test_plan_" in raw or "test_mem_" in raw:
            data = json.loads(raw)
            ts = data.get("timestamp", "?")
            stats = data.get("stats", {})
            msgs_count = stats.get("messages_count", 0)
            tokens = stats.get("total_estimated_tokens", "?")
            found.append((f, data))
            print(f"  {os.path.basename(f)} | {ts} | msgs={msgs_count} | tokens={tokens}")
    except Exception:
        pass

print(f"\nTotal found: {len(found)}")

if found:
    # Analyze the latest one with most messages (likely the multi-turn chat test)
    found.sort(key=lambda x: x[1].get("stats", {}).get("messages_count", 0), reverse=True)
    f, data = found[0]
    
    print(f"\n{'='*60}")
    print(f"DEEPEST TEST CONVERSATION: {os.path.basename(f)}")
    print(f"{'='*60}")
    
    llm_req = data.get("llm_request", {})
    msgs = llm_req.get("messages", [])
    system = llm_req.get("system", "")
    stats = data.get("stats", {})
    
    print(f"Stats: {json.dumps(stats, ensure_ascii=False)}")
    print(f"System prompt length: {len(system)} chars")
    
    # System prompt sections
    print(f"\n--- System Prompt Sections ---")
    for keyword in ["当前会话", "系统概况", "对话上下文", "记忆系统", "powered by", "仅供参考"]:
        idx = system.find(keyword)
        if idx >= 0:
            print(f"  Found '{keyword}' at pos {idx}: ...{system[max(0,idx-20):idx+80]}...")
        else:
            print(f"  NOT FOUND: '{keyword}'")
    
    # Messages analysis
    print(f"\n--- Messages ({len(msgs)}) ---")
    import re
    for i, m in enumerate(msgs):
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        if not isinstance(content, str):
            content = str(content)
        
        # Check for timestamps, markers
        has_ts = bool(re.search(r'\[\d{2}:\d{2}\]', content))
        has_latest = "[最新消息]" in content
        has_alpha = "Alpha-7" in content
        has_xiaowang = "小王" in content
        has_compress = "context_compressed" in content.lower() or "[压缩]" in content
        
        flags = []
        if has_ts: flags.append("TS")
        if has_latest: flags.append("LATEST")
        if has_alpha: flags.append("Alpha-7")
        if has_xiaowang: flags.append("小王")
        if has_compress: flags.append("COMPRESSED")
        
        preview = content[:120].replace("\n", " ")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        print(f"  [{i}] {role}{flag_str}: {preview}")
    
    # Check for context compression events
    print(f"\n--- Context Integrity Check ---")
    all_content = " ".join(
        m.get("content", "") if isinstance(m.get("content",""), str) 
        else " ".join(b.get("text","") for b in m.get("content",[]) if isinstance(b,dict))
        for m in msgs
    )
    print(f"  Alpha-7 present: {'Alpha-7' in all_content}")
    print(f"  小王 present: {'小王' in all_content}")
    print(f"  Python present: {'Python' in all_content}")
    print(f"  Rust present: {'Rust' in all_content}")
    print(f"  424 present: {'424' in all_content}")
