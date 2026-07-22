# Windows 一键包

`RelinkSaveForge.cmd` 提供 manifest 驱动的 Windows 入口。发布 ZIP 自带固定版本的
CPython x64 便携运行时，不要求用户安装 Python。存档解析依赖的
GBFR-Save-Editor 不随项目重新分发；启动脚本会从固定上游提交下载并校验 SHA-256。

## 使用

1. 完全退出游戏。
2. 解压发布 ZIP，双击 `RelinkSaveForge.cmd`。
3. 选择预设。只有明确输入 `y` 时才部署；否则只生成离线候选。

默认菜单包含四个预设：

- `latest-endgame-gold`：29 人完整 31 效果终盘配装；角色 24 个 99 级因子词条、当前装备武器 3 个 99 级祝福、现有四颗终盘召唤石各 1 个合法满级被动；
- `resources-900`：329 种普通可叠加物和 8 种解锁卷精确设为 900；
- `fate-episodes-all`：完成 319 条真实命运篇章及 56 个有效任务计数；
- `mainline-safe-endgame`：资源 → 命运篇章 → 完整终盘配装。

综合配装只修改已有装备关系所指向的因子、29 把当前武器的祝福快照和现有四颗终盘召唤石的第一被动槽；不会创建武器、召唤石或库存 Wrightstone，也不会修改专精或主线。

非交互调用：

```powershell
RelinkSaveForge.cmd --preset latest-endgame-gold --apply
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
./packaging/build-windows-bundle.ps1 -Version 1.0.0 -OutputDirectory dist
```

输出为 `dist/RelinkSaveForge-win-x64-v1.0.0.zip`。构建只捆绑官方 CPython
3.11.9 embeddable runtime；GBFR-Save-Editor 仍由用户启动时从固定提交获取。
