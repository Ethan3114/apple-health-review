# 数据契约与脚本行为

V1 的 scripts/convert_health_export.py 是项目现有转换器的原样快照。读取 schema_version=2 快捷指令 JSON，无网络调用，不修改原输入或健康数据库。

## 输入契约

根字段：schema_version=2、exported_at、window_start、window_end、metrics。时间含时区偏移，exported_at=window_end。七个 metric 各出现一次，允许 samples=[]。

| metric | 内容 | unit_raw | 规范单位 |
|---|---|---|---|
| sleep | 睡眠阶段 | 空字符串 | 分类 |
| steps | 步数 | count | count |
| active_energy | 活动能量 | kcal | kcal |
| exercise_minutes | 锻炼分钟数 | min | min |
| resting_heart_rate | 静息心率 | count/min | bpm |
| hrv_sdnn | SDNN 心率变异性 | ms | ms |
| heart_rate | 心率 | count/min | bpm |

模块包含 metric、query_start、query_end、status、sample_count、samples。数量与数组长度一致；status 为 samples_present 或 no_samples。type 是导出器写入的标签，不等于对手机查询类型的独立验证；按 App 实际项目和原生选择器核对类型。

样本 start/end/value_raw/source/unit_raw 均为字符串；起止时间落在模块查询窗口内，start≤end。数值有限、非负，步数为整数；未知单位或睡眠阶段会报错，不猜测转换。

当前导出习惯是从若干天前零点查到现在，睡眠额外向前一天。脚本按明确窗口处理，不靠文件名推断日期。按开始时间筛选可能漏掉窗口开始前启动的跨边界记录；完整时间窗口不代表佩戴、同步完整。

## 五类输出

- health-normalized.json：全部规范样本、输入索引、来源注册表、原文件 SHA-256。来源名称可能含个人名称，在本地样本层保留。
- health-model-input.json：每日汇总、睡眠段、边界／冲突信息及核对结果。去掉来源名称仍不等于健康数据已匿名化；来源 ID 是标签，不是已确认的设备 UUID。
- health-app-reconciliation.json：计算值、App 值、差值、容差、状态及用户报告的来源优先级。
- health-app-observations-template.json：只生成一次的空观测模板，已有文件不覆盖。
- 健康App核对清单.md：自动生成的可读比较表。

## 累计量

步数、活动能量、锻炼分钟数按来源分别处理。只有当天有一个来源，且没有跨边界或同来源重叠时，value 为已观察样本合计；否则保持 null，提供 per_source 候选及记录 IDs。无样本也为 null。

start_day_sum_candidate 将样本全部归入开始日期，仅供对照；contained_sample_sum 只合计完全在日内的样本。贡献范围由完全在日内的合计加上边界样本量组成；同来源重叠时不生成范围。这只约束导出记录，不是 App 完整总量的上下界。

用户报告 Ultra 优先时记录声明及映射依据，不能把名称含 Watch 的来源当作已由设备 UUID 确认的 Ultra。已知优先级不等于已复现苹果原生合并。

## 睡眠与离散统计

睡眠段以 90 分钟间隔切分，归入段结束日期。这是本地约定，不保证匹配 App 的所有午睡、时区和日期场景。额外查询日的睡眠段 assigned_to_target_window_date=false，不能算入目标日报或趋势。

core/deep/rem/asleep_unspecified 区间取并集得到睡眠时长，排除 awake/in_bed；各阶段也取并集，冲突单独标记。缺失阶段为 null，缺失来源不补为手表。

心率、静息心率、HRV 按开始日期归日，输出 min/max/sample_mean/sample_median/latest。平均是未加权样本平均；latest 为该日开始时间最晚的样本，不能误称全天时间平均。

## V1 限制

输入至少覆盖一个完整日；只有部分日时 comparison() 会拒绝输出。单文件可汇总多个完整日，但自动核对只针对最后一个完整日。

不直接支持 Apple 全量 XML、旧 dates/values 或第三方 JSON；不内置跨文件合并。多文件先分目录转换，处理重复日期、不同导出版本及来源后再分析，不能把重复日总量相加。

脚本不按时长均摊累计量。若另算跨午夜均匀分配作为诊断，明确假设，单独保存，不覆盖默认日总量。
