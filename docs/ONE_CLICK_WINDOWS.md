# Windows 一键包

`RelinkSaveForge.cmd` 提供 manifest 驱动的 Windows 入口。发布 ZIP 自带固定版本的
CPython x64 便携运行时，不要求用户安装 Python。存档解析依赖的
GBFR-Save-Editor 不随项目重新分发；启动脚本会从固定上游提交下载并校验 SHA-256。

## 使用

1. 完全退出游戏。
2. 解压发布 ZIP，双击 `RelinkSaveForge.cmd`。
3. 选择预设。只有明确输入 `y` 时才部署；否则只生成离线候选。

v1.1.0 默认菜单包含 12 个预设，分为三组。

### 仅配装现有实例

| ID | 作用 |
| --- | --- |
| `standard-endgame-output` | 数据库真实组合的正常 Lv15 输出因子、Lv15 Quick Cooldown/Cascade/Stout Heart 武器祝福，以及现有四颗终盘召唤石被动。 |
| `standard-endgame-qol` | 数据库真实组合的正常 Lv15 生存/QoL 因子、Lv15 Cascade/Nimble Onslaught/Greater Aegis 武器祝福，以及现有四颗终盘召唤石被动。 |
| `latest-endgame-gold` | 明确标记的老金版：24 条 Lv99 因子词条、三条 Lv99 武器祝福和现有四颗召唤石的合法满级被动。 |
| `mainline-safe-endgame` | 资源 900 → 全命运篇章 → `latest-endgame-gold`；武器和召唤石仍只处理现有实例。 |

这组不会修复缺失的因子装备链接，也不会创建缺失的武器或召唤石。输入存档必须已经让 29 名角色各有 12 个可解析的因子链接。召唤石步骤只修改现有四颗目标的第一被动 lane，并保留 `1451/1456/1457/1460`、第二加成 lane 和现有实例关系。

### 自动补齐后配装

| ID | 作用 |
| --- | --- |
| `standard-complete-output` | 启用全部角色 → 完成命运篇章 → 用已有非零因子实例建立 29×12 个可解析链接 → 创建并强化全部正式武器 → 创建并装备四颗终盘召唤石 → 正常 Lv15 输出配装。 |
| `standard-complete-qol` | 同样自动补齐，再应用正常 Lv15 生存/QoL 配装。 |
| `gold-complete` | 同样自动补齐，再应用显式 Lv99 老金因子和武器祝福。 |

三个 complete pack 都在 Fate Episodes 后执行 `ensure-sigil-loadouts`，再进入武器、召唤和最终配装阶段。该步骤只从已有非零因子实例中选择 348 个唯一实例，规范化每名角色前 12 个 `1403` 引用及所选实例的 `2706` owner。因子实例 ID、显示外壳、等级、flags 和内部词条完整保留；非目标 `1403`、目标角色第 13 个及后续尾槽、未选中的 owner 也不会被改写，并从可分配补位池排除。实例不足时在写盘前失败，不会创建假实例。

标准版所有非空因子 lane 都精确为 Lv15，外壳、主词条和副词条必须对应 2.0.2 数据库真实 `gem` 行，并按角色检查相同技能的合计等级不超过曲线上限。只有 ID 带 `gold`、显示名和说明明确标记 `Gold` 与 `Lv99` 的包会写入 99 级 trait。

### 独立操作

| ID | 作用 |
| --- | --- |
| `unlock-all-characters` | 对 29 个预分配角色行的 `1305` 只 OR 自然激活 mask，保留已有 bit、进度和装备。 |
| `complete-armory` | 在规范空槽中创建缺少的 174 把正式武器；160 把写入完整终盘运行时规格，14 把保持基础规格，并装备每名角色的已验证最强武器。 |
| `create-top-four-summons` | 创建缺少的 Rolan、Lilith、Beelzebub、Lucilius，设置合法 `15/9` lane、解锁目录并装备四颗。 |
| `resources-900` | 329 种普通可叠加物和 8 种解锁卷精确设为 900。 |
| `fate-episodes-all` | 完成 319 条真实命运篇章及 56 个有效任务计数。 |

武器创建只消费完全匹配规范模板的空槽，保留所有未知或模组武器。发现重复实例 ID、重复正式武器、非规范半空壳或空槽不足时会在写盘前失败。现有召唤重配不会改写语义未知的 `1460`；创建包对四颗目标使用实机保存后统一规范化的值 `6`，而不是按稀有度推导。全部 12 个包都保持 Steam 封装、payload 大小、记录数和主线字段 `2510/2511/2520/2522` 不变，也不修改专精。

非交互调用：

```powershell
RelinkSaveForge.cmd --list-presets
RelinkSaveForge.cmd --preset standard-endgame-qol --apply
RelinkSaveForge.cmd --preset standard-complete-output --apply
RelinkSaveForge.cmd --preset gold-complete --apply
RelinkSaveForge.cmd --preset resources-900 --save "$env:LOCALAPPDATA\GBFR\Saved\SaveGames"
RelinkSaveForge.cmd --validate-only
RelinkSaveForge.cmd --restore-latest
```

主要参数：

- `--preset ID`：选择 `presets/packs/*.json` 中的预设。
- `--save PATH`：可指向 `SaveData1.dat` 或 `SaveGames` 目录。
- `--apply`：部署候选；缺少该参数时只构建、验证并保留候选。
- `--editor-root PATH`：覆盖自动下载的 GBFR-Save-Editor 路径。
- `--state-root PATH`：覆盖运行、日志和备份根目录。

默认运行数据位于 `%LOCALAPPDATA%\RelinkSaveForge`：

```text
backups/gbfr-save-backup-*/SaveGames/
runs/<session>/source.dat
runs/<session>/pass-1/
runs/<session>/pass-2/
runs/<session>/candidate.dat
runs/<session>/run.log
runs/<session>/events.jsonl
runs/<session>/session.json
```

## 安全事务

每次执行都会先通过 `tasklist` 确认游戏未运行，检查活动存档哈希，然后完整复制
`SaveGames` 目录并为每个文件写入 SHA-256 清单。所有步骤在运行目录中的副本上
执行。完整预设会对第一次候选再执行一遍，最终两个存档必须逐字节一致。

部署前会再次确认活动 `SaveData1.dat` 的 SHA-256 没有变化。候选先写到活动目录
内的临时文件并刷新磁盘，再以 `os.replace` 原子替换。替换后重新打开存档并验证
活动哈希、候选 SHA 和预设声明的结构不变量。部署或复验失败时自动从本次完整
备份恢复原主存档；下次启动也会检查并恢复中断在部署阶段的事务。

## Pack manifest v1

预设文件位于 `presets/packs/*.json`：

```json
{
  "schema_version": 1,
  "id": "example",
  "name": "Example preset",
  "description": "Human-readable scope",
  "invariants": {
    "preserve_header": true,
    "preserve_payload_size": true,
    "preserve_record_count": true
  },
  "steps": [
    {
      "id": "example-step",
      "name": "Example transform",
      "kind": "transform",
      "timeout_seconds": 1800,
      "audit_required": true,
      "command": [
        "{python}",
        "{root}/scripts/example.py",
        "{input}",
        "{output}",
        "--audit", "{audit}",
        "--editor-root", "{editor_root}"
      ]
    }
  ]
}
```

命令不经过 shell。支持 `{python}`、`{input}`、`{output}`、`{audit}`、`{root}`、
`{editor_root}`、`{run_dir}` 和 `{save_dir}`。`transform` 步骤必须生成独立输出；
`verify` 步骤只验证当前输入，不推进候选链。

## 构建发布 ZIP

```powershell
./packaging/build-windows-bundle.ps1 -Version 1.1.0 -OutputDirectory dist
```

输出为 `dist/RelinkSaveForge-win-x64-v1.1.0.zip`。构建只捆绑官方 CPython
3.11.9 embeddable runtime；GBFR-Save-Editor 仍由用户启动时从固定提交获取。
