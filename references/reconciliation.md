# App 证据与核对

对应项目选择目标日期的日视图；睡眠阶段在“显示更多睡眠数据”中查看。步数优先级查看“数据来源与访问权限”，核对时无需调整顺序。

主图选中的“昨天”与底部“最新”可能不是同一天。根据明确日期、用户当前日期解析相对日期并记录依据，不明确时询问。导出后的最新心率不能验证导出前的最后样本。

HRV 平均、范围、最后样本分别核对；最新读数不能替代日平均。确认 App 指标的 SDNN 说明，不混用其他 HRV 算法。

## 观测字段

JSON 根字段为 date（YYYY-MM-DD）、origin、values、step_source_priority、notes。values 支持以下数值键；不可见值保留 null：

- sleep_minutes、sleep_core_minutes、sleep_deep_minutes、sleep_rem_minutes、sleep_awake_minutes：分钟，小时×60+分钟。
- steps：步；active_energy_kcal：千卡；exercise_minutes：分钟。
- resting_heart_rate_bpm：日平均静息心率。
- hrv_sdnn_mean_ms、hrv_sdnn_min_ms、hrv_sdnn_max_ms、hrv_sdnn_latest_ms：不同 HRV 统计量。
- heart_rate_min_bpm、heart_rate_max_bpm：日范围。

使用转换生成的模板写独立观测文件。保留截图文件名、哈希、日期依据和排除的其他时间点。无需把真实健康数据或私人截图加入 skill 文件夹。

来源优先级可记录 source_id、rank、用户声明及映射依据，未经确认的名称映射保持推断状态，不反推完整来源顺序。

运行同一转换命令并追加 `--observations "<OBSERVATIONS_JSON>"`。观测日期必须匹配窗口最后一个完整日；拿到新证据后重新运行，而非手工把 pending 改为通过。

## 状态解释

| 字段状态 | 含义 |
|---|---|
| pending_health_app_value | 缺该统计量的 App 值 |
| consistent_with_exported_statistic | 在配置容差内相容 |
| difference_requires_review | 超过配置容差 |
| requires_source_or_boundary_review | 无可独立确认的单一日总量 |
| compatible_with_boundary_contribution | 单来源能量 App 值与边界贡献范围相容，未确认算法 |

容差：睡眠 0.5 分钟；步数和锻炼为 0；能量 1 千卡；心率 1 bpm；HRV 1 ms。这些是比较阈值，不能证明 App 内部舍入方式、采集完整性或临床准确性。

pending/partially_compared/review_results 表示观测覆盖程度；review_results 不等于全部通过。health_app_verified 保守为 false，用 displayed_values 及 checks 表达已获得证据。

## 差异排查

先核对日期、单位、统计量及证据时间，再看来源、重叠、跨午夜、睡眠额外查询日。阶段总量分别在原始层和 App 层核对；两层内部各自一致仍可能互不一致。

保留 App 与原始计算两套值，不修改原始睡眠长度来对齐，不把某一天差额应用到其他天。优先手表的样本合计也可能不同于原生合并总步数；均匀时间分配估算只作独立诊断。

苹果参考：[来源优先级](https://support.apple.com/en-us/108779)、[HealthKit 统计合并](https://developer.apple.com/documentation/healthkit/hkstatistics)、[睡眠阶段](https://support.apple.com/en-gb/108906)。
