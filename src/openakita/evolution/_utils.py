import re


def strip_json_fences(text: str) -> str:
    m = re.match(r"^```(?:json)?\s*\n?(.*?)```\s*$", text.strip(), re.DOTALL)
    if m:
        return m.group(1).strip()
    sb = text.find("{")
    sq = text.find("[")
    s = min(x for x in (sb, sq) if x >= 0) if (sb >= 0 or sq >= 0) else -1
    if s > 0:
        text = text[s:]
    e = max(text.rfind("}"), text.rfind("]"))
    if 0 <= e < len(text) - 1:
        text = text[: e + 1]
    return text


strip_json = strip_json_fences
