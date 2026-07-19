# 配装预设结构

JSON 预设以 `presets/schema.json` 校验，核心字段如下：

- `schemaVersion`：仓库预设格式版本。
- `gameDataVersion`：生成/验证该配置时的数据版本标签。
- `character.id`：角色 GBID，例如实机确认的暗龙 `PL2900`。
- `scope`：本预设覆盖的范围；因子预设不得暗示同时配置了武器、技能或加护。
- `sigils[].primary.id`：主因子 GBID。
- `sigils[].secondary.id`：第二词条 Skill ID。
- `sigils[].level`：因子等级。
- `evidence`：数据来源和设计说明，方便后续复核。

YAML 模板用于人工编辑；正式提交的角色预设优先使用 JSON，以便标准库脚本直接验证。
