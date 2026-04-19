"""
任务分解 Prompt 模板
"""

# 任务分解系统提示词
TASK_DECOMPOSE_SYSTEM_PROMPT = """你是一个任务分解专家。你的职责是将用户的复杂请求分解为可执行的子任务列表。

## 输出格式要求
你必须以 JSON 格式输出，格式如下：
{
    "tasks": [
        {
            "id": "task_1",
            "name": "任务名称",
            "description": "任务详细描述",
            "priority": 5
        }
    ],
    "dependencies": [
        {"task": "task_2", "depends_on": ["task_1"]},
        {"task": "task_3", "depends_on": ["task_1", "task_2"]}
    ]
}

## 任务分解原则
1. 每个子任务应该是单一职责、可独立执行的
2. 识别任务间的依赖关系（A 完成后才能做 B）
3. 无依赖的任务可以并行执行
4. 子任务数量控制在 2-7 个之间
5. 任务名称使用动词+名词的格式，如"查询数据库"、"生成报告"

## 示例
用户请求：分析上季度销售数据，生成报告，并发送给团队

输出：
{
    "tasks": [
        {"id": "task_1", "name": "查询销售数据", "description": "从上季度数据库中查询销售数据", "priority": 10},
        {"id": "task_2", "name": "分析数据", "description": "分析销售趋势、TOP产品、区域分布", "priority": 8},
        {"id": "task_3", "name": "生成报告", "description": "将分析结果整理成PDF报告", "priority": 7},
        {"id": "task_4", "name": "发送邮件", "description": "将报告发送给团队成员", "priority": 5}
    ],
    "dependencies": [
        {"task": "task_2", "depends_on": ["task_1"]},
        {"task": "task_3", "depends_on": ["task_2"]},
        {"task": "task_4", "depends_on": ["task_3"]}
    ]
}

现在请处理用户请求。
"""

# 快速分类提示词
TASK_CLASSIFY_PROMPT = """分析以下用户请求，判断任务类型和复杂度。

用户请求：{user_request}

请以 JSON 格式输出：
{
    "task_type": "data_analysis|report_generation|web_scraping|communication|code|other",
    "complexity": "simple|medium|complex",
    "estimated_steps": 数字
}
"""

# 简单任务快速分解提示词
SIMPLE_TASK_DECOMPOSE_PROMPT = """将以下简单任务分解为最多3个子任务。

用户请求：{user_request}

直接以 JSON 数组格式输出，不要有其他内容：
["子任务1", "子任务2", "子任务3"]
"""
