import 'dart:convert';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:oasx/utils/file_save_stub.dart'
    if (dart.library.io) 'package:oasx/utils/file_save_io.dart';

import 'solana_api.dart';
import 'solana_controller.dart';
import 'solana_widgets.dart';
import 'solana_recovery.dart';
import 'solana_application_preferences.dart';

class SolanaSettings extends StatelessWidget {
  final SolanaController controller;
  final bool dark;
  final bool lowEffects;
  final ValueChanged<bool> onDark;
  final ValueChanged<bool> onLowEffects;
  final VoidCallback onConnectionSettings;
  final VoidCallback onDeployment;
  final VoidCallback onManageProfiles;
  const SolanaSettings({
    super.key,
    required this.controller,
    required this.dark,
    required this.lowEffects,
    required this.onDark,
    required this.onLowEffects,
    required this.onConnectionSettings,
    required this.onDeployment,
    required this.onManageProfiles,
  });

  @override
  Widget build(BuildContext context) => SingleChildScrollView(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Surface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SectionHeading('界面偏好', subtitle: '设置保存在本机'),
              const SizedBox(height: 12),
              SwitchListTile.adaptive(
                contentPadding: EdgeInsets.zero,
                title: const Text('夜间背景', style: TextStyle(fontSize: 13)),
                subtitle: const Text(
                  '调暗背景，保留清晰的浅色内容面板',
                  style: TextStyle(fontSize: 11),
                ),
                value: dark,
                onChanged: onDark,
              ),
              SwitchListTile.adaptive(
                contentPadding: EdgeInsets.zero,
                title: const Text('简化视觉效果', style: TextStyle(fontSize: 13)),
                subtitle: const Text(
                  '使用简化背景，减少渲染效果',
                  style: TextStyle(fontSize: 11),
                ),
                value: lowEffects,
                onChanged: onLowEffects,
              ),
            ],
          ),
        ),
        const SizedBox(height: 18),
        Surface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SectionHeading('后端连接', subtitle: '在当前界面管理连接与部署'),
              const SizedBox(height: 16),
              Row(
                children: [
                  const Icon(Icons.dns_outlined, size: 22, color: solanaBlue),
                  const SizedBox(width: 13),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          controller.api.baseUri.host.isEmpty
                              ? '未设置服务地址'
                              : '${controller.api.baseUri.scheme}://${controller.api.baseUri.host}:${controller.api.baseUri.port}',
                          style: const TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          controller.connected ? '已获取后端状态' : '后端未连接',
                          style: const TextStyle(
                            fontSize: 11,
                            color: Color(0xFF9299AB),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 18),
              Wrap(
                spacing: 10,
                runSpacing: 10,
                children: [
                  OutlinedButton.icon(
                    onPressed: onConnectionSettings,
                    icon: const Icon(Icons.settings_ethernet_rounded, size: 17),
                    label: const Text('连接设置'),
                  ),
                  OutlinedButton.icon(
                    onPressed: onDeployment,
                    icon: const Icon(Icons.terminal_rounded, size: 17),
                    label: const Text('后端部署'),
                  ),
                  OutlinedButton.icon(
                    onPressed: onManageProfiles,
                    icon: const Icon(Icons.folder_copy_outlined, size: 17),
                    label: const Text('配置管理'),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SizedBox(height: 18),
        const SolanaApplicationPreferences(),
        const SizedBox(height: 18),
        RemoteBody(
          remote: controller.storage,
          retry: controller.refreshAll,
          builder: (data) => Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _StorageUsage(data: data),
              const SizedBox(height: 18),
              if (object(data['policy']).isNotEmpty)
                StoragePolicyEditor(
                  controller: controller,
                  policy: object(data['policy']),
                ),
              const SizedBox(height: 18),
              Surface(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    const SectionHeading('历史维护', subtitle: '清理只处理满足保留与恢复条件的数据'),
                    const SizedBox(height: 16),
                    Wrap(
                      spacing: 10,
                      runSpacing: 10,
                      children: [
                        OutlinedButton.icon(
                          onPressed: controller.connected && !controller.busy
                              ? controller.cleanup
                              : null,
                          icon: const Icon(
                            Icons.cleaning_services_outlined,
                            size: 17,
                          ),
                          label: const Text('清理可清理历史'),
                        ),
                        OutlinedButton.icon(
                          onPressed: () =>
                              showSolanaRecovery(context, controller),
                          icon: const Icon(Icons.fact_check_outlined, size: 17),
                          label: const Text('异常恢复与核验'),
                        ),
                        OutlinedButton.icon(
                          onPressed: controller.audit.data == null
                              ? null
                              : () => _export(context),
                          icon: const Icon(
                            Icons.file_download_outlined,
                            size: 18,
                          ),
                          label: const Text('导出当前已加载记录'),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    const Text(
                      '导出包含当前筛选的汇总与已加载明细；未加载分页和已清理记录不会包含在文件中。',
                      style: TextStyle(fontSize: 11, color: Color(0xFF9299AB)),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    ),
  );

  Future<void> _export(BuildContext context) async {
    try {
      final payload = {
        'schema_version': 1,
        'exported_at': DateTime.now().toUtc().toIso8601String(),
        'scope': 'loaded_records_only',
        'filter': controller.filter,
        'statistics': controller.statistics.data,
        'runs': controller.runs.data,
        'audit': controller.audit.data,
      };
      final bytes = Uint8List.fromList(
        utf8.encode(const JsonEncoder.withIndent('  ').convert(payload)),
      );
      final path = await FilePicker.platform.saveFile(
        dialogTitle: '导出当前已加载记录',
        fileName: 'oas-records-${SolanaController.date(DateTime.now())}.json',
        type: FileType.custom,
        allowedExtensions: ['json'],
        bytes: bytes,
      );
      if (path != null && shouldWritePickedSavePath()) {
        await saveBytesToPath(path, bytes);
      }
      if (path != null && context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('当前已加载记录已导出')));
      }
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(const SnackBar(content: Text('导出失败，请检查保存位置是否可写')));
      }
    }
  }
}

class _StorageUsage extends StatelessWidget {
  final JsonObject data;
  const _StorageUsage({required this.data});
  @override
  Widget build(BuildContext context) {
    final usage = object(data['usage']);
    final categories = object(data['bytes'] ?? usage['bytes']);
    final total = numberValue(data['total_bytes'] ?? usage['total_bytes']);
    final policy = object(data['policy']);
    final normal = numberValue(policy['normal_budget_bytes']);
    final reserve = numberValue(policy['temporary_reserve_bytes']);
    final limit = normal != null && reserve != null ? normal + reserve : null;
    final external = object(data['external_usage']);
    final externalBytes = object(external['bytes']);
    return Surface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SectionHeading(
            '本地数据占用',
            subtitle: '运行记录目录的占用；日志、截图与配置备份在下方单独统计',
            trailing: StateBadge(
              stateLabel(data['state']),
              color: stateColor(data['state']),
            ),
          ),
          const SizedBox(height: 20),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(
                formatBytes(total),
                style: Theme.of(context).textTheme.headlineMedium,
              ),
              const SizedBox(width: 10),
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text(
                  '/ ${formatBytes(limit)} 保护阈值',
                  style: const TextStyle(
                    fontSize: 12,
                    color: Color(0xFF9299AB),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          if (total != null && limit != null && limit > 0)
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: LinearProgressIndicator(
                value: (total / limit).clamp(0.0, 1.0),
                minHeight: 8,
                backgroundColor: solanaBlue.withValues(alpha: .08),
                color: total > (normal ?? limit) ? solanaAmber : solanaBlue,
              ),
            ),
          const SizedBox(height: 16),
          Wrap(
            spacing: 22,
            runSpacing: 10,
            children: [
              for (final item in [
                ('events', '事件明细'),
                ('summaries', '日汇总'),
                ('checkpoints', '恢复状态'),
                ('meta', '元数据'),
                ('temp', '临时文件'),
              ])
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      item.$2,
                      style: const TextStyle(
                        fontSize: 11,
                        color: Color(0xFF9299AB),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(
                      formatBytes(categories[item.$1]),
                      style: const TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
            ],
          ),
          const SizedBox(height: 18),
          Text(
            '可查明细自 ${textValue(data['detail_from'] ?? data['details_available_from'], '暂无')} · 可查汇总自 ${textValue(data['summary_from'], '暂无')} · 峰值 ${formatBytes(data['peak_bytes'])}',
            style: const TextStyle(fontSize: 11, color: Color(0xFF9299AB)),
          ),
          if (data['last_error'] != null) ...[
            const SizedBox(height: 14),
            Notice(textValue(data['last_error']), error: true),
          ],
          if (data['dispatch_allowed'] == false ||
              data['needs_reconciliation'] == true) ...[
            const SizedBox(height: 14),
            const Notice('存储或恢复状态需要核验，后端已限制新任务派发。'),
          ],
          const Divider(height: 28),
          Text(
            '原有文件占用 · ${external.isEmpty
                ? '未统计'
                : external['complete'] == true
                ? '已扫描'
                : '已扫描部分'}',
            style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 18,
            runSpacing: 8,
            children: [
              for (final entry in const [
                ('text_logs', '文本日志'),
                ('screenshots', '截图'),
                ('config_backups', '配置备份'),
                ('other_log_files', '其他日志文件'),
              ])
                Text(
                  '${entry.$2}  ${externalBytes.containsKey(entry.$1) ? formatBytes(externalBytes[entry.$1]) : '未统计'}',
                  style: const TextStyle(fontSize: 11),
                ),
            ],
          ),
          const SizedBox(height: 8),
          const Text(
            '这些文件不计入运行记录容量预算，本页历史清理不会删除它们。',
            style: TextStyle(fontSize: 11, color: Color(0xFF9299AB)),
          ),
          if (external['observed_at'] != null)
            Text(
              '扫描时间 ${external['observed_at']}',
              style: const TextStyle(fontSize: 10, color: Color(0xFF9299AB)),
            ),
        ],
      ),
    );
  }
}

class StoragePolicyEditor extends StatefulWidget {
  final SolanaController controller;
  final JsonObject policy;
  const StoragePolicyEditor({
    super.key,
    required this.controller,
    required this.policy,
  });
  @override
  State<StoragePolicyEditor> createState() => _StoragePolicyEditorState();
}

class _StoragePolicyEditorState extends State<StoragePolicyEditor> {
  final _form = GlobalKey<FormState>();
  late String _mode;
  late final TextEditingController _events;
  late final TextEditingController _summaries;
  late final TextEditingController _budget;
  late final TextEditingController _reserve;
  late final TextEditingController _timezone;
  @override
  void initState() {
    super.initState();
    _mode = textValue(widget.policy['mode'], 'standard');
    _events = TextEditingController(
      text: textValue(widget.policy['event_retention_days'], ''),
    );
    _summaries = TextEditingController(
      text: textValue(widget.policy['summary_retention_days'], ''),
    );
    _budget = TextEditingController(
      text: _mib(widget.policy['normal_budget_bytes']),
    );
    _reserve = TextEditingController(
      text: _mib(widget.policy['temporary_reserve_bytes']),
    );
    _timezone = TextEditingController(
      text: textValue(widget.policy['timezone'], ''),
    );
  }

  String _mib(Object? value) => numberValue(value) == null
      ? ''
      : '${(numberValue(value)! / 1048576).round()}';
  @override
  void dispose() {
    for (final controller in [
      _events,
      _summaries,
      _budget,
      _reserve,
      _timezone,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Surface(
    child: Form(
      key: _form,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SectionHeading('历史保留策略', subtitle: '容量优先于天数；恢复所需状态独立保留'),
          const SizedBox(height: 18),
          DropdownButtonFormField<String>(
            initialValue: _mode,
            style: solanaFormTextStyle.copyWith(
              color: Theme.of(context).colorScheme.onSurface,
            ),
            dropdownColor: Theme.of(context).canvasColor,
            borderRadius: BorderRadius.circular(10),
            isExpanded: true,
            iconSize: 18,
            decoration: const InputDecoration(labelText: '记录模式'),
            selectedItemBuilder: (context) => [
              for (final option in _modeLabels.entries)
                Text(
                  option.value,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
            ],
            items: [
              for (final option in _modeLabels.entries)
                DropdownMenuItem(
                  value: option.key,
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          option.value,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      const SizedBox(width: 12),
                      if (_mode == option.key)
                        const Icon(
                          Icons.check_rounded,
                          size: 17,
                          color: solanaBlue,
                        )
                      else
                        const SizedBox(width: 17),
                    ],
                  ),
                ),
            ],
            onChanged: (value) {
              if (value != null) setState(() => _mode = value);
            },
          ),
          const SizedBox(height: 18),
          Wrap(
            spacing: 14,
            runSpacing: 16,
            children: [
              _field(_events, '事件明细保留 / 天', minimum: 0),
              _field(_summaries, '每日汇总保留 / 天', minimum: 0),
              _field(_budget, '正常预算 / MiB', minimum: 8),
              _field(_reserve, '临时空间 / MiB', minimum: 4),
              SizedBox(
                width: 220,
                child: TextFormField(
                  controller: _timezone,
                  style: solanaFormTextStyle,
                  decoration: const InputDecoration(labelText: '报表时区'),
                  validator: (value) =>
                      value == null || value.trim().isEmpty ? '请输入有效时区' : null,
                ),
              ),
            ],
          ),
          const SizedBox(height: 18),
          const Notice('更换报表时区不会重算已经清理明细的历史日期。关闭历史后，新的普通操作将不再可追溯。'),
          const SizedBox(height: 18),
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.icon(
              onPressed: widget.controller.connected && !widget.controller.busy
                  ? () async {
                      if (!_form.currentState!.validate()) return;
                      await widget.controller.saveStorage({
                        'mode': _mode,
                        'event_retention_days': int.parse(_events.text),
                        'summary_retention_days': int.parse(_summaries.text),
                        'normal_budget_bytes':
                            int.parse(_budget.text) * 1048576,
                        'temporary_reserve_bytes':
                            int.parse(_reserve.text) * 1048576,
                        'timezone': _timezone.text.trim(),
                      });
                    }
                  : null,
              icon: const Icon(Icons.save_outlined, size: 17),
              label: const Text('保存存储设置'),
            ),
          ),
        ],
      ),
    ),
  );
  Widget _field(
    TextEditingController controller,
    String label, {
    required int minimum,
  }) => SizedBox(
    width: 220,
    child: TextFormField(
      controller: controller,
      style: solanaFormTextStyle,
      keyboardType: TextInputType.number,
      decoration: InputDecoration(labelText: label),
      validator: (value) {
        final n = int.tryParse(value ?? '');
        return n == null || n < minimum ? '请输入不小于 $minimum 的整数' : null;
      },
    ),
  );

  static const _modeLabels = {
    'standard': '标准 · 明细与汇总',
    'summary_only': '仅汇总 · 不保留长期逐条审计',
    'off': '关闭普通历史 · 保留必要恢复状态',
  };
}

class SchedulerPolicyEditor extends StatefulWidget {
  final SolanaController controller;
  final JsonObject policy;
  final List<JsonObject> tasks;
  const SchedulerPolicyEditor({
    super.key,
    required this.controller,
    required this.policy,
    required this.tasks,
  });
  @override
  State<SchedulerPolicyEditor> createState() => _SchedulerPolicyEditorState();
}

class _SchedulerPolicyEditorState extends State<SchedulerPolicyEditor> {
  late String _mode;
  late final TextEditingController _batch;
  final Map<String, TextEditingController> _weights = {};
  bool _advanced = false;
  String? _error;
  bool _hasChangesComparedTo(JsonObject policy) {
    if (_mode != textValue(policy['mode'], 'legacy') ||
        double.tryParse(_batch.text) !=
            (numberValue(policy['batch_seconds']) ?? 120)) {
      return true;
    }
    final currentWeights = object(policy['weights']);
    return _weights.entries.any(
      (entry) =>
          double.tryParse(entry.value.text) !=
          (numberValue(currentWeights[entry.key]) ?? 1),
    );
  }

  bool get _hasChanges => _hasChangesComparedTo(widget.policy);
  @override
  void initState() {
    super.initState();
    _mode = textValue(widget.policy['mode'], 'legacy');
    _batch = TextEditingController(
      text: textValue(widget.policy['batch_seconds'], '120'),
    );
    final existing = object(widget.policy['weights']);
    final ids = {
      ...existing.keys,
      ...widget.tasks
          .map((task) => textValue(task['task_id'] ?? task['name'], ''))
          .where((id) => id.isNotEmpty),
    };
    for (final id in ids) {
      _weights[id] = TextEditingController(text: textValue(existing[id], '1'));
    }
  }

  @override
  void dispose() {
    _batch.dispose();
    for (final input in _weights.values) {
      input.dispose();
    }
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant SchedulerPolicyEditor oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Refresh the confirmed policy only when the user has no local draft.
    if (!_hasChangesComparedTo(oldWidget.policy)) {
      _mode = textValue(widget.policy['mode'], 'legacy');
      _batch.text = textValue(widget.policy['batch_seconds'], '120');
      final currentWeights = object(widget.policy['weights']);
      for (final entry in _weights.entries) {
        entry.value.text = textValue(currentWeights[entry.key], '1');
      }
      for (final entry in currentWeights.entries) {
        _weights.putIfAbsent(
          entry.key,
          () => TextEditingController(text: textValue(entry.value, '1')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) => Surface(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SectionHeading(
          '调度策略',
          subtitle: '修改后需点击应用；正在执行的任务不会被强制打断',
          trailing: StateBadge(
            '当前策略：${_policyName(textValue(widget.policy['mode'], 'legacy'))}',
            dot: false,
          ),
        ),
        const SizedBox(height: 12),
        for (final mode in ['legacy', 'eevdf_shadow', 'eevdf'])
          _modeOption(mode),
        if (_hasChanges) ...[
          const SizedBox(height: 4),
          Text(
            '未应用修改：${_policyName(_mode)}。点击“应用调度策略”后才会保存。',
            key: const ValueKey('scheduler-policy-draft'),
            style: const TextStyle(fontSize: 11, color: solanaAmber),
          ),
        ],
        const SizedBox(height: 10),
        if (_advanced) ...[
          const SizedBox(height: 8),
          SizedBox(
            width: 240,
            child: TextField(
              controller: _batch,
              style: solanaFormTextStyle,
              onChanged: (_) => setState(() {}),
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: '目标批次 / 秒',
                helperText: '达到目标后请求让出，不在战斗中强制中断',
              ),
            ),
          ),
          const SizedBox(height: 18),
          if (_weights.isEmpty)
            const Text(
              '有可调度任务后，可在这里设置任务执行份额。',
              style: TextStyle(fontSize: 11, color: Color(0xFF9299AB)),
            )
          else
            Wrap(
              spacing: 14,
              runSpacing: 14,
              children: _weights.entries
                  .map(
                    (entry) => SizedBox(
                      width: 210,
                      child: TextField(
                        controller: entry.value,
                        style: solanaFormTextStyle,
                        onChanged: (_) => setState(() {}),
                        keyboardType: TextInputType.number,
                        decoration: InputDecoration(
                          labelText: '${taskLabel(entry.key)} · 执行份额',
                        ),
                      ),
                    ),
                  )
                  .toList(),
            ),
          const SizedBox(height: 16),
        ],
        if (_error != null) ...[
          Notice(_error!, error: true),
          const SizedBox(height: 12),
        ],
        Row(
          children: [
            TextButton.icon(
              onPressed: () => setState(() => _advanced = !_advanced),
              icon: Icon(
                _advanced ? Icons.expand_less : Icons.expand_more,
                size: 17,
              ),
              label: const Text('高级设置', style: TextStyle(fontSize: 12)),
            ),
            const Spacer(),
            FilledButton.icon(
              onPressed:
                  widget.controller.connected &&
                      !widget.controller.busy &&
                      widget.policy.isNotEmpty
                  ? _save
                  : null,
              icon: const Icon(Icons.check_rounded, size: 17),
              label: const Text('应用调度策略'),
            ),
          ],
        ),
      ],
    ),
  );

  Widget _modeOption(String mode) => Padding(
    padding: const EdgeInsets.only(bottom: 8),
    child: LayoutBuilder(
      builder: (context, bounds) {
        final choice = ChoiceChip(
          label: Text(_policyName(mode), style: const TextStyle(fontSize: 12)),
          selected: _mode == mode,
          onSelected: widget.controller.busy
              ? null
              : (_) => setState(() => _mode = mode),
        );
        final help = Text(
          switch (mode) {
            'eevdf_shadow' => '仍按原有排序执行，不按新权重改序；只记录原策略与公平策略各会选择哪个任务。',
            'eevdf' => '实际按权重分配设备时间；仅在安全边界轮换，未适配的任务仍整项执行。',
            _ => '沿用现有配置的任务排序规则，不按新权重改序；运行记录与统计照常保存。',
          },
          key: ValueKey('scheduler-mode-help-$mode'),
          style: const TextStyle(
            fontSize: 11,
            height: 1.55,
            color: Color(0xFF77708B),
          ),
        );
        return bounds.maxWidth < 440
            ? Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [choice, const SizedBox(height: 4), help],
              )
            : Row(
                crossAxisAlignment: CrossAxisAlignment.center,
                children: [
                  SizedBox(
                    width: 154,
                    child: Align(
                      alignment: Alignment.centerLeft,
                      child: choice,
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(child: help),
                ],
              );
      },
    ),
  );

  Future<void> _save() async {
    final batch = double.tryParse(_batch.text);
    final weights = <String, double>{};
    if (batch == null || !batch.isFinite || batch < 1 || batch > 86400) {
      setState(() => _error = '目标批次应为 1 至 86400 秒。');
      return;
    }
    for (final entry in _weights.entries) {
      final value = double.tryParse(entry.value.text);
      if (value == null || !value.isFinite || value < 0.01 || value > 100) {
        setState(() => _error = '执行份额须在 0.01 至 100 之间。');
        return;
      }
      weights[entry.key] = value;
    }
    setState(() => _error = null);
    await widget.controller.saveScheduler({
      'mode': _mode,
      'batch_seconds': batch,
      'weights': weights,
    });
  }

  String _policyName(String value) => switch (value) {
    'eevdf_shadow' => '公平调度 · 试算',
    'eevdf' => '公平调度',
    _ => '原有策略',
  };
}
