# Apple Health Review

把 Apple Health／苹果健康快捷指令导出的七类指标 JSON 转为规范样本和每日汇总，与健康 App 数值或截图核对，再由 Codex 生成中文报告。

当前版本：**1.0.0**。分析范围为睡眠、步数、活动能量、锻炼分钟数、静息心率、HRV SDNN 和心率。

## 安装与使用

将仓库克隆到个人 skill 目录（目标目录不存在时）：

```sh
git clone https://github.com/Ethan3114/apple-health-review.git ~/.agents/skills/apple-health-review
```

在 Codex 中附上快捷指令导出的 JSON，输入：

```text
使用 $apple-health-review 处理这份健康数据，生成结构化内容和中文报告。
```

Codex 会执行转换脚本，读取汇总文件，按核对状态生成报告。仓库不直接连接 iPhone，也不提供临床诊断或健康评分。

## 单独运行脚本

需要 Python 3.10+，无额外依赖。

```sh
python3 scripts/convert_health_export.py /absolute/path/to/export.json \
  --output-dir /absolute/path/to/new-result-directory \
  --timezone Asia/Shanghai
```

脚本要求 schema_version=2、七类 metric 各出现一次，并至少覆盖一个完整日；完整契约见 [data-contract.md](references/data-contract.md)。Apple 全量 XML、旧 dates/values 格式和第三方 JSON 需要另行适配。

输出包括规范样本、模型汇总、核对 JSON、观测模板和核对清单。中文分析报告由遵循 [SKILL.md](SKILL.md) 的模型生成，转换脚本本身不生成分析结论。

有健康 App 实际显示值时，填写独立观测文件，再运行同一命令并追加 `--observations /absolute/path/to/observations.json`。自动核对针对窗口最后一个完整日，规则见 [reconciliation.md](references/reconciliation.md)。

## 当前处理规则

- 数值转为数字，心率统一为 bpm，睡眠阶段使用标准标签。
- 完全一致的记录折叠，保留原始索引；多来源累计量分别保留。
- 跨午夜记录保留边界贡献范围，默认不按时长均摊。
- 睡眠区间取并集，排除清醒和卧床；睡眠段按结束日期归日。
- 缺失为 null，部分日单独标记；均值、范围和最新读数分别核对。
- App 显示值与原始计算值独立保留，差异不通过修改原始数据抹平。

## 已知限制

V1 未复现苹果原生多来源统计合并。知道手表优先级，也不能直接把只取手表的样本合计称为健康 App 日总量。部分导出可能缺失睡眠来源；睡眠归日和阶段时长需要与 App 核对。跨文件合并尚未内置。

脚本已通过真实导出的回归验证与 8 项行为测试；原始数据不随仓库发布。单日只做单日描述，多日可比数据才用于趋势分析。数值相容不证明持续佩戴、同步完整或临床准确性。

## 数据文件

脚本只在本地处理数据，无网络调用，不写入健康数据库。模型输入去除来源中的个人名称，但仍包含健康信息。请把自己的导出文件、截图和结果目录放在仓库外；仓库只包含代码和说明。

开发与修改 skill 时，请保留 [reporting.md](references/reporting.md) 中对数据口径、证据和解释的要求。
