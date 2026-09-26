part of 'solana_shell.dart';

extension _SolanaPanels on _SolanaShellState {
  Widget _metrics(JsonObject totals) {
    final success = numberValue(totals['succeeded'] ?? totals['success_count']);
    final failed = numberValue(totals['failed'] ?? totals['failure_count']);
    final denominator = success != null && failed != null
        ? success + failed
        : null;
    final ratio = denominator != null && denominator > 0
        ? '${(100 * success! / denominator).toStringAsFixed(1)}%'
        : denominator == 0
        ? '暂无数据'
        : '—';
    final metrics = [
      (
        '开始次数',
        textValue(totals['started'] ?? totals['started_count']),
        '续跑不重复计数',
      ),
      ('成功完成', textValue(success), failed == null ? '等待执行结果' : '失败 $failed 次'),
      ('任务成功率', ratio, denominator == null ? '样本量未知' : '$denominator 个成功或失败样本'),
      (
        '设备占用',
        formatDuration(totals['device_seconds'] ?? totals['duration_seconds']),
        '仅累计实际执行片段',
      ),
      ('取消次数', textValue(totals['cancelled']), '不计入成功率分母'),
      (
        '异常退出',
        textValue(totals['crashed']),
        '中断 ${textValue(totals['interrupted'])} 次',
      ),
    ];
    return Surface(
      child: Column(
        children: [
          for (var row = 0; row < 2; row++)
            Padding(
              padding: EdgeInsets.only(top: row == 0 ? 0 : 17),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (var column = 0; column < 3; column++)
                    Expanded(
                      child: Padding(
                        padding: EdgeInsets.only(right: column == 2 ? 0 : 12),
                        child: Builder(
                          builder: (context) {
                            final metric = metrics[row * 3 + column];
                            return Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  metric.$1,
                                  style: const TextStyle(
                                    fontSize: 11,
                                    color: Color(0xFF8F829C),
                                  ),
                                ),
                                const SizedBox(height: 5),
                                Tooltip(
                                  message: metric.$2,
                                  child: Text(
                                    metric.$2,
                                    style: const TextStyle(
                                      fontSize: 18,
                                      fontWeight: FontWeight.w600,
                                    ),
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                ),
                                const SizedBox(height: 3),
                                Tooltip(
                                  message: metric.$3,
                                  child: Text(
                                    metric.$3,
                                    style: const TextStyle(
                                      fontSize: 10,
                                      color: Color(0xFF8F829C),
                                    ),
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                ),
                              ],
                            );
                          },
                        ),
                      ),
                    ),
                ],
              ),
            ),
        ],
      ),
    );
  }

  Widget _taskRow(
    JsonObject task, {
    bool detailed = false,
    String? fallbackState,
  }) {
    final state =
        task['state'] ??
        task['status'] ??
        fallbackState ??
        (task['blocked_reason'] == null ? 'queued' : 'waiting');
    final profile = c.profiles.firstWhere(
      (profile) => profile['id'] == task['profile_id'],
      orElse: () => <String, dynamic>{},
    );
    final deviceName = textValue(object(profile['device'])['name'], '设备已连接');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: solanaBlue.withValues(alpha: .07),
              borderRadius: BorderRadius.circular(10),
            ),
            child: const Icon(
              Icons.task_alt_rounded,
              size: 18,
              color: solanaBlue,
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  taskLabel(
                    task['task_name'] ??
                        task['task'] ??
                        task['task_id'] ??
                        task['name'],
                  ),
                  style: const TextStyle(
                    fontWeight: FontWeight.w600,
                    fontSize: 12,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  task['state'] == 'running'
                      ? '$deviceName · ${formatDuration(task['execution_seconds'] ?? task['duration_seconds'])}'
                      : reasonLabel(
                          textValue(
                            task['blocked_reason'] ??
                                task['reason'] ??
                                task['next_run'],
                            '等待调度器确认',
                          ),
                        ),
                  style: const TextStyle(
                    fontSize: 11,
                    color: Color(0xFF9299AB),
                  ),
                ),
                if (detailed && task['estimated_seconds'] != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Text(
                      '预计批次 ${formatDuration(task['estimated_seconds'])}',
                      style: const TextStyle(
                        fontSize: 10,
                        color: Color(0xFF9299AB),
                      ),
                    ),
                  ),
                if (detailed &&
                    (task['resumable'] == false ||
                        task['cooperative'] == false))
                  const Padding(
                    padding: EdgeInsets.only(top: 5),
                    child: Text(
                      '此任务会完整执行后再切换',
                      style: TextStyle(fontSize: 10, color: solanaAmber),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          StateBadge(stateLabel(state), color: stateColor(state)),
        ],
      ),
    );
  }

  Widget _scheduler(BuildContext context) => SingleChildScrollView(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        RemoteBody(
          remote: c.scheduler,
          retry: c.refreshAll,
          builder: (data) => Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SchedulerPolicyEditor(
                key: const ValueKey('scheduler-policy'),
                controller: c,
                policy: object(data['policy']),
                tasks: [...objects(data['ready']), ...objects(data['waiting'])],
              ),
              const SizedBox(height: 20),
              for (final entry in [
                ('running', '正在执行', Icons.play_circle_outline),
                ('ready', '可执行队列', Icons.format_list_bulleted_rounded),
                ('waiting', '等待条件', Icons.schedule_rounded),
              ]) ...[
                Surface(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      SectionHeading(
                        entry.$2,
                        trailing: StateBadge(
                          '${objects(data[entry.$1]).length} 项',
                          dot: false,
                        ),
                      ),
                      const SizedBox(height: 10),
                      if (objects(data[entry.$1]).isEmpty)
                        EmptyState(
                          '暂无${entry.$2}任务',
                          '满足条件后，任务将由后端安排执行。',
                          icon: entry.$3,
                        )
                      else
                        ...(entry.$1 == 'waiting'
                                ? waitingByNextRun(data[entry.$1])
                                : objects(data[entry.$1]))
                            .map(
                              (item) => _taskRow(
                                item,
                                detailed: true,
                                fallbackState: entry.$1 == 'ready'
                                    ? 'queued'
                                    : entry.$1,
                              ),
                            ),
                    ],
                  ),
                ),
                const SizedBox(height: 18),
              ],
              Surface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const SectionHeading('最近调度决策', subtitle: '选中原因来自后端记录'),
                    const SizedBox(height: 10),
                    if (objects(data['decisions']).isEmpty)
                      const EmptyState(
                        '还没有调度决策',
                        '启动计划后可以在这里查看任务选中原因。',
                        icon: Icons.account_tree_outlined,
                      )
                    else
                      ...objects(
                        data['decisions'],
                      ).take(10).map((item) => _eventRow(item)),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    ),
  );

  Widget _historyFilters(BuildContext context) => Padding(
    padding: const EdgeInsets.only(bottom: 18),
    child: Wrap(
      spacing: 10,
      runSpacing: 10,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        OutlinedButton.icon(
          onPressed: () async {
            final range = await showDateRangePicker(
              context: context,
              firstDate: DateTime(2020),
              lastDate: DateTime.now(),
              initialDateRange: DateTimeRange(start: c.from, end: c.to),
              helpText: '选择统计日期',
              saveText: '应用',
            );
            if (range != null) await c.setRange(range.start, range.end);
          },
          icon: const Icon(Icons.calendar_today_outlined, size: 15),
          label: Text(
            '${SolanaController.date(c.from)} — ${SolanaController.date(c.to)}',
            style: const TextStyle(fontSize: 12),
          ),
        ),
        SizedBox(
          width: 210,
          child: TextFormField(
            initialValue: c.taskFilter,
            style: solanaFormTextStyle,
            decoration: const InputDecoration(
              hintText: '按任务标识筛选',
              prefixIcon: Icon(Icons.search_rounded, size: 18),
            ),
            onFieldSubmitted: c.setTaskFilter,
          ),
        ),
        if (c.taskFilter.isNotEmpty)
          TextButton(
            onPressed: () => c.setTaskFilter(''),
            child: const Text('清除筛选'),
          ),
      ],
    ),
  );

  Widget _statistics(BuildContext context) => SingleChildScrollView(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _historyFilters(context),
        RemoteBody(
          remote: c.statistics,
          retry: c.refreshHistory,
          builder: (data) => Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _metrics(object(data['totals'])),
              const SizedBox(height: 20),
              Surface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    SectionHeading(
                      '每日设备占用',
                      subtitle:
                          '报表时区 ${textValue(data['timezone'], '未知')} · 悬停查看每日耗时',
                    ),
                    const SizedBox(height: 28),
                    DailyBarChart(days: objects(data['days'])),
                  ],
                ),
              ),
              const SizedBox(height: 18),
              Surface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const SectionHeading(
                      '执行效率与异常',
                      subtitle: '基于已确认的片段耗时与终态记录',
                    ),
                    const SizedBox(height: 18),
                    Wrap(
                      spacing: 30,
                      runSpacing: 14,
                      children: [
                        _summaryValue(
                          '平均执行耗时',
                          formatDuration(
                            object(data['totals'])['average_execution_seconds'],
                          ),
                          '成功样本 ${textValue(object(data['totals'])['success_samples'])}',
                        ),
                        _summaryValue(
                          '累计排队等待',
                          formatDuration(
                            object(data['totals'])['queue_wait_seconds'],
                          ),
                          '不包含未来计划时间与冷却',
                        ),
                      ],
                    ),
                    const SizedBox(height: 22),
                    const Text(
                      '失败原因',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 12),
                    if (object(
                      object(data['totals'])['failure_categories'],
                    ).isEmpty)
                      const Text(
                        '所选范围暂无已分类的失败记录',
                        style: TextStyle(
                          fontSize: 11,
                          color: Color(0xFF9299AB),
                        ),
                      )
                    else
                      Wrap(
                        spacing: 12,
                        runSpacing: 10,
                        children:
                            object(object(data['totals'])['failure_categories'])
                                .entries
                                .map(
                                  (entry) => StateBadge(
                                    '${reasonLabel(entry.key)} · ${entry.value} 次',
                                    color: solanaRed,
                                    dot: false,
                                  ),
                                )
                                .toList(),
                      ),
                  ],
                ),
              ),
              const SizedBox(height: 18),
              Surface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const SectionHeading(
                      '其他执行结果',
                      subtitle: '以下结果单独统计，不计入任务成功率分母',
                    ),
                    const SizedBox(height: 18),
                    Wrap(
                      spacing: 26,
                      runSpacing: 14,
                      children: [
                        for (final item in [
                          ('cancelled', '取消'),
                          ('interrupted', '中断'),
                          ('crashed', '异常退出'),
                        ])
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Text(
                                item.$2,
                                style: const TextStyle(
                                  fontSize: 12,
                                  color: Color(0xFF9299AB),
                                ),
                              ),
                              const SizedBox(width: 10),
                              Text(
                                textValue(object(data['totals'])[item.$1]),
                                style: const TextStyle(
                                  fontSize: 20,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                            ],
                          ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 20),
        Surface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SectionHeading('运行记录', subtitle: '让出和恢复属于同一次逻辑运行'),
              const SizedBox(height: 14),
              RemoteBody(
                remote: c.runs,
                retry: c.refreshHistory,
                builder: (data) {
                  final items = objects(data['items']);
                  if (items.isEmpty) {
                    return const EmptyState(
                      '所选范围没有运行记录',
                      '可以调整日期或任务筛选。已清理的明细无法展开。',
                      icon: Icons.receipt_long_outlined,
                    );
                  }
                  return Column(
                    children: [
                      for (final item in items) _runRow(context, item),
                      if (data['next_cursor'] != null)
                        Padding(
                          padding: const EdgeInsets.only(top: 14),
                          child: OutlinedButton(
                            onPressed: c.runs.loading
                                ? null
                                : () => c.nextPage(c.runs, '/api/v2/runs'),
                            child: Text(c.runs.loading ? '加载中…' : '加载更多'),
                          ),
                        ),
                    ],
                  );
                },
              ),
            ],
          ),
        ),
      ],
    ),
  );

  Widget _runRow(BuildContext context, JsonObject run) => ListTile(
    contentPadding: EdgeInsets.zero,
    leading: Icon(
      Icons.receipt_long_outlined,
      size: 20,
      color: stateColor(run['result'] ?? run['state']),
    ),
    title: Text(
      taskLabel(run['task_name'] ?? run['task_id'] ?? run['task']),
      style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
    ),
    subtitle: Text(
      '${displayTime(run['started_at'] ?? run['occurred_at'])} · ${formatDuration(run['device_seconds'] ?? run['duration_seconds'])}',
      style: const TextStyle(fontSize: 11),
    ),
    trailing: StateBadge(
      stateLabel(run['result'] ?? run['state']),
      color: stateColor(run['result'] ?? run['state']),
    ),
    onTap: () => _details(context, '运行详情', run),
  );

  Widget _summaryValue(String title, String value, String detail) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        title,
        style: const TextStyle(fontSize: 11, color: Color(0xFF9299AB)),
      ),
      const SizedBox(height: 6),
      Text(
        value,
        style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
      ),
      const SizedBox(height: 4),
      Text(
        detail,
        style: const TextStyle(fontSize: 10, color: Color(0xFF9299AB)),
      ),
    ],
  );

  Widget _audit(BuildContext context) => SingleChildScrollView(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _historyFilters(context),
        Surface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SectionHeading('操作时间线', subtitle: '配置变更、控制结果、调度与恢复记录'),
              const SizedBox(height: 18),
              RemoteBody(
                remote: c.audit,
                retry: c.refreshHistory,
                builder: (data) {
                  final items = objects(data['items']);
                  if (items.isEmpty) {
                    return const EmptyState(
                      '所选范围没有审计记录',
                      '只显示后端保存的关键事件。汇总模式或关闭历史后，不提供长期逐条审计。',
                      icon: Icons.manage_search_rounded,
                    );
                  }
                  return Column(
                    children: [
                      for (final item in items)
                        InkWell(
                          borderRadius: BorderRadius.circular(10),
                          onTap: () => _details(context, '事件详情', item),
                          child: _eventRow(item),
                        ),
                      if (data['next_cursor'] != null)
                        Padding(
                          padding: const EdgeInsets.only(top: 18),
                          child: OutlinedButton(
                            onPressed: c.audit.loading
                                ? null
                                : () => c.nextPage(c.audit, '/api/v2/audit'),
                            child: Text(c.audit.loading ? '加载中…' : '加载更多'),
                          ),
                        ),
                    ],
                  );
                },
              ),
            ],
          ),
        ),
      ],
    ),
  );

  Widget _eventRow(JsonObject event, {bool compact = false}) {
    final payload = object(event['payload']);
    final label = eventLabel(textValue(event['type'], 'event'));
    final result = payload['result'] ?? event['result'];
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 11, horizontal: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 5),
            child: Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                color: result == null
                    ? solanaBlue.withValues(alpha: .55)
                    : stateColor(result),
                shape: BoxShape.circle,
              ),
            ),
          ),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label,
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  _eventSummary(event),
                  maxLines: compact ? 1 : 3,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 11,
                    color: Color(0xFF9299AB),
                  ),
                ),
                if (!compact && event['request_id'] != null)
                  Text(
                    '请求 ${event['request_id']}',
                    style: const TextStyle(
                      fontSize: 10,
                      color: Color(0xFF9299AB),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          Text(
            displayTime(event['occurred_at'] ?? event['recorded_at']),
            style: const TextStyle(fontSize: 10, color: Color(0xFF9299AB)),
          ),
        ],
      ),
    );
  }

  String? _recordProfileName(JsonObject record) {
    final payload = object(record['payload']);
    final profileId = record['profile_id'] ?? payload['profile_id'];
    final profiles = c.profiles.where((profile) => profile['id'] == profileId);
    if (profiles.isNotEmpty) return textValue(profiles.first['name'], '当前配置');
    final name = record['profile_name'] ?? payload['name'];
    return name == null ? null : textValue(name);
  }

  String _eventSummary(JsonObject event) {
    final payload = object(event['payload']);
    final task = event['task_name'] ?? event['task_id'] ?? payload['task'];
    final reason = payload['reason'] ?? payload['message'];
    final profile = _recordProfileName(event);
    final parts = [
      if (profile != null) profile,
      if (task != null) taskLabel(task),
      if (reason != null) reasonLabel(textValue(reason)),
    ];
    return parts.isEmpty ? '系统事件' : parts.join(' · ');
  }

  Widget _recordSummary(JsonObject record) {
    final payload = object(record['payload']);
    final task =
        record['task_name'] ??
        record['task_id'] ??
        record['task'] ??
        payload['task'];
    final profile = _recordProfileName(record);
    final status =
        record['result'] ??
        record['state'] ??
        payload['result'] ??
        payload['outcome'] ??
        payload['status'];
    final time =
        record['occurred_at'] ?? record['started_at'] ?? record['recorded_at'];
    final reason = payload['reason'] ?? payload['message'] ?? record['reason'];
    final rows = <(String, String)>[
      if (record['type'] != null) ('事件', eventLabel(textValue(record['type']))),
      if (task != null) ('任务', taskLabel(task)),
      if (profile != null) ('配置', profile),
      if (status != null) ('状态', stateLabel(status)),
      if (time != null) ('时间', displayTime(time)),
      if (reason != null) ('原因', reasonLabel(textValue(reason))),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final row in rows)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 5),
            child: Text(
              '${row.$1}：${row.$2}',
              style: const TextStyle(fontSize: 13),
            ),
          ),
        if (rows.isEmpty) const Text('暂无可显示的记录摘要'),
      ],
    );
  }

  Future<void> _details(BuildContext context, String title, JsonObject data) =>
      showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          content: SizedBox(
            width: 620,
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _recordSummary(data),
                  const SizedBox(height: 12),
                  ExpansionTile(
                    title: const Text('原始记录', style: TextStyle(fontSize: 13)),
                    tilePadding: EdgeInsets.zero,
                    children: [
                      SelectableText(
                        const JsonEncoder.withIndent('  ').convert(data),
                        style: const TextStyle(
                          fontFamily: 'monospace',
                          fontSize: 12,
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Clipboard.setData(
                ClipboardData(
                  text: const JsonEncoder.withIndent('  ').convert(data),
                ),
              ),
              child: const Text('复制记录'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('关闭'),
            ),
          ],
        ),
      );
}

String eventLabel(String type) =>
    const {
      'run.created': '创建任务运行',
      'run.started': '开始执行',
      'run.finished': '执行结束',
      'run.progress_observed': '任务进度更新',
      'battle.finished': '战斗结算完成',
      'execution.cycle_started': '开始本轮任务',
      'execution.cycle_finished': '本轮任务结束',
      'segment.started': '开始执行片段',
      'segment.finished': '执行片段结束',
      'run.yielded': '任务安全让出',
      'run.paused': '任务已暂停',
      'run.resumed': '任务续跑',
      'recovery.requested': '请求异常恢复',
      'recovery.resolved': '恢复核验完成',
      'scheduler.selected': '调度选中任务',
      'scheduler.override': '优先调度',
      'scheduler.skipped': '本次暂不执行',
      'control.requested': '收到控制请求',
      'control.completed': '控制操作完成',
      'config.changed': '配置已修改',
      'strategy.changed': '调度策略已变更',
      'storage.degraded': '历史记录受限',
      'storage.recovered': '记录功能已恢复',
    }[type] ??
    '其他事件';
