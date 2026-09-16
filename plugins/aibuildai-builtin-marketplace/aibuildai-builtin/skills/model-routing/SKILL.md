---
name: model-routing
description: Choose one model for each AIBuildAI Agent role. Use the task, the allowed model list, current model knowledge, and past routing facts. This skill is only for Router.
---

# Model routing

Choose the least costly model that can keep the result quality for this task. A cheap run that gives a bad or invalid result is not a success.

Read the task folder before you choose. Read `references/model-evidence.md` beside this file for general routing notes. Read `references/static-policy.json` when an exact task match may help; it ships with no entries, and an operator adds the tasks and model choices of their own runs. The local files are hints, not a list of every model you may use. They can be old. Use current model knowledge, the optional kb, and web search when you hold a web tool and it gives better facts.

When Input lists model ids, that list is a hard limit: choose only exact ids from it. When Input gives no list, choose a known model that works with the run's provider.

In stable mode, use a cheaper model only when there is good reason to think it will keep the result quality. In aggressive mode, a cheaper model may be tried when no fact shows it is too weak. Give more weight to the roles that plan the method, write the code, or combine the final result. Use the task and what the run has produced so far to judge the other roles; do not use a fixed role rule when the work says otherwise.

Put one model for every routed role in Input in `models`. Do not add other roles. State the facts and task details that led to the choices. Then call `StructuredOutput` once.
