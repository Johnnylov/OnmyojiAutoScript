import 'solana_api.dart';
import 'solana_widgets.dart' show displayTime, reasonLabel, stateLabel;

/// All values belong to one selected profile. A task count, a settled battle,
/// and a completed task in an executor session are different measurements.
class SolanaOverviewMetric {
  final String value;
  final String label;
  final String tooltip;
  final String? observedAge;
  final double? fraction;

  const SolanaOverviewMetric({
    required this.value,
    required this.label,
    required this.tooltip,
    this.observedAge,
    this.fraction,
  });

  String get subtitle => observedAge == null ? label : '$label · $observedAge';
}

JsonObject selectedOverviewRun(JsonObject? overview, String? profileId) {
  if (profileId == null) return {};
  final runs = objects(
    overview?['current_runs'],
  ).where((run) => run['profile_id']?.toString() == profileId).toList();
  return runs.firstWhere(
    (run) => run['state'] == 'running',
    orElse: () => runs.isEmpty ? <String, dynamic>{} : runs.first,
  );
}

JsonObject selectedExecutionCycle(JsonObject? overview, String? profileId) {
  if (profileId == null) return {};
  final cycles = objects(
    overview?['execution_cycles'],
  ).where((cycle) => cycle['profile_id']?.toString() == profileId).toList();
  cycles.sort((a, b) {
    final aTime = DateTime.tryParse(textValue(a['started_at'], ''));
    final bTime = DateTime.tryParse(textValue(b['started_at'], ''));
    return (bTime?.millisecondsSinceEpoch ?? 0).compareTo(
      aTime?.millisecondsSinceEpoch ?? 0,
    );
  });
  return cycles.isEmpty ? {} : cycles.first;
}

class SolanaOverviewMetrics {
  final SolanaOverviewMetric current;
  final SolanaOverviewMetric battles;
  final SolanaOverviewMetric tasks;

  SolanaOverviewMetrics({
    required JsonObject run,
    required JsonObject cycle,
    DateTime? now,
  }) : current = _current(run, now ?? DateTime.now()),
       battles = _battles(run),
       tasks = _tasks(cycle);

  static String? _age(Object? value, DateTime now) {
    final observed = DateTime.tryParse(textValue(value, ''));
    if (observed == null) return null;
    final seconds = now.difference(observed).inSeconds.clamp(0, 1 << 50);
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return '${seconds ~/ 60}分钟前';
    if (seconds < 86400) return '${seconds ~/ 3600}小时前';
    return '${seconds ~/ 86400}天前';
  }

  static num? _count(Object? value) {
    final count = numberValue(value);
    return count != null && count.isFinite && count >= 0 ? count : null;
  }

  static String _number(num value) =>
      value == value.roundToDouble() ? value.toInt().toString() : '$value';

  static String _phase(Object? phase) => switch (phase) {
    'initializing' || 'starting' => '正在初始化',
    'running' => '正在执行',
    'battle' || 'battling' => '战斗中',
    'settlement' || 'settled' => '战斗结算',
    'paused' => '已暂停，保留最近一次进度',
    'yielded' => '已让出，保留最近一次进度',
    'completed' || 'succeeded' => '已完成',
    null || '' => '',
    _ => stateLabel(phase),
  };

  static String _reason(Object? reason, String fallback) => switch (reason) {
    'no_active_run' => '当前配置暂无运行任务。',
    'not_observed_yet' => '尚未观测到可确认的战斗结算，暂不显示为零。',
    'unsupported_task' || 'not_supported' => '该任务暂未提供此项统计。',
    'not_applicable' => '该任务不涉及战斗。',
    null || '' => fallback,
    _ => reasonLabel('$reason'),
  };

  static SolanaOverviewMetric _current(JsonObject run, DateTime now) {
    if (run.isEmpty) {
      return const SolanaOverviewMetric(
        value: '— / —',
        label: '当前值',
        tooltip: '当前配置暂无运行任务。开始执行后显示该任务的当前计数与目标数量。',
      );
    }
    final count = _count(run['current_count']);
    final target = _count(run['target_count']);
    final supported = run['count_supported'] != false;
    final unlimited = target == null && run['target_unbounded'] == true;
    final unit = textValue(run['count_unit'], '未上报');
    final phase = _phase(run['progress_phase'] ?? run['state']);
    final reason = _reason(
      run['count_unavailable_reason'],
      supported ? '执行器尚未上报当前进度。' : '该任务暂未提供可计量的进度。',
    );
    final value = !supported
        ? '暂不支持'
        : count == null
        ? '待上报'
        : '${_number(count)} / ${target != null
              ? _number(target)
              : unlimited
              ? '不限'
              : '—'}';
    return SolanaOverviewMetric(
      value: value,
      label: '当前值',
      tooltip: [
        '当前任务的当前计数 / 目标数量，单位：$unit。',
        if (!supported || count == null) reason,
        if (supported && count != null && target == null)
          unlimited ? '此任务未设置数量上限。' : '执行器尚未上报数量目标，横线不表示无限。',
        if (phase.isNotEmpty) '阶段：$phase。',
        if (['paused', 'yielded'].contains(run['state']) &&
            run['progress_phase'] != run['state'])
          '运行状态：${_phase(run['state'])}。',
        if (run['observed_at'] != null)
          '观测时间：${displayTime(run['observed_at'])}。',
      ].join('\n'),
      observedAge: _age(run['observed_at'], now),
      fraction: supported && count != null && target != null && target > 0
          ? (count / target).clamp(0, 1).toDouble()
          : null,
    );
  }

  static SolanaOverviewMetric _battles(JsonObject run) {
    final count = _count(run['battle_count']);
    final supported = run['battle_supported'] != false;
    final waitingForObservation =
        run['battle_unavailable_reason'] == 'not_observed_yet';
    final value = run.isEmpty
        ? '—'
        : run['battle_unavailable_reason'] == 'not_applicable'
        ? '不涉及战斗'
        : waitingForObservation
        ? '待观测'
        : !supported
        ? '暂不支持'
        : count == null
        ? '待上报'
        : _number(count);
    return SolanaOverviewMetric(
      value: value,
      label: '战斗次数',
      tooltip: run.isEmpty
          ? '当前配置暂无运行任务。'
          : [
              '当前任务本次运行已确认结算的战斗场次，包含胜利和失败。',
              '暂停、继续与调度让出保留累计；新的任务运行重新计数。',
              if (!supported || count == null)
                _reason(
                  run['battle_unavailable_reason'],
                  supported ? '执行器尚未上报战斗计数。' : '该任务暂不支持战斗结算统计。',
                ),
            ].join('\n'),
    );
  }

  static SolanaOverviewMetric _tasks(JsonObject cycle) {
    final completed = _count(cycle['completed_tasks']);
    final total = _count(cycle['total_tasks']);
    final failed = _count(cycle['failed_tasks']);
    final available = completed != null && total != null;
    return SolanaOverviewMetric(
      value: available ? '${_number(completed)} / ${_number(total)}' : '— / —',
      label: '任务进度',
      tooltip: [
        '当前配置本次启动：已成功完成的任务数 / 本轮计划任务数。',
        '同一任务成功完成多次只计一项；本轮新增执行的任务加入总数。',
        '暂停和继续不重置；退出后重新运行开始新一轮。',
        if (!available) '当前配置尚无本轮执行进度。',
        if (failed != null && failed > 0) '失败任务：${_number(failed)} 项，未计入成功完成数。',
      ].join('\n'),
      fraction: available && total > 0
          ? (completed / total).clamp(0, 1).toDouble()
          : null,
    );
  }
}
