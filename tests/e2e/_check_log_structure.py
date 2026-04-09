import sys, os, json
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

d = r'D:\OpenAkita\data\llm_debug'

# Look at recent large request files
req_files = sorted([f for f in os.listdir(d) if f.startswith('llm_request_20260409')])
# Pick a large one
for f in req_files[-3:]:
    fp = os.path.join(d, f)
    size = os.path.getsize(fp)
    if size < 5000:
        continue
    print(f"\n=== {f} ({size} bytes) ===")
    with open(fp, 'r', encoding='utf-8') as fh:
        data = json.load(fh)
    print(f'Top-level keys: {list(data.keys())}')
    for k, v in data.items():
        if isinstance(v, str):
            print(f'  {k}: str[{len(v)}] = {v[:120]}...')
        elif isinstance(v, list):
            print(f'  {k}: list[{len(v)}]')
            if v:
                first = v[0]
                if isinstance(first, dict):
                    role = first.get('role', 'no-role')
                    content = str(first.get('content', ''))[:120]
                    print(f'    [0] keys={list(first.keys())}, role={role}, content={content}')
                if len(v) > 1:
                    last = v[-1]
                    if isinstance(last, dict):
                        role = last.get('role', 'no-role')
                        content = str(last.get('content', ''))[:120]
                        print(f'    [-1] keys={list(last.keys())}, role={role}, content={content}')
        elif isinstance(v, dict):
            print(f'  {k}: dict keys={list(v.keys())}')
        else:
            print(f'  {k}: {type(v).__name__} = {v}')
    break

# Now find the conversation logs for our e2e test (conv_id starting with e2e_chat_)
print("\n\n=== Searching for e2e_chat_ conversation logs ===")
for f in req_files[-50:]:
    fp = os.path.join(d, f)
    try:
        with open(fp, 'r', encoding='utf-8') as fh:
            content = fh.read(500)
        if 'e2e_chat_' in content or 'e2e_test_' in content:
            print(f'FOUND: {f}')
    except:
        pass
