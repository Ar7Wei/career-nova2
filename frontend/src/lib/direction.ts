/**
 * 方向（投递·检索条件）前端常量。
 *
 * MAX_KEYWORD_GROUPS：查询组组数上限（ADR 0009）。与后端 app/schemas/direction.py 的
 * MAX_KEYWORD_GROUPS 同值——前端在编辑器源头禁用「添加条件组」（加不出第 7 组，谈不上截断），
 * 后端仍兜底校验（手改 state / agent 工具路径时 ConflictError 出声）。同一份规则两处承载，
 * 改时两边同步。
 */
export const MAX_KEYWORD_GROUPS = 6
