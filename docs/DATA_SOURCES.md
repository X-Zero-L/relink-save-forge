# 数据来源与版本

## 本次生成基准

- 游戏数据解析器：[Nenkai/GBFRDataTools](https://github.com/Nenkai/GBFRDataTools)，提交 `571a1d1ce71c17601684894dad186269c0fed1dc`。
- 补充因子/词条研究：[alexfrljuckic/GBFRelinkMod](https://github.com/alexfrljuckic/GBFRelinkMod)，提交 `c9bd8350e6deb3a3034194fe6fbf62cd453989e9`。
- `gbfr-live.db` SHA-256：`7A721A9B1822C2C7660C71F653B2FA1B3BE2DAFC4049B6FD4178BA78E5E96789`。
- `gbfr-live-items.db` SHA-256：`4AF30C37143AE15FEF4930A4C7D0FF06B2CC59B8F0EC5F8E17204DB12DFFB59A`。

数据库文件不随仓库发布。摘要用于确认清单由哪一份本地提取数据生成，不代表游戏厂商版本签名。

## 清单筛选规则

### 角色（29）

`chara` 表中：

- `CharId LIKE 'PL%'`
- `IsNPC = 0`
- `MaxLevelMaybe = 100`

名称字段原则上保留数据库本地化 key，避免把未验证的社区译名当作正式名称。`PL2900 = Fediel` 是由游戏内 UI 与对应存档记录交叉确认的实机映射；`PL2800` 不再标记为 Fediel。

### 武器（174）

对每名角色生成候选 `WEP_<CharId>_01` 至 `_09`，但只有与 `weapon.Key` 的明文值或自定义哈希真实匹配的数据库基础行才能进入清单。候选生成只用于反查，不允许把未匹配的连续编号当成真实武器写入存档。

`PL2100`、`PL2200`、`PL2300` 均不存在 `_05`；它们的第六把正式武器是 `_07`。觉醒阶段行、模型占位和调试/额外索引不重复计数。

### 2.0 因子

从 `gem` 表选择 `Rarity = 5` 且满足以下任一条件的每一条数据库行：

- 角色专属家族 `GEEN_173`–`GEEN_178`；
- 2.0 通用/天星家族 `GEEN_320`–`GEEN_327`。

同一显示 GBID 可能对应多个不同第二词条数据库行。清单保留 `database_key`、`skill_id_1` 和 `skill_id_2`，不会错误地把它们合并成一个实例。

## 更新方法

1. 使用合法取得的游戏文件和 GBFRDataTools 重新提取表。
2. 将数据库放在仓库外部。
3. 运行 `scripts/generate_catalogs.py` 并明确传入两个数据库路径。
4. 阅读 JSON diff，确认角色数、武器数和因子家族变化是预期的。
5. 运行 `scripts/validate_repository.py`。
