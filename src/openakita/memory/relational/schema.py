"""
正确的 JSON Schema 定义
解决 "data/items must be object,boolean" 错误
"""

INTENT_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["query", "task", "chat", "command", "greeting", "farewell", "thanks"],
        },
        "task_type": {"type": "string"},
        "goal": {"type": "string"},
        "tool_hints": {"type": "array", "items": {"type": "string"}},
        "memory_keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["intent"],
}


MEMORY_ENCODING_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["person", "cuisine", "trait", "concept", "preference", "fact"],
                    },
                    "name": {"type": "string"},
                    "properties": {"type": "object", "additionalProperties": True},
                },
                "required": ["id", "type", "name"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "relation": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["from", "to", "relation"],
            },
        },
    },
    "required": ["nodes", "edges"],
}


SELF_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["healthy", "warning", "error"]},
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "status": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["status"],
}


def validate_schema(schema: dict) -> bool:
    """验证 Schema 格式是否正确"""
    try:
        import jsonschema

        jsonschema.Draft7Validator.check_schema(schema)
        return True
    except Exception as e:
        print(f"Schema validation failed: {e}")
        return False


def get_schema_for_model(model_name: str, task: str) -> dict:
    """根据模型和任务获取合适的 Schema"""
    if "intent" in task.lower():
        return INTENT_ANALYSIS_SCHEMA
    elif "memory" in task.lower():
        return MEMORY_ENCODING_SCHEMA
    elif "selfcheck" in task.lower():
        return SELF_CHECK_SCHEMA
    else:
        return INTENT_ANALYSIS_SCHEMA
