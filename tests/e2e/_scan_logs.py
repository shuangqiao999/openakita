import sys, os, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

d = r'D:\OpenAkita\data\llm_debug'
req_files = sorted([f for f in os.listdir(d) if f.startswith('llm_request_20260409')])

print(f"Total request files: {len(req_files)}")
print("\nLast 30 files:")
for f in req_files[-30:]:
    fp = os.path.join(d, f)
    try:
        with open(fp, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        stats = data.get('stats', {})
        ctx = data.get('context', {})
        sp_len = stats.get('system_prompt_length', 0)
        mc = stats.get('messages_count', 0)
        sid = ctx.get('session_id', '?')
        oid = ctx.get('org_id', '')
        org_label = oid[:12] if oid else "-"
        print(f"  {f}: sp={sp_len}, msgs={mc}, org={org_label}, sid={sid[:35]}")
    except Exception as e:
        print(f"  {f}: ERR {e}")

# Look for the most recent main chat log (from earlier today, with session_id not containing org:)
print("\n\n=== Looking for main desktop chat logs today ===")
main_chat_found = []
for f in req_files:
    fp = os.path.join(d, f)
    if os.path.getsize(fp) < 8000:
        continue
    try:
        with open(fp, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        ctx = data.get('context', {})
        sid = ctx.get('session_id', '')
        if sid and not sid.startswith('org:') and not ctx.get('org_id'):
            stats = data.get('stats', {})
            sp_len = stats.get('system_prompt_length', 0)
            mc = stats.get('messages_count', 0)
            main_chat_found.append((f, sp_len, mc, sid))
    except:
        pass

print(f"Found {len(main_chat_found)} non-org chat logs")
for f, sp, mc, sid in main_chat_found[-10:]:
    print(f"  {f}: sp_len={sp}, msgs={mc}, session={sid}")
