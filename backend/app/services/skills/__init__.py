"""Skill 框架：AI 对话的工具调用能力（联网搜索 / 网页抓取 / 自定义 API）。

- base: Skill 协议与结果截断
- builtin: 内置 web_search / web_fetch
- api_skill: 管理端自定义的 HTTP API skill
- registry: 租户级解析（DB 行覆盖内置默认，未建行的内置 skill 以代码默认出现）
"""
