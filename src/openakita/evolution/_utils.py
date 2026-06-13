import re


def strip_json(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text)
    sb = text.find("{")
    sq = text.find("[")
    s = min(x for x in (sb, sq) if x >= 0) if (sb >= 0 or sq >= 0) else -1
    if s > 0:
        text = text[s:]
    e = max(text.rfind("}"), text.rfind("]"))
    if 0 <= e < len(text) - 1:
        text = text[: e + 1]
    return text
