"""Graph 层：LangGraph 工作流编排。被 services 层调用，不碰 DB。

本包不 eager import 子模块——rewrite 的节点链（app.nodes → app.services）会反向依赖
回本包（services.rewrite → graphs.rewrite），若在 __init__ 里 eager 导入
rewrite 会成环（2026-09-07 基建接入时暴露）。调用方一律按模块路径导入：
`from app.graphs.rewrite import run_rewrite` / `from app.graphs import resume_parse`。
"""
