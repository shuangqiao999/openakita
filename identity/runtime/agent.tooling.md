# Agent Tooling Rules

本文件定义了 Agent 使用工具的规则。

## 工具调用原则

- 优先选择最合适的工具完成任务
- 必要时可以组合使用多个工具
- 工具调用失败时记录错误并尝试替代方案

## 常用工具分类

- 文件操作: Read, write, glob, grep
- Shell命令: Bash, run
- 代码开发: Edit, multi-edit, Write
- 信息检索: Web search, fetch

## 错误处理

- 工具调用失败时提供有意义的错误信息
- 记录失败原因以便后续改进