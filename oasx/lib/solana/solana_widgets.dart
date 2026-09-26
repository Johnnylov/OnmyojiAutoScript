import 'dart:math' as math;

import 'package:flutter/material.dart';

import 'solana_api.dart';
import 'solana_controller.dart';

export 'solana_labels.dart' show taskLabel;

const solanaBlue = Color(0xFF82729E);
const solanaGreen = Color(0xFF27856C);
const solanaAmber = Color(0xFFAF761A);
const solanaRed = Color(0xFFBE4B4B);
const solanaFormTextStyle = TextStyle(
  fontFamily: 'Microsoft YaHei',
  fontFamilyFallback: ['PingFang SC', 'Segoe UI', 'sans-serif'],
  fontSize: 13,
  fontWeight: FontWeight.w400,
  height: 1.4,
  letterSpacing: 0,
);

ThemeData solanaTheme(Brightness brightness, {bool nightBackdrop = false}) {
  final dark = brightness == Brightness.dark;
  final scheme =
      ColorScheme.fromSeed(
        seedColor: solanaBlue,
        brightness: brightness,
      ).copyWith(
        surface: dark
            ? const Color(0xBB383344)
            : nightBackdrop
            ? const Color(0xF2FFFFFF)
            : const Color(0xAAFFFFFF),
      );
  // Floating menus need their own opaque surface: the page cards remain glass.
  final menuSurface = dark ? const Color(0xFF302B3B) : const Color(0xFFFDFCFF);
  final menuStyle = MenuStyle(
    backgroundColor: WidgetStatePropertyAll(menuSurface),
    surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
    elevation: const WidgetStatePropertyAll(8),
    padding: const WidgetStatePropertyAll(EdgeInsets.symmetric(vertical: 6)),
    shape: WidgetStatePropertyAll(
      RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
    ),
  );
  return ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: scheme,
    canvasColor: menuSurface,
    hoverColor: dark ? const Color(0xFF45404F) : const Color(0xFFF0EDF5),
    focusColor: dark ? const Color(0xFF514365) : const Color(0xFFE8E0F2),
    scaffoldBackgroundColor: Colors.transparent,
    fontFamily: 'Microsoft YaHei',
    fontFamilyFallback: const [
      'PingFang SC',
      'Segoe UI',
      'Segoe UI Emoji',
      'sans-serif',
    ],
    textTheme: const TextTheme(
      displayLarge: TextStyle(
        fontSize: 24,
        fontWeight: FontWeight.w600,
        letterSpacing: 0,
      ),
      displayMedium: TextStyle(
        fontSize: 22,
        fontWeight: FontWeight.w600,
        letterSpacing: 0,
      ),
      displaySmall: TextStyle(
        fontSize: 20,
        fontWeight: FontWeight.w600,
        letterSpacing: 0,
      ),
      headlineLarge: TextStyle(
        fontSize: 20,
        fontWeight: FontWeight.w600,
        letterSpacing: 0,
      ),
      headlineMedium: TextStyle(
        fontSize: 19,
        fontWeight: FontWeight.w600,
        letterSpacing: 0,
      ),
      headlineSmall: TextStyle(
        fontSize: 18,
        fontWeight: FontWeight.w600,
        letterSpacing: 0,
      ),
      titleLarge: TextStyle(
        fontSize: 16,
        fontWeight: FontWeight.w600,
        letterSpacing: 0,
      ),
      titleMedium: TextStyle(
        fontSize: 13,
        fontWeight: FontWeight.w600,
        letterSpacing: 0,
      ),
      titleSmall: TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w600,
        letterSpacing: 0,
      ),
      bodyLarge: solanaFormTextStyle,
      bodyMedium: TextStyle(
        fontSize: 12.5,
        height: 1.45,
        fontWeight: FontWeight.w400,
        letterSpacing: 0,
      ),
      bodySmall: TextStyle(
        fontSize: 11.5,
        height: 1.45,
        fontWeight: FontWeight.w400,
        letterSpacing: 0,
      ),
      labelLarge: TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w500,
        letterSpacing: 0,
      ),
      labelMedium: TextStyle(
        fontSize: 11.5,
        fontWeight: FontWeight.w500,
        letterSpacing: 0,
      ),
      labelSmall: TextStyle(
        fontSize: 11,
        fontWeight: FontWeight.w500,
        letterSpacing: 0,
      ),
    ),
    dividerColor: dark ? const Color(0xFF303642) : const Color(0xFFE9ECF2),
    inputDecorationTheme: InputDecorationTheme(
      isDense: true,
      filled: true,
      fillColor: dark ? const Color(0xFF242B37) : const Color(0xFFF6F7FB),
      labelStyle: const TextStyle(
        fontSize: 12,
        fontWeight: FontWeight.w400,
        letterSpacing: 0,
      ),
      // InputDecorator scales a floating label by .75, giving 11.25 px here.
      floatingLabelStyle: const TextStyle(
        fontSize: 15,
        fontWeight: FontWeight.w400,
        letterSpacing: 0,
      ),
      hintStyle: const TextStyle(
        fontSize: 13,
        fontWeight: FontWeight.w400,
        letterSpacing: 0,
      ),
      helperStyle: const TextStyle(
        fontSize: 11,
        height: 1.4,
        fontWeight: FontWeight.w400,
        letterSpacing: 0,
      ),
      errorStyle: const TextStyle(
        fontSize: 11,
        height: 1.4,
        fontWeight: FontWeight.w400,
        letterSpacing: 0,
      ),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: BorderSide(
          color: dark ? const Color(0xFF51495E) : const Color(0xFFD8D2E2),
        ),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: BorderSide(
          color: dark ? const Color(0xFF51495E) : const Color(0xFFD8D2E2),
        ),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(10),
        borderSide: const BorderSide(color: solanaBlue),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 11, vertical: 10),
    ),
    dropdownMenuTheme: DropdownMenuThemeData(
      textStyle: solanaFormTextStyle,
      menuStyle: menuStyle,
    ),
    menuTheme: MenuThemeData(style: menuStyle),
    menuButtonTheme: MenuButtonThemeData(
      style: ButtonStyle(
        textStyle: const WidgetStatePropertyAll(solanaFormTextStyle),
        backgroundColor: WidgetStateProperty.resolveWith((states) {
          if (states.contains(WidgetState.selected) ||
              states.contains(WidgetState.focused)) {
            return dark ? const Color(0xFF514365) : const Color(0xFFE8E0F2);
          }
          if (states.contains(WidgetState.hovered)) {
            return dark ? const Color(0xFF45404F) : const Color(0xFFF0EDF5);
          }
          return null;
        }),
      ),
    ),
    popupMenuTheme: PopupMenuThemeData(
      color: menuSurface,
      surfaceTintColor: Colors.transparent,
      textStyle: solanaFormTextStyle,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: FilledButton.styleFrom(
        backgroundColor: solanaBlue,
        foregroundColor: Colors.white,
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: OutlinedButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        side: BorderSide(
          color: dark ? const Color(0xFF414957) : const Color(0xFFDDE2ED),
        ),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
      ),
    ),
    chipTheme: ChipThemeData(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
    ),
  );
}

class Surface extends StatelessWidget {
  final Widget child;
  final EdgeInsetsGeometry padding;
  final Color? color;
  const Surface({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(14),
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    final dark = Theme.of(context).brightness == Brightness.dark;
    return Container(
      decoration: BoxDecoration(
        color: color ?? Theme.of(context).colorScheme.surface,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: dark ? const Color(0x555D526F) : const Color(0xCCFFFFFF),
        ),
      ),
      padding: padding,
      child: child,
    );
  }
}

class SectionHeading extends StatelessWidget {
  final String title;
  final String? subtitle;
  final Widget? trailing;
  const SectionHeading(this.title, {super.key, this.subtitle, this.trailing});
  @override
  Widget build(BuildContext context) => Row(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleMedium),
            if (subtitle != null) ...[
              const SizedBox(height: 4),
              Text(
                subtitle!,
                style: TextStyle(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                  fontSize: 11,
                ),
              ),
            ],
          ],
        ),
      ),
      if (trailing != null) trailing!,
    ],
  );
}

class StateBadge extends StatelessWidget {
  final String label;
  final Color color;
  final bool dot;
  const StateBadge(
    this.label, {
    super.key,
    this.color = solanaBlue,
    this.dot = true,
  });
  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
    decoration: BoxDecoration(
      color: color.withValues(alpha: .10),
      borderRadius: BorderRadius.circular(7),
    ),
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (dot) ...[
          Container(
            width: 5,
            height: 5,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
        ],
        Flexible(
          child: Text(
            label,
            overflow: TextOverflow.ellipsis,
            style: TextStyle(
              fontSize: 11,
              color: color,
              fontWeight: FontWeight.w600,
            ),
          ),
        ),
      ],
    ),
  );
}

class EmptyState extends StatelessWidget {
  final String title;
  final String description;
  final IconData icon;
  final Widget? action;
  const EmptyState(
    this.title,
    this.description, {
    super.key,
    this.icon = Icons.inbox_outlined,
    this.action,
  });
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 14),
    child: Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: solanaBlue.withValues(alpha: .06),
              shape: BoxShape.circle,
            ),
            child: Icon(
              icon,
              color: solanaBlue.withValues(alpha: .65),
              size: 26,
            ),
          ),
          const SizedBox(height: 14),
          Text(
            title,
            style: Theme.of(context).textTheme.titleMedium,
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 7),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 380),
            child: Text(
              description,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
                fontSize: 12,
              ),
            ),
          ),
          if (action != null) ...[const SizedBox(height: 16), action!],
        ],
      ),
    ),
  );
}

class Notice extends StatelessWidget {
  final String message;
  final bool error;
  final VoidCallback? onClose;
  const Notice(this.message, {super.key, this.error = false, this.onClose});
  @override
  Widget build(BuildContext context) {
    final color = error ? solanaRed : solanaAmber;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .08),
        border: Border.all(color: color.withValues(alpha: .2)),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          Icon(
            error ? Icons.error_outline_rounded : Icons.info_outline_rounded,
            size: 18,
            color: color,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(message, style: TextStyle(color: color, fontSize: 12)),
          ),
          if (onClose != null)
            IconButton(
              onPressed: onClose,
              icon: Icon(Icons.close, size: 16, color: color),
              visualDensity: VisualDensity.compact,
            ),
        ],
      ),
    );
  }
}

class RemoteBody extends StatelessWidget {
  final RemoteData remote;
  final Widget Function(JsonObject) builder;
  final VoidCallback? retry;
  const RemoteBody({
    super.key,
    required this.remote,
    required this.builder,
    this.retry,
  });

  @override
  Widget build(BuildContext context) {
    if (remote.loading && remote.data == null) {
      return const Padding(
        padding: EdgeInsets.all(42),
        child: Center(
          child: SizedBox(
            width: 24,
            height: 24,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ),
      );
    }
    if (remote.data == null) {
      final error = remote.error;
      return EmptyState(
        error?.code == 'history_expired'
            ? '这段历史已清理'
            : error != null
            ? '暂时无法获取数据'
            : '等待服务数据',
        error?.message ?? '连接后端后会显示真实的运行记录。',
        icon: Icons.cloud_off_outlined,
        action: retry == null
            ? null
            : OutlinedButton.icon(
                onPressed: retry,
                icon: const Icon(Icons.refresh_rounded, size: 16),
                label: const Text('重新加载'),
              ),
      );
    }
    final data = remote.data!;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (remote.error != null) ...[
          Notice('${remote.error!.message} · 当前显示上次获取的数据', error: true),
          const SizedBox(height: 12),
        ],
        if (data['incomplete'] == true ||
            (data['gaps'] is List && (data['gaps'] as List).isNotEmpty)) ...[
          const Notice('所选范围存在记录缺口，统计仅覆盖已保存的数据。'),
          const SizedBox(height: 12),
        ],
        builder(data),
      ],
    );
  }
}

class MetricCard extends StatelessWidget {
  final String label;
  final String value;
  final String detail;
  final IconData icon;
  final Color accent;
  const MetricCard(
    this.label,
    this.value,
    this.detail, {
    super.key,
    required this.icon,
    this.accent = solanaBlue,
  });
  @override
  Widget build(BuildContext context) => Surface(
    padding: const EdgeInsets.all(12),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                label,
                style: TextStyle(
                  fontSize: 12,
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
              ),
            ),
            Container(
              padding: const EdgeInsets.all(7),
              decoration: BoxDecoration(
                color: accent.withValues(alpha: .08),
                borderRadius: BorderRadius.circular(9),
              ),
              child: Icon(icon, color: accent, size: 17),
            ),
          ],
        ),
        const SizedBox(height: 14),
        Text(
          value,
          style: Theme.of(
            context,
          ).textTheme.headlineMedium?.copyWith(fontSize: 18),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        const SizedBox(height: 6),
        Text(
          detail,
          style: TextStyle(
            fontSize: 11,
            color: Theme.of(context).colorScheme.onSurfaceVariant,
          ),
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
        ),
      ],
    ),
  );
}

class ResponsiveCards extends StatelessWidget {
  final List<Widget> children;
  const ResponsiveCards({super.key, required this.children});
  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, bounds) {
      final columns = bounds.maxWidth > 520
          ? 4
          : bounds.maxWidth > 450
          ? 2
          : 1;
      final width = (bounds.maxWidth - (columns - 1) * 14) / columns;
      return Wrap(
        spacing: 14,
        runSpacing: 14,
        children: children
            .map((child) => SizedBox(width: width, child: child))
            .toList(),
      );
    },
  );
}

String stateLabel(Object? state) =>
    const {
      'running': '运行中',
      'queued': '排队中',
      'waiting': '等待中',
      'waiting_resource': '等待设备',
      'stopped': '已停止',
      'idle': '空闲',
      'succeeded': '成功',
      'success': '成功',
      'failed': '失败',
      'cancelled': '已取消',
      'interrupted': '已中断',
      'crashed': '异常退出',
      'yielded': '已让出',
      'paused': '已暂停',
      'inactive': '未启动',
      'warning': '需要处理',
      'starting': '启动中',
      'started': '已启动',
      'restarted': '已重新启动',
      'control_pending': '等待操作确认',
      'pausing': '等待安全位置',
      'stopping': '等待安全停止',
      'pause_requested': '等待安全位置',
      'stop_requested': '等待安全停止',
      'recovery_requested': '恢复中',
      'needs_reconciliation': '待核验',
      'ready': '可执行',
      'degraded': '记录受限',
      'restricted': '记录受限',
      'unavailable': '存储不可用',
      'healthy': '正常',
      'ok': '正常',
      'offline': '离线',
      'unknown': '状态未知',
      'disabled': '未启用',
      'active': '已启用',
      'requested': '已请求',
      'acquired': '已取得执行权',
      'partially_executed': '部分操作已执行',
      'executed_not_saved': '已执行，记录未保存',
      'unknown_state_accepted': '已确认未知状态',
      'completed': '已完成',
      'rejected': '未接受',
      'identity_reconciled': '身份已核验',
      'not_applied': '未应用',
      'accepted': '已接受',
      'not_saved': '未保存',
      'already_stopped': '已停止',
      'observed': '已观测',
      'stale': '已过期',
      'saved': '已保存',
      'not_recorded': '未记录',
      'retry_scheduled': '已安排重试',
      'server_update_delayed': '等待服务器维护结束',
      'team_wait_failed': '组队等待失败',
    }[state?.toString()] ??
    _chineseOrFallback(state, '状态未知');

String _chineseOrFallback(Object? value, String fallback) {
  final text = value?.toString() ?? '';
  return RegExp(r'[\u3400-\u9fff]').hasMatch(text) ? text : fallback;
}

String reasonLabel(String reason) {
  final label = const {
    'profile_inactive': '计划尚未启动',
    'paused': '计划已暂停',
    'scheduled': '尚未到计划运行时间',
    'waiting_device': '等待设备执行权',
    'fair_share': '按执行份额安排',
    'deadline': '活动即将截止，优先执行',
    'recovery': '优先处理异常恢复',
    'max_wait': '等待较久，安排一次执行机会',
    'urgent_budget_exhausted': '优先执行预算已用完',
    'recovery_budget_exhausted': '恢复次数已达上限，需要处理',
    'recovery_budget_disabled': '恢复预算为零，请调整后重新运行',
    'recovery_backoff': '等待恢复重试间隔',
    'storage_reconciliation_required': '存储恢复后需要核验状态',
    'real_deadline': '活动即将截止，优先执行',
    'deadline_expired': '活动已到期，任务已自动停用',
    'waiting_limit': '等待较久，安排一次执行机会',
    'legacy_order': '按原有任务顺序执行',
    'recovery_blocked': '异常恢复暂不可执行',
    'activity_window_closed': '当前不在活动开放时间',
    'business_precondition:activity_window_closed': '当前不在活动开放时间',
    'previous_process_still_owns_device': '等待原执行器释放设备',
    'device_owned': '设备正由其他配置使用',
    'another_profile_selected': '当前轮到其他配置执行',
    'device_recovery_pending': '等待设备异常恢复',
    'user_closed_uncertain_run': '已结束未确认运行',
    'user_restart': '手动重新运行，旧运行已记为中断',
    'runtime_prepared': '游戏已恢复，重新安排任务',
    'team_rendezvous_pending': '等待双方约定的组队任务完成',
    'safe_stop': '已请求在安全位置停止',
    'unverified_checkpoint': '上次执行进度需要核验',
    'resume': '继续之前的运行',
    'stopped': '计划已停止',
    'service_restarted': '服务重启后待核验',
    'executor_exit': '执行器已退出',
    'worker_exit': '执行器已退出',
    'business_failure': '任务未成功完成',
    'runtime_preparation': '任务准备阶段异常',
    'legacy_audit_persistence_failed': '操作记录保存失败，需要核验',
    'legacy_mutation_unresolved': '上次操作结果尚未核验',
    'retry_scheduled': '已安排重试',
    'server_update_delayed': '等待服务器维护结束',
    'team_wait_failed': '组队等待失败',
    'team_preempted': '已切换至组队任务',
    'team_partner_finished': '队友已结束任务',
    'skipped': '本次任务已跳过',
    'yielded': '已让出执行机会',
    'cancelled': '任务已取消',
    'interrupted': '任务已中断',
    'recovery_requested': '正在处理异常恢复',
    'recovered': '已完成恢复',
    'GameNotRunningError': '游戏未运行',
    'GameStuckError': '游戏界面长时间未响应',
    'GameTooManyClickError': '重复点击过多，已停止操作',
    'GameBugError': '游戏状态异常',
    'GamePageUnknownError': '无法识别当前游戏页面',
    'BattleTransitionTimeout': '等待战斗切换超时',
    'ActivityPreparationTimeout': '活动准备超时',
    'EmulatorNotRunningError': '模拟器未运行',
    'RequestHumanTakeover': '需要手动处理',
    'ScriptError': '任务执行异常',
    'ScriptEnd': '任务已结束',
    'TaskEnd': '任务已结束',
    'TaskDeferred': '任务已延后',
  }[reason];
  if (label != null) return label;
  if (DateTime.tryParse(reason) != null) {
    return '计划时间：${displayTime(reason)}';
  }
  return _chineseOrFallback(reason, '暂无原因说明');
}

Color stateColor(Object? state) {
  final value = state?.toString();
  if (['running', 'succeeded', 'success', 'healthy', 'ok'].contains(value)) {
    return solanaGreen;
  }
  if (['failed', 'crashed', 'offline', 'unavailable'].contains(value)) {
    return solanaRed;
  }
  if ([
    'recovery_requested',
    'needs_reconciliation',
    'degraded',
    'restricted',
    'interrupted',
  ].contains(value)) {
    return solanaAmber;
  }
  return solanaBlue;
}

String displayTime(Object? value) {
  final parsed = DateTime.tryParse(value?.toString() ?? '');
  if (parsed == null) return textValue(value);
  final local = parsed.toLocal();
  return '${local.month.toString().padLeft(2, '0')}/${local.day.toString().padLeft(2, '0')} ${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}:${local.second.toString().padLeft(2, '0')}';
}

class DailyBarChart extends StatelessWidget {
  final List<JsonObject> days;
  const DailyBarChart({super.key, required this.days});
  @override
  Widget build(BuildContext context) {
    if (days.isEmpty) {
      return const EmptyState(
        '还没有趋势数据',
        '执行记录会汇总成每日统计，开始运行后可查看趋势。',
        icon: Icons.bar_chart_rounded,
      );
    }
    final selected = days.length > 31 ? days.sublist(days.length - 31) : days;
    final values = selected.map((day) {
      final totals = object(day['totals']);
      return (numberValue(
                day['device_seconds'] ??
                    day['duration_seconds'] ??
                    totals['device_seconds'],
              ) ??
              0)
          .toDouble();
    }).toList();
    final highest = math.max(1.0, values.fold<double>(0, math.max));
    return SizedBox(
      height: 190,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          SizedBox(
            width: 42,
            child: Column(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${(highest / 60).ceil()} 分',
                  style: const TextStyle(fontSize: 10, color: Colors.grey),
                ),
                const Text(
                  '0',
                  style: TextStyle(fontSize: 10, color: Colors.grey),
                ),
                const SizedBox(height: 8),
              ],
            ),
          ),
          Expanded(
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: List.generate(selected.length, (index) {
                final day = selected[index];
                final date = textValue(day['date'] ?? day['day'], '');
                return Expanded(
                  child: Tooltip(
                    message: '$date\n${formatDuration(values[index])}',
                    child: Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 3),
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.end,
                        children: [
                          Container(
                            height: values[index] == 0
                                ? 2
                                : 150 * values[index] / highest,
                            width: 24,
                            decoration: BoxDecoration(
                              color: index == selected.length - 1
                                  ? solanaBlue
                                  : solanaBlue.withValues(alpha: .22),
                              borderRadius: const BorderRadius.vertical(
                                top: Radius.circular(5),
                              ),
                            ),
                          ),
                          const SizedBox(height: 10),
                          Text(
                            selected.length <= 14 || index % 5 == 0
                                ? (date.length >= 10 ? date.substring(5) : date)
                                : '',
                            style: const TextStyle(
                              fontSize: 9,
                              color: Colors.grey,
                            ),
                            maxLines: 1,
                          ),
                        ],
                      ),
                    ),
                  ),
                );
              }),
            ),
          ),
        ],
      ),
    );
  }
}
