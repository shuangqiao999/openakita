"""健康检查重试优化验证"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:1234/v1")
os.environ.setdefault("OPENAI_API_KEY", "not-needed")
os.environ.setdefault("DEFAULT_MODEL", "qwen/qwen3.5-9b")

LMSTUDIO_BASE = "http://localhost:1234/v1"
MODEL = "qwen/qwen3.5-9b"
PASS = FAIL = 0
FAILED: list[str] = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        FAILED.append(f"{name}: {detail}" if detail else name)
        print(f"  [FAIL] {name}  --  {detail}" if detail else f"  [FAIL] {name}")


def test_source_has_retry():
    print("\n" + "=" * 60)
    print("1. 源码重试逻辑检查")

    src_path = Path(__file__).parent.parent.parent / "src" / "openakita" / "llm" / "client.py"
    src = src_path.read_text(encoding="utf-8")
    idx = src.index("def startup_health_check")
    func_src = src[idx:idx + 3000]

    check("for _attempt in range(2)", "for _attempt in range(2)" in func_src)
    check("content empty 时 continue 重试", "continue" in func_src)
    check("重试前 sleep 3s", "sleep(3)" in func_src)
    check("model loading 日志", "model loading" in func_src)
    check("非 content-empty 错误 break 不重试", func_src.count("break") >= 3)


def test_lmstudio_health_check_live():
    print("\n" + "=" * 60)
    print("2. LMStudio 实测健康检查")

    try:
        import urllib.request
        req = urllib.request.Request(f"{LMSTUDIO_BASE}/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["id"] for m in data.get("data", [])]
            if MODEL not in models:
                print(f"  [SKIP] 模型 {MODEL} 未加载")
                return
    except Exception:
        print("  [SKIP] LMStudio 不可用")
        return

    import urllib.request
    payload = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5, "temperature": 0, "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{LMSTUDIO_BASE}/chat/completions",
        data=payload, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())

    content = result["choices"][0]["message"].get("content", "")
    usage = result.get("usage", {})
    output_tokens = usage.get("completion_tokens", 0)
    print(f"  response: content='{content}', output_tokens={output_tokens}")
    check("LLM 健康检查返回内容", len(content.strip()) > 0 or output_tokens == 0,
          f"content='{content}', tokens={output_tokens}")


def main():
    print("=" * 60)
    print("  健康检查重试优化验证")
    print("=" * 60)

    test_source_has_retry()
    test_lmstudio_health_check_live()

    print("\n" + "=" * 60)
    print(f"  结果: {PASS} passed, {FAIL} failed")
    if FAILED:
        for f in FAILED:
            print(f"    x {f}")
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
