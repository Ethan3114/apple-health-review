#!/usr/bin/env python3
"""Convert Shortcut Health exports locally; preserve aggregation uncertainty.

Python 3.10+, standard library only. No network or Health database writes.
"""
import argparse
import collections
import hashlib
import json
import math
import statistics
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

UNITS = {'steps': ('count', 'count'), 'active_energy': ('kcal', 'kcal'),
         'exercise_minutes': ('min', 'min'), 'resting_heart_rate': ('count/min', 'bpm'),
         'hrv_sdnn': ('ms', 'ms'), 'heart_rate': ('count/min', 'bpm'), 'sleep': ('', None)}
STAGES = {'核心睡眠': 'core', '深度睡眠': 'deep', '快速动眼睡眠': 'rem',
          '清醒时间': 'awake', '卧床时间': 'in_bed', '卧床': 'in_bed',
          '睡眠': 'asleep_unspecified', '已入睡': 'asleep_unspecified',
          'Core': 'core', 'Deep': 'deep', 'REM': 'rem', 'Awake': 'awake',
          'In Bed': 'in_bed', 'Asleep': 'asleep_unspecified',
          'Asleep (Unspecified)': 'asleep_unspecified'}
ASLEEP = {'core', 'deep', 'rem', 'asleep_unspecified'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def dt(value):
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None, 'Timestamp must have an explicit timezone')
    return result


def identifier(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()[:24]


def normalize(data, timezone):
    require(isinstance(data, dict) and data.get('schema_version') == 2, 'Expected export schema_version=2')
    window_start, window_end = dt(data['window_start']), dt(data['window_end'])
    require(window_start < window_end and dt(data['exported_at']) == window_end, 'Invalid export window')
    modules = data['metrics']
    require(isinstance(modules, list), 'metrics must be a list')
    require(collections.Counter(m['metric'] for m in modules) == collections.Counter(UNITS.keys()),
            'Expected all seven metrics exactly once')
    sources, records, seen = {}, [], {}
    duplicates = 0
    for module in modules:
        metric, rows = module['metric'], module['samples']
        require(isinstance(rows, list) and len(rows) == module['sample_count'], f'{metric}: count mismatch')
        require(module['status'] == ('samples_present' if rows else 'no_samples'), f'{metric}: status mismatch')
        query_start, query_end = dt(module['query_start']), dt(module['query_end'])
        require(query_start <= query_end == window_end, f'{metric}: invalid query window')
        if metric != 'sleep':
            require(query_start == window_start, f'{metric}: unexpected query start')
        for index, row in enumerate(rows):
            require(all(isinstance(row.get(k), str) for k in ('start', 'end', 'value_raw', 'unit_raw', 'source')),
                    f'{metric}[{index}]: invalid record fields')
            start, end = dt(row['start']), dt(row['end'])
            require(query_start <= start <= end <= query_end, f'{metric}[{index}]: invalid interval')
            raw_unit, canonical_unit = UNITS[metric]
            require(row['unit_raw'] == raw_unit, f'{metric}[{index}]: unexpected unit {row["unit_raw"]!r}')
            source_name = row['source'].strip()
            source_id = identifier({'source_name': source_name}) if source_name else None
            if source_id:
                sources[source_id] = {'name': source_name,
                    'kind_hint': 'watch' if 'watch' in source_name.casefold() else 'unspecified',
                    'kind_hint_basis': 'display_name_heuristic', 'priority': None}
            if metric == 'sleep':
                require(row['value_raw'] in STAGES, f'Unsupported sleep stage: {row["value_raw"]!r}')
                value = STAGES[row['value_raw']]
            else:
                value = float(row['value_raw'])
                require(math.isfinite(value) and value >= 0, f'{metric}[{index}]: invalid number')
                if metric == 'steps':
                    require(value.is_integer(), 'Steps must be integer')
                    value = int(value)
            rid = identifier({'metric': metric, **row})
            reference = {'metric': metric, 'index': index}
            if rid in seen:
                seen[rid]['input_record_refs'].append(reference)
                duplicates += 1
                continue
            record = {'id': rid, 'metric': metric, 'start': start.astimezone(timezone).isoformat(),
                'end': end.astimezone(timezone).isoformat(), 'source_id': source_id,
                'value': value, 'unit': canonical_unit, 'value_raw': row['value_raw'],
                'duration_seconds': (end - start).total_seconds(), 'input_record_refs': [reference]}
            records.append(record)
            seen[rid] = record
    records.sort(key=lambda r: (r['start'], r['metric'], r['id']))
    return {'schema_version': 'health.normalized.v1', 'source_export_schema_version': 2,
        'exported_at': data['exported_at'], 'timezone': timezone.key,
        'window_start': window_start.astimezone(timezone).isoformat(),
        'window_end': window_end.astimezone(timezone).isoformat(),
        'normalization': {'numeric_values': 'numbers', 'sleep_stages': 'canonical_english',
            'exact_duplicate_records_removed': duplicates, 'cross_source_overlap_removed': False,
            'source_names_preserved_only_in_source_registry': True},
        'sources': sources, 'records': records}


def union_seconds(intervals):
    intervals = sorted((a, b) for a, b in intervals if b > a)
    if not intervals:
        return 0.0
    total = 0.0
    start, end = intervals[0]
    for a, b in intervals[1:]:
        if a <= end:
            end = max(end, b)
        else:
            total += (end - start).total_seconds()
            start, end = a, b
    return total + (end - start).total_seconds()


def overlap_pairs(rows):
    pairs = []
    for i, row in enumerate(rows):
        a, b = dt(row['start']), dt(row['end'])
        for other in rows[i + 1:]:
            c, d = dt(other['start']), dt(other['end'])
            if min(b, d) > max(a, c):
                pairs.append({'record_ids': [row['id'], other['id']],
                    'cross_source': row['source_id'] != other['source_id']})
    return pairs


def sleep_sessions(records, gap_minutes=90):
    rows = sorted((r for r in records if r['metric'] == 'sleep'), key=lambda r: r['start'])
    groups = []
    for row in rows:
        if not groups or dt(row['start']) > max(dt(r['end']) for r in groups[-1]) + timedelta(minutes=gap_minutes):
            groups.append([])
        groups[-1].append(row)
    sessions = []
    for group in groups:
        start = min(dt(r['start']) for r in group)
        end = max(dt(r['end']) for r in group)
        intervals = [(dt(r['start']), dt(r['end'])) for r in group if r['value'] in ASLEEP]
        stages = {stage: union_seconds([(dt(r['start']), dt(r['end'])) for r in group if r['value'] == stage]) / 60
                  if any(r['value'] == stage for r in group) else None
                  for stage in sorted(set(STAGES.values()))}
        conflicts = []
        by_id = {r['id']: r for r in group}
        for pair in overlap_pairs(group):
            first, second = (by_id[i] for i in pair['record_ids'])
            if first['value'] != second['value'] and first['value'] in ASLEEP | {'awake'} and second['value'] in ASLEEP | {'awake'}:
                conflicts.append(pair)
        sessions.append({'id': identifier([r['id'] for r in group]), 'start': start.isoformat(), 'end': end.isoformat(),
            'assigned_date': end.date().isoformat(), 'sample_count': len(group),
            'asleep_minutes': union_seconds(intervals) / 60 if intervals else None, 'stage_minutes': stages,
            'source_missing': any(r['source_id'] is None for r in group),
            'conflicting_stage_overlap_pairs': conflicts, 'record_ids': [r['id'] for r in group]})
    return sessions


def cumulative(rows, start, end, sources):
    relevant = [r for r in rows if (dt(r['start']) < end and dt(r['end']) > start)
                or (dt(r['start']) == dt(r['end']) and start <= dt(r['start']) < end)]
    grouped = collections.defaultdict(list)
    for r in relevant:
        grouped[r['source_id']].append(r)
    per_source = []
    for sid, group in sorted(grouped.items(), key=lambda x: str(x[0])):
        within = [r for r in group if start <= dt(r['start']) and dt(r['end']) <= end]
        boundary = [r for r in group if r not in within]
        overlap = overlap_pairs(group)
        lower = math.fsum(r['value'] for r in within)
        upper = lower + math.fsum(r['value'] for r in boundary)
        per_source.append({'source_id': sid, 'kind_hint': sources.get(sid, {}).get('kind_hint', 'unknown'),
            'sample_count': len(group), 'contained_sample_sum': lower,
            'exported_sample_contribution_bounds': [lower, upper] if not overlap else None,
            'start_day_sum_candidate': math.fsum(r['value'] for r in group if start <= dt(r['start']) < end),
            'boundary_record_ids': [r['id'] for r in boundary], 'overlap_pairs': overlap})
    clean = len(per_source) == 1 and not per_source[0]['boundary_record_ids'] and not per_source[0]['overlap_pairs']
    return {'value': per_source[0]['contained_sample_sum'] if clean else None,
        'status': 'observed_sample_sum' if clean else 'needs_source_or_boundary_resolution' if relevant else 'no_observed_samples',
        'per_source': per_source, 'health_app_verified': False,
        'bounds_scope': 'Contribution of exported records only; not a bound on the full Health app total',
        'cross_source_overlap_pairs': sum(p['cross_source'] for p in overlap_pairs(relevant))}


def discrete(rows, start, end):
    selected = [r for r in rows if start <= dt(r['start']) < end]
    values = [r['value'] for r in selected]
    return {'sample_count': len(values), 'min': min(values) if values else None,
        'max': max(values) if values else None, 'sample_mean': math.fsum(values) / len(values) if values else None,
        'sample_median': statistics.median(values) if values else None,
        'latest': max(selected, key=lambda r: r['start'])['value'] if selected else None,
        'mean_method': 'unweighted_arithmetic_mean_of_exported_samples', 'health_app_verified': False}


def summarize(normalized):
    timezone = ZoneInfo(normalized['timezone'])
    window_start, window_end = dt(normalized['window_start']), dt(normalized['window_end'])
    records = normalized['records']
    sessions = sleep_sessions(records)
    by_metric = collections.defaultdict(list)
    for r in records:
        by_metric[r['metric']].append(r)
    day = window_start.date()
    days = []
    while datetime.combine(day, time.min, timezone) < window_end:
        start = datetime.combine(day, time.min, timezone)
        end = start + timedelta(days=1)
        observed_start, observed_end = max(start, window_start), min(end, window_end)
        nights = [s for s in sessions if s['assigned_date'] == day.isoformat()]
        asleep_values = [s['asleep_minutes'] for s in nights if s['asleep_minutes'] is not None]
        stage_values = {stage: [s['stage_minutes'][stage] for s in nights if s['stage_minutes'][stage] is not None]
                        for stage in sorted(set(STAGES.values()))}
        sleep = {'asleep_minutes': sum(asleep_values) if asleep_values else None,
            'stage_minutes': {stage: sum(values) if values else None for stage, values in stage_values.items()} if nights else None,
            'session_ids': [s['id'] for s in nights], 'source_missing': any(s['source_missing'] for s in nights),
            'conflicting_stages': any(s['conflicting_stage_overlap_pairs'] for s in nights),
            'date_assignment': 'session_end_date', 'health_app_verified': False}
        days.append({'date': day.isoformat(), 'calendar_window_complete': start >= window_start and end <= window_end,
            'sampling_completeness': 'not_verified_against_health_app',
            'observed_window_start': observed_start.isoformat(), 'observed_window_end': observed_end.isoformat(),
            'sleep': sleep,
            **{key: cumulative(by_metric[key], observed_start, observed_end, normalized['sources'])
               for key in ('steps', 'active_energy', 'exercise_minutes')},
            **{key: discrete(by_metric[key], observed_start, observed_end)
               for key in ('resting_heart_rate', 'hrv_sdnn', 'heart_rate')}})
        day += timedelta(days=1)
    return {'schema_version': 'health.model_input.v1', 'exported_at': normalized['exported_at'],
        'timezone': timezone.key, 'health_app_reconciliation': 'pending', 'daily': days,
        'sleep_sessions': [{**{k: v for k, v in s.items() if k != 'record_ids'},
                            'assigned_to_target_window_date': s['assigned_date'] in {d['date'] for d in days}}
                           for s in sessions],
        'policies': {'sleep_session_gap_minutes': 90, 'sleep_day': 'session_end_date',
            'sleep_duration': 'union_of_core_deep_rem_and_unspecified_asleep_intervals; awake_and_in_bed_excluded',
            'cumulative_cross_midnight': 'preserve_boundary_records; do_not_prorate',
            'multiple_sources': 'keep_separate; source_priority_unknown', 'numeric_mean': 'unweighted_sample_mean'},
        'limitations': ['Sleep sources missing in this export.',
            'Sample queries filter by start time; a record beginning before the export window may be absent.',
            'Exported cumulative samples cannot reproduce Health source merging and daily splitting exactly.',
            'A complete calendar window does not prove continuous wear or synchronization completeness.',
            'No source names are present in this model input; source IDs are local opaque labels.',
            'Partial days must not be treated as full days; missing readings are not zero.',
            'No medical conclusions are produced.']}


COMPARISON_FIELDS = {
    'sleep_minutes': ('睡眠时长', 'min', 0.5), 'sleep_core_minutes': ('核心睡眠', 'min', 0.5),
    'sleep_deep_minutes': ('深度睡眠', 'min', 0.5), 'sleep_rem_minutes': ('快速动眼睡眠', 'min', 0.5),
    'sleep_awake_minutes': ('清醒时间', 'min', 0.5), 'steps': ('步数', 'count', 0),
    'active_energy_kcal': ('活动能量', 'kcal', 1), 'exercise_minutes': ('锻炼分钟数', 'min', 0),
    'resting_heart_rate_bpm': ('静息心率', 'bpm', 1), 'hrv_sdnn_mean_ms': ('HRV 样本平均值', 'ms', 1),
    'hrv_sdnn_min_ms': ('HRV 最低值', 'ms', 1), 'hrv_sdnn_max_ms': ('HRV 最高值', 'ms', 1),
    'hrv_sdnn_latest_ms': ('HRV 当天最后样本', 'ms', 1),
    'heart_rate_min_bpm': ('心率最低值', 'bpm', 1), 'heart_rate_max_bpm': ('心率最高值', 'bpm', 1)}


def comparison(summary, observations=None):
    full_days = [d for d in summary['daily'] if d['calendar_window_complete']]
    require(bool(full_days), 'No complete calendar day available for comparison')
    day = full_days[-1]
    expected = {'sleep_minutes': day['sleep']['asleep_minutes'], 'steps': day['steps']['value'],
        'active_energy_kcal': day['active_energy']['value'], 'exercise_minutes': day['exercise_minutes']['value'],
        'resting_heart_rate_bpm': day['resting_heart_rate']['sample_mean'],
        'hrv_sdnn_mean_ms': day['hrv_sdnn']['sample_mean'],
        'hrv_sdnn_min_ms': day['hrv_sdnn']['min'], 'hrv_sdnn_max_ms': day['hrv_sdnn']['max'],
        'hrv_sdnn_latest_ms': day['hrv_sdnn']['latest'], 'heart_rate_min_bpm': day['heart_rate']['min'],
        'heart_rate_max_bpm': day['heart_rate']['max']}
    for stage in ('core', 'deep', 'rem', 'awake'):
        stages = day['sleep']['stage_minutes']
        expected[f'sleep_{stage}_minutes'] = stages[stage] if stages else None
    if observations:
        require(observations.get('date') == day['date'], 'Observation date does not match comparison date')
    checks = []
    for key, (label, unit, tolerance) in COMPARISON_FIELDS.items():
        actual = observations.get('values', {}).get(key) if observations else None
        require(actual is None or (isinstance(actual, (int, float)) and not isinstance(actual, bool)
                                  and math.isfinite(actual) and actual >= 0), f'Invalid observed value: {key}')
        value = expected[key]
        status = 'pending_health_app_value'
        if actual is not None:
            status = ('consistent_with_exported_statistic' if abs(actual - value) <= tolerance
                      else 'difference_requires_review') if value is not None else 'requires_source_or_boundary_review'
        item = {'key': key, 'label': label, 'unit': unit, 'exported_statistic': value,
            'health_app_value': actual, 'display_rounding_tolerance': tolerance, 'status': status}
        if actual is not None and value is not None:
            item['app_minus_exported_statistic'] = actual - value
        if key in ('steps', 'active_energy_kcal'):
            item['per_source_candidates'] = day['steps' if key == 'steps' else 'active_energy']['per_source']
            if key == 'active_energy_kcal' and actual is not None and value is None:
                candidates = item['per_source_candidates']
                if len(candidates) == 1 and candidates[0]['exported_sample_contribution_bounds'] is not None:
                    lower, upper = candidates[0]['exported_sample_contribution_bounds']
                    if lower - tolerance <= actual <= upper + tolerance:
                        item['status'] = 'compatible_with_boundary_contribution'
        checks.append(item)
    supplied = sum(i['health_app_value'] is not None for i in checks)
    return {'schema_version': 'health.reconciliation.v1', 'date': day['date'],
        'status': 'pending' if not supplied else 'partially_compared' if supplied < len(checks) else 'review_results',
        'checks': checks, 'observations_count': supplied,
        'step_source_priority': observations.get('step_source_priority', []) if observations else [],
        'note': 'Matching rounded statistics alone does not verify original type identifiers, source priority, or capture completeness.'}


def duration(value):
    if value is None:
        return '无样本'
    seconds = round(value * 60)
    return f'{seconds // 3600}小时{seconds % 3600 // 60}分{seconds % 60}秒'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def render_checklist(summary, reconciliation):
    day = next(d for d in summary['daily'] if d['date'] == reconciliation['date'])
    lines = [f'# 健康 App 核对：{day["date"]}', '',
        f'转换已完成，已有 {reconciliation["observations_count"]} 项 App 显示数值用于比较。原始计算值与 App 显示值分别保留，不覆盖差异。', '',
        '| 项目 | 导出计算值 | App 显示值 | 核对状态 |', '|---|---|---|---|']
    for item in reconciliation['checks']:
        value = item['exported_statistic']
        if value is None:
            shown = '见下方来源／跨午夜说明' if item['key'] in ('steps', 'active_energy_kcal') else '无对应样本'
        elif item['unit'] == 'min' and item['key'].startswith('sleep'):
            shown = duration(value)
        else:
            shown = f'{value:.3f}'.rstrip('0').rstrip('.') + ' ' + item['unit']
        status_label = {'pending_health_app_value': '待提供 App 数值',
                        'consistent_with_exported_statistic': '与导出计算值一致',
                        'difference_requires_review': '存在差异，需检查',
                        'compatible_with_boundary_contribution': '与跨午夜贡献范围相容，未确认算法',
                        'requires_source_or_boundary_review': '需核对来源或跨日归属'}[item['status']]
        actual = item['health_app_value']
        app_shown = '未提供' if actual is None else duration(actual) if item['unit'] == 'min' and item['key'].startswith('sleep') else f'{actual:g} {item["unit"]}'
        lines.append(f'| {item["label"]} | {shown} | {app_shown} | {status_label} |')
    lines += ['', '## 步数与活动能量', '',
        '健康 App 会按来源优先级处理同类数据。本转换保留各来源，不把手机和手表的步数简单相加，也不假定按区间长度均摊跨午夜记录。', '']
    for key, label in (('steps', '步数'), ('active_energy', '活动能量')):
        for s in day[key]['per_source']:
            bounds = s['exported_sample_contribution_bounds']
            hint = '名称提示为手表' if s['kind_hint'] == 'watch' else '设备类型未确认'
            bounds_text = '需先处理同来源重叠' if bounds is None else f'{bounds[0]:.3f}–{bounds[1]:.3f}'
            lines.append(f'- {label}，来源 `{s["source_id"]}`（{hint}）：'
                f'按开始日期计入的候选值 {s["start_day_sum_candidate"]:.3f}；'
                f'完全在当天内的样本合计 {s["contained_sample_sum"]:.3f}；'
                f'跨边界样本 {len(s["boundary_record_ids"])} 条；'
                f'导出记录在当天的贡献范围 {bounds_text}。')
    lines += ['', '这些范围只约束已经导出的记录，不是健康 App 完整日总量的上下界。步数两个来源之间有 '
        f'{day["steps"]["cross_source_overlap_pairs"]} 对区间重叠。'
        + ('用户已提供首选来源，记录在核对 JSON；这不代表已复现苹果合并算法。' if reconciliation.get('step_source_priority') else '健康 App 的真实来源顺序仍未知。'), '',
        '## 归日与统计口径', '',
        f'- 核对日期是 {day["date"]}；不完整日期单独标记：'
        + '、'.join(d['date'] for d in summary['daily'] if not d['calendar_window_complete']) + '。',
        '- 睡眠按连续记录构成睡眠段（间隔超过 90 分钟另起一段），归入该段结束日期。这个约定需要与 App 的日期显示核对。',
        '- 睡眠时长取核心、深度、快速动眼和未细分睡眠区间的并集；清醒、卧床不计入睡眠时长。',
        '- 睡眠来源为空，保留 unknown，不补写为 Apple Watch。',
        '- 心率与 HRV 平均值是样本算术平均，不是全天按时间加权平均。HRV 的平均、范围和当天最后样本分别核对，不能把最新一次值当成平均值。',
        '- 查询按样本开始时间筛选，可能缺少窗口开始前启动、窗口内结束的记录。', '',
        '## 在 iPhone 上核对', '',
        f'在健康 App 搜索对应项目，进入“日”视图，切到 {day["date"]}。睡眠查看睡眠时长与各阶段；其余项目记录当天总量、平均值或范围。', '',
        '步数来源顺序作为核对依据保留；无需为核对调整顺序，以免改变基准。', '',
        '可把数值直接发给本对话，或填写 `health-app-observations-template.json` 后用 `--observations` 重新运行脚本。睡眠数值填写分钟，例如 6 小时 1 分为 361；未知值保留 null。', '',
        '数值一致只表示导出计算值与 App 显示值在舍入范围内一致，不能由此认定采集完全。', '',
        '## 参考', '',
        '- [苹果：健康数据与来源优先级](https://support.apple.com/en-us/108779)',
        '- [苹果：HealthKit 统计会合并来源](https://developer.apple.com/documentation/healthkit/hkstatistics)',
        '- [苹果：睡眠时长与阶段查看](https://support.apple.com/en-gb/108906)', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--timezone', default='Asia/Shanghai')
    parser.add_argument('--observations', type=Path)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    normalized = normalize(json.loads(raw), ZoneInfo(args.timezone))
    normalized['input_file'] = {'name': args.input.name, 'sha256': hashlib.sha256(raw).hexdigest()}
    summary = summarize(normalized)
    summary['input_file'] = normalized['input_file']
    observations = json.loads(args.observations.read_text()) if args.observations else None
    result = comparison(summary, observations)
    summary['health_app_reconciliation'] = result['status']
    summary['health_app_reconciliation_details'] = {'date': result['date'],
        'observations_count': result['observations_count'], 'checks': result['checks'],
        'step_source_priority': result['step_source_priority']}
    if observations:
        target_day = next(d for d in summary['daily'] if d['date'] == result['date'])
        target_day['health_app_displayed_values'] = {i['key']: i['health_app_value'] for i in result['checks'] if i['health_app_value'] is not None}
        target_day['health_app_displayed_values_origin'] = observations.get('origin', 'user_supplied_observation')
        summary['policies']['observations'] = 'Displayed UI values are separate observations, not replacements for derived sample statistics.'
        if result['step_source_priority']:
            summary['policies']['multiple_sources'] = 'Keep sources separate; reported step source priority is recorded, but native source merging is not reproduced.'
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / 'health-normalized.json', normalized)
    write_json(args.output_dir / 'health-model-input.json', summary)
    write_json(args.output_dir / 'health-app-reconciliation.json', result)
    template = args.output_dir / 'health-app-observations-template.json'
    if not template.exists():
        write_json(template, {'date': result['date'], 'values': {k: None for k in COMPARISON_FIELDS},
            'step_source_priority': [], 'notes': ''})
    (args.output_dir / '健康App核对清单.md').write_text(render_checklist(summary, result))
    print(json.dumps({'records': len(normalized['records']), 'duplicates_removed': normalized['normalization']['exact_duplicate_records_removed'],
        'days': [d['date'] for d in summary['daily']], 'comparison_status': result['status']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
