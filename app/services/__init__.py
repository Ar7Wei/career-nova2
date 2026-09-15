"""Service 层：编排业务逻辑，介于 Router 与 Graph/Repository 之间。

本包不 eager import 子模块——chat → services.rewrite → graphs.rewrite 的链路上有反向
依赖，eager import 会成环（2026-09-07 机器门接入时暴露，与 graphs/__init__.py 同一潜在坑）。
调用方一律按模块路径导入（如 `from app.services.chat import handle_message` / `from app.services import extract_state`）。
"""
