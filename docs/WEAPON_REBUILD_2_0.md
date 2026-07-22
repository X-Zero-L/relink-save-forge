# Relink 2.0 武器超限字段与数据库曲线

本页记录由 2.0 数据库和游戏正常保存后的满级实例交叉确认的武器超限模型。旧的“只处理 58 种旧觉醒武器、统一写一个最终技能”模型已被普通武器实机样本推翻。

## 已确认字段

| 字段 | 含义 | 完成策略 |
| --- | --- | --- |
| `2807` | 旧版武器觉醒等级 | 旧觉醒武器保持 `10`；普通武器保持 `0` |
| `2815` | 武器状态 bit flags | 保留原值，仅 OR `0x40` |
| `2817` | Relink 2.0 超限等级 | 满级 `7` |
| `2818` | 五项武器技能哈希向量 | 五槽分别按该武器的数据库曲线解析 |

`2807` 和 `2817` 是两套独立进度。普通武器可以拥有完整的 2.0 超限进度，但不因此变成旧觉醒武器；所以普通武器必须保持 `2807=0`。旧觉醒武器则继续保持合法的 `2807=10`。

`2815` 是 flags。完成脚本保留所有已有 bit，只设置超限 bit `0x40`，不把整字段覆盖成固定值。

## `2818` 的逐槽解析规则

武器表为五个存档槽分别提供：

```text
WeaponSkillLevelRebuildId1
WeaponSkillLevelRebuildId2
WeaponSkillLevelRebuildId3
WeaponSkillLevelRebuildId4
WeaponSkillLevelRebuildId5
```

每一槽独立执行：

1. 读取该槽的曲线 ID。
2. 在 `weapon_skill_level_rebuild` 中选择 `Unk13` 等于曲线 ID 的候选行。
3. 若武器行存在对应的 `WeaponSkillId`，选择 `Unk12` 哈希与该技能一致的行。
4. 若没有对应技能 ID，则按数据库 `rowid` 稳定顺序选择第一行；只有一行时即选择该唯一行。
5. 把所选 `Unk12` 的 GBFR 哈希写入对应 `2818` 槽。

槽位与源技能字段对应为：

| `2818` 槽 | 曲线列 | 可用时匹配的武器技能列 |
| --- | --- | --- |
| 0 | `WeaponSkillLevelRebuildId1` | `WeaponSkillId1` |
| 1 | `WeaponSkillLevelRebuildId2` | `WeaponSkillId2` |
| 2 | `WeaponSkillLevelRebuildId3` | `WeaponSkillId5ForAwakening` |
| 3 | `WeaponSkillLevelRebuildId4` | `WeaponSkillId6ForAwakening` |
| 4 | `WeaponSkillLevelRebuildId5` | `WeaponSkillId7ForAwakening` |

禁止使用两种旧捷径：

- 把第一技能 ID 的末尾替换成 `_10` 来合成 `max_skill_id`；
- 把某次暗龙探针的第五技能当成所有武器共享的最终技能。

第五槽和前四槽一样属于每把武器自己的曲线。不同武器可以得到不同的第五槽哈希。

## 完整数据库范围

入选规格必须同时满足：

- `WeaponStatusRebuildId` 在 `weapon_status_rebuild` 中恰好具有连续 Level 1–7；
- 五个 `WeaponSkillLevelRebuildId` 都非空且能在技能曲线表中解析。

当前提取数据库得到：

- 162 条完整数据库规格；
- 160 条当前规格；
- 2 条备用 `_A0` 运行时规格：`WEP_PL2800_A0`、`WEP_PL2900_A0`。

`catalogs/weapon-rebuild-2.0.json` 保存完整规格和每槽选择来源；`catalogs/weapon-runtime-aliases.json` 保存数据库运行时哈希到正式基础武器的映射。

## 实机证明

在游戏正常加载、强化并保存后的当前样本中：

- 识别出 171 个可处理实例、159 种当前规格；
- 其中 70 个为旧觉醒实例，101 个为普通武器实例；
- 唯一未出现的当前规格是 `WEP_PL0000_01`；
- 74 个已经满级的实例提供 370 个 `2818` 槽位校验；
- 370 个槽全部等于上述数据库曲线解析结果。

完成脚本在写入前先用这些已有满级实例验证曲线模型；任何一个槽不匹配都会中止。写入时只修改 `2815`、`2817`、`2818`，并保护 `2803`、`2807`、`2813`、`2816` 与主线字段 `2510/2511/2520/2522`。

## 实例关联不变量

完成超限不能替代基础武器实例结构。每把武器仍需同时满足：

- `2802` 是非零且唯一的实例 ID；
- `2803` 是真实数据库运行时武器哈希；
- 角色 `1402` 指向该武器的 `2802`；
- 超限等级、flags 和技能向量位于同一个 weapon unit。

脚本只处理能够直接匹配 160 条当前数据库规格的既有实例，不创建占位武器，不猜测 ID，也不直接写入活动存档目录。
