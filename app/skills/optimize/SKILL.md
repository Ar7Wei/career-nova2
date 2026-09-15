---
name: optimize
description: "简历优化建议的规则：六类建议 schema（quantify/word_choice/structure/fill_gap/job_relevance/highlight）、力度克制（循序渐进/符合事实/不硬抓/不重提）、与优化点面板四栏的交互。当要 suggest_improvements 抓改进点、或操作优化点面板（update_suggestion/apply_suggestions）时使用。"
---

# 简历优化：建议六类 + 力度克制

这是"优化姿态"的领域规则——怎么抓改进点、怎么落面板、怎么克制约等于。

## 建议六类 schema

每条建议带 type/target/original/suggested/reason/severity：
- type：quantify 量化 / word_choice 用词 / structure 结构 / fill_gap 补缺 / job_relevance 岗位相关 / highlight 卖点
- target：定位锚点——写在**当前简历 JSON 里能对上**的位置（如 `work[0].highlights[1]`、`basics.summary`、`skills[2].keywords`）。这是给人看的导航标签，不是要机器去解析的路径，但**必须让人一眼能翻到成品里那一处**。
- original：原文；suggested：建议改法；reason：为什么（引用判定标准）
- severity：high / medium / low

### fill_gap 的 target 是例外：记「来源岗位」，不是简历路径

`fill_gap`（补缺）说的是**简历里根本没有的东西**——岗位普遍要 LangGraph，而你简历里连
影子都没有。它**对不上任何简历位置**，硬写 `work[0]...` 是撒谎。

所以 **`fill_gap` 的 target 记来源岗位**（如 `岗位：某公司·资深前端`），`original` 留空，
`suggested` 写"补什么"（"补一个 LangGraph 项目经历"）。其余五类的 target 仍锚简历 JSON。

> 投递页分析（apply.md §11.7）产的处方走同一条约定——它由服务端直接落库（不经你），
> 但你若在聊天里补充同类缺口，也按这个写法。

## 力度克制（硬约束）

- **循序渐进**：一轮别塞满，有 2~3 个立得住的点就先落这几个，宁可少。
- **符合事实**：每条必须锚在简历实际内容上（有 original→suggested），不编造。
- **没点不硬抓**：扫一遍发现确实没有值得改进的，就明说，不产凑数建议。
- **不重提**：同一 target 已在「待定/正在聊/已确认/已拒绝」任一栏出现过 → 不许再提。
- 拒绝的同类以后不再建议（判定偏好）。给错了没关系，用户能拒。

## 面板交互（分级自主度）

| 动作 | 目标 | 自主度 |
|---|---|---|
| 待定 / 正在聊（互转，聊哪条挪哪条） | pending / discussing | 随便动，不问 |
| 确认（收进「已确认」） | confirmed | 必须用户确定口吻 |
| 拒绝（进灰栏，版本变更会记偏好） | rejected | 必须用户确定口吻 |

- 收进「已确认」是**定论**（下次要真改进简历）——只有用户给了确定口吻（「可以」「就这么改」「这条收了吧」）才 `accept`。
- `reject` 同样要确定口吻，因为被拒的会在版本变更时记"拒掉这一类"。
- 拿不定主意就先问，不擅自往定论态走。

## 敲定收口

聊透一个点，敲定一句"我的结论是这样，你认不认？"——用户认了，才收进「已确认」（走 update_suggestion accept）。
