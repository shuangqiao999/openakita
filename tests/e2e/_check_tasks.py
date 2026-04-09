import httpx, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = "http://127.0.0.1:18900"

# Check tasks via correct endpoint
orgs = httpx.get(f"{BASE}/api/orgs", timeout=10).json()
for org in orgs:
    oid = org.get("id", "")
    oname = org.get("name", "?")
    
    # GET /api/orgs/{org_id}/tasks (cross-project aggregation)
    r = httpx.get(f"{BASE}/api/orgs/{oid}/tasks", timeout=10)
    data = r.json()
    tasks = data if isinstance(data, list) else data.get("tasks", data.get("items", []))
    print(f"\n=== {oname} (status={org.get('status','?')}) ===")
    print(f"  Tasks endpoint: status={r.status_code}, type={type(data).__name__}")
    if isinstance(data, dict):
        print(f"  Keys: {list(data.keys())}")
    if tasks:
        print(f"  Total tasks: {len(tasks)}")
        for t in tasks[:5]:
            title = t.get("title", t.get("name", "?"))
            status = t.get("status", "?")
            tid = t.get("id", "?")
            assignee = t.get("assignee", "?")
            print(f"    [{status}] {title} (id={tid}, assignee={assignee})")
    else:
        print(f"  No tasks found")
        print(f"  Raw data sample: {json.dumps(data, ensure_ascii=False)[:300]}")

# Check LLM debug logs
print("\n\n=== LLM Debug Logs ===")
import os
data_dir = os.path.join(os.environ.get("USERPROFILE", ""), ".openakita", "data")
if not os.path.exists(data_dir):
    data_dir = "D:\\OpenAkita\\data"
    
for dirpath, dirnames, filenames in os.walk(data_dir):
    llm_files = [f for f in filenames if f.startswith("llm_request")]
    if llm_files:
        print(f"Found {len(llm_files)} LLM debug files in {dirpath}")
        for f in sorted(llm_files)[-3:]:
            path = os.path.join(dirpath, f)
            size = os.path.getsize(path)
            print(f"  {f} ({size} bytes)")

# Also check user home data dir
for possible_dir in [
    os.path.join(os.environ.get("USERPROFILE", ""), ".openakita"),
    os.path.join(os.environ.get("APPDATA", ""), "openakita"),
    "D:\\OpenAkita\\data",
]:
    if os.path.isdir(possible_dir):
        print(f"\nData dir exists: {possible_dir}")
        for item in os.listdir(possible_dir)[:20]:
            full = os.path.join(possible_dir, item)
            if os.path.isdir(full):
                print(f"  [DIR] {item}")
            else:
                print(f"  [FILE] {item} ({os.path.getsize(full)} bytes)")
