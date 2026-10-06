# boss_daily_patrol — 全店客户机会巡检

无人值守入口。复用客群列表、客群买家明细、客户名单和画像建议，不新增外部接口。

```bash
python cli.py boss_daily_patrol
python cli.py boss_daily_patrol --top 10 --date-type RECENT_30
python cli.py boss_daily_patrol --skip-advice
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--top` | 10 | 重点客户上限。低机会不会为了凑数进入名单 |
| `--date-type` | RECENT_30 | 补充筛选的时间范围 |
| `--max-clusters` | 20 | 最多展开多少个 AI 客群 |
| `--skip-advice` | 关 | 跳过画像建议 |
| `--state-dir` | ~/.openclaw/1688-customer-opportunity/patrol | 上一期快照目录 |

输出仍是 `success / markdown / data`。`markdown` 是给老板看的日报，不含接口名和内部字段名。

查询失败的来源会记入日报，不使用空结果编造客户。AK 未配置时与其他命令一样提示先配置。
