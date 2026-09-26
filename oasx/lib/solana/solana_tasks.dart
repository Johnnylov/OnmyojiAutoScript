import 'dart:async';

import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/args/index.dart';
import 'package:oasx/modules/home/widgets/task_json_transfer_actions.dart';

import 'solana_api.dart';
import 'solana_controller.dart';
import 'solana_linker.dart';
import 'solana_widgets.dart';

class SolanaTasks extends StatefulWidget {
  final SolanaController controller;
  final bool showCatalog;
  final String? initialTask;
  const SolanaTasks({
    super.key,
    required this.controller,
    this.showCatalog = true,
    this.initialTask,
  });
  @override
  State<SolanaTasks> createState() => SolanaTasksState();
}

class SolanaTasksState extends State<SolanaTasks> {
  Map<String, List<String>> _menu = {};
  String _search = '';
  String? _task;
  String? _error;
  bool _loadingMenu = true;
  bool _loadingForm = false;
  bool _saveBlocked = false;
  SolanaLinkedTaskSession? _session;
  int _generation = 0;
  int _editorKey = 0;
  String? _loadedProfile;
  Timer? _deadlinePoll;
  bool _checkingDeadline = false;
  int _deadlineGeneration = 0;
  ArgsController get args => Get.find<ArgsController>();

  @override
  void initState() {
    super.initState();
    if (!Get.isRegistered<ArgsController>()) Get.put(ArgsController());
    widget.controller.deadlineChanges.addListener(_onDeadlineChange);
    if (widget.showCatalog) _loadMenu();
    if (widget.initialTask != null) _loadForm(widget.initialTask!);
  }

  @override
  void dispose() {
    _deadlinePoll?.cancel();
    widget.controller.deadlineChanges.removeListener(_onDeadlineChange);
    super.dispose();
  }

  void _onDeadlineChange() {
    final change = widget.controller.deadlineChanges.value;
    String normalize(Object? value) =>
        value.toString().replaceAll('_', '').toLowerCase();
    if (change?['profile_id'] == _loadedProfile &&
        normalize(change?['task_id']) == normalize(_task)) {
      unawaited(_showDeadlineDisabled());
    }
  }

  void _armDeadlinePoll() {
    final deadline = args.findArgument('scheduler', 'real_deadline')?.value;
    _deadlinePoll?.cancel();
    if (deadline != null && deadline.toString().trim().isNotEmpty) {
      _deadlinePoll = Timer.periodic(const Duration(seconds: 15), (_) {
        unawaited(_checkDeadlineStatus());
      });
    }
  }

  /// Recover a missed expiry event after reconnecting, only for a form with a
  /// saved cutoff. Ordinary forms add no background configuration requests.
  Future<void> _checkDeadlineStatus() async {
    final session = _session;
    if (!mounted ||
        _checkingDeadline ||
        _loadingForm ||
        _saveBlocked ||
        session == null ||
        !widget.controller.connected ||
        args.isSavingDraft.value) {
      return;
    }
    final deadline = args.findArgument('scheduler', 'real_deadline')?.value;
    if (deadline == null || deadline.toString().trim().isEmpty) return;
    final enabled = args.findArgument('scheduler', 'enable')?.value == true;
    if (!enabled) return;
    _checkingDeadline = true;
    try {
      final latest = await widget.controller.api.get(
        '/api/v2/config/${Uri.encodeComponent(session.sourceId)}/${Uri.encodeComponent(session.task)}/args',
      );
      if (!mounted || !identical(_session, session)) return;
      final fields = objects(object(latest['args'])['scheduler']);
      final cutoff = fields
          .where((field) => field['name'] == 'real_deadline')
          .firstOrNull?['value'];
      final date = DateTime.tryParse(cutoff?.toString() ?? '');
      if (date != null &&
          !date.isAfter(DateTime.now()) &&
          fields.any(
            (field) => field['name'] == 'enable' && field['value'] == false,
          )) {
        await _showDeadlineDisabled();
      }
    } catch (_) {
      // Connection errors retain the current form and its draft.
    } finally {
      _checkingDeadline = false;
    }
  }

  Future<void> _showDeadlineDisabled() async {
    if (!mounted || _task == null || _loadingForm) return;
    _deadlineGeneration++;
    if (args.hasDraftChanges || args.isSavingDraft.value) {
      setState(() {
        _saveBlocked = true;
        _error = '该任务已到截止时间，后端已自动取消启用。当前未保存的修改已保留，请重新加载核对后再保存。';
      });
      return;
    }
    final task = _task!;
    final profile = _loadedProfile;
    await _loadForm(task);
    if (mounted && profile == _loadedProfile && task == _task) {
      setState(() => _error = '该任务已到截止时间，已自动取消启用。');
    }
  }

  @override
  void didUpdateWidget(covariant SolanaTasks oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.initialTask != widget.initialTask &&
        widget.initialTask != null) {
      _loadForm(widget.initialTask!);
      return;
    }
    if (_loadedProfile != widget.controller.selectedProfile && _task != null) {
      _loadForm(_task!);
    }
  }

  Future<void> _loadMenu() async {
    setState(() => _loadingMenu = true);
    try {
      final response = await widget.controller.api.get('/script_menu');
      final menu = response.map(
        (key, value) => MapEntry(
          key,
          value is List
              ? value.map((item) => item.toString()).toList()
              : <String>[],
        ),
      );
      if (!mounted) return;
      setState(() {
        _menu = {
          for (final entry in menu.entries)
            if (!['Overview', 'Home', 'Updater', 'Tool'].contains(entry.key))
              entry.key: entry.value.isEmpty ? [entry.key] : entry.value,
        };
        _loadingMenu = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _loadingMenu = false;
          _error = '无法获取任务目录，请检查后端连接后重试。';
        });
      }
    }
  }

  Future<bool> canLeave() async {
    if (args.isSavingDraft.value || _loadingForm) return false;
    if (!args.hasDraftChanges || _task == null) return true;
    final choice = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('任务参数尚未保存'),
        content: const Text('是否保存当前修改后离开？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, 'stay'),
            child: const Text('继续编辑'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, 'discard'),
            child: const Text('放弃修改'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, 'save'),
            child: const Text('保存并继续'),
          ),
        ],
      ),
    );
    if (choice == 'discard') {
      await args.discardDraftChanges();
      return true;
    }
    if (choice == 'save') return args.saveDraftChanges();
    return false;
  }

  Future<void> reloadFromServer() async {
    if (_task != null) await _loadForm(_task!);
  }

  Future<void> _select(String task) async {
    if (_task == task || !await canLeave()) return;
    await _loadForm(task);
  }

  Future<void> _executeTask() async {
    final task = _task;
    final profile = _loadedProfile;
    if (task == null ||
        !await canLeave() ||
        !mounted ||
        profile != widget.controller.selectedProfile ||
        task != _task) {
      return;
    }
    await widget.controller.quickScheduleTask(task);
    if (mounted &&
        !widget.controller.operationFailed &&
        profile == widget.controller.selectedProfile &&
        task == _task) {
      await _loadForm(task);
    }
  }

  Future<void> _loadForm(String task) async {
    final profile = widget.controller.selectedProfile;
    if (profile == null) return;
    final generation = ++_generation;
    setState(() {
      _task = task;
      _loadedProfile = profile;
      _loadingForm = true;
      _error = null;
      _saveBlocked = false;
      _session = null;
    });
    try {
      final sourceName = textValue(widget.controller.profile['name'], '');
      final session = await SolanaLinkedTaskSession.load(
        api: widget.controller.api,
        requestId: SolanaController.requestId,
        sourceId: profile,
        task: task,
        targets: widget.controller.linker.snapshotFor(sourceName),
      );
      if (!mounted || generation != _generation) return;
      _session = session;
      await args.loadGroups(
        config: session.sourceName,
        task: task,
        stagingMode: true,
        scopeScripts: session.scopeNames,
        preloadedGroups: session.sourceArgs,
        saveArgumentOverride: _saveArgument,
      );
      if (!mounted || generation != _generation) return;
      _armDeadlinePoll();
      setState(() {
        _loadingForm = false;
        _editorKey++;
      });
    } catch (error) {
      if (mounted && generation == _generation) {
        setState(() {
          _loadingForm = false;
          _error = error is SolanaApiException
              ? error.message
              : '任务参数加载失败，请重试。';
        });
      }
    }
  }

  Future<bool> _saveArgument(
    String profile,
    String task,
    String group,
    String argument,
    String type,
    dynamic value,
  ) async {
    final session = _session;
    final deadlineGeneration = _deadlineGeneration;
    if (_saveBlocked ||
        !widget.controller.connected ||
        session == null ||
        task != session.task) {
      return false;
    }
    try {
      final result = await session.saveField(group, argument, type, value);
      if (deadlineGeneration != _deadlineGeneration) {
        // An expiry may be delivered while an earlier save response is still
        // in flight. Keep the conflict and stop saving the remaining fields.
        return false;
      }
      _saveBlocked = !result.allSuccess;
      if (result.allSuccess &&
          group == 'scheduler' &&
          argument == 'real_deadline') {
        _armDeadlinePoll();
      }
      if (mounted) {
        setState(
          () => _error = result.allSuccess
              ? null
              : '${result.message}。请重新加载核验，尚未保存的字段保留在表单；未重复提交已成功配置。',
        );
      }
      return result.allSuccess;
    } catch (error) {
      _saveBlocked = true;
      if (mounted) {
        setState(
          () => _error = error is SolanaApiException
              ? '${error.message} 请重新加载核验，尚未保存的字段会保留在当前表单。'
              : '保存结果未确认。请重新加载核验，避免重复提交。',
        );
      }
      return false;
    }
  }

  @override
  Widget build(BuildContext context) {
    if (widget.controller.selectedProfile == null) {
      return const Surface(
        child: EmptyState(
          '先连接并选择一个配置',
          '任务目录和参数将从后端读取。',
          icon: Icons.folder_open_outlined,
        ),
      );
    }
    return LayoutBuilder(
      builder: (context, bounds) {
        final catalog = Surface(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                decoration: const InputDecoration(
                  prefixIcon: Icon(Icons.search_rounded, size: 18),
                  hintText: '搜索任务',
                ),
                onChanged: (value) =>
                    setState(() => _search = value.toLowerCase()),
              ),
              const SizedBox(height: 16),
              Expanded(
                child: _loadingMenu
                    ? const Center(
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : _menu.isEmpty
                    ? EmptyState(
                        '没有任务目录',
                        '后端还未返回可配置任务。',
                        action: TextButton(
                          onPressed: _loadMenu,
                          child: const Text('重新加载'),
                        ),
                      )
                    : ListView(
                        children: [
                          for (final category in _menu.entries) ...[
                            if (category.value.any(
                              (task) =>
                                  task.toLowerCase().contains(_search) ||
                                  taskLabel(
                                    task,
                                  ).toLowerCase().contains(_search),
                            ))
                              Padding(
                                padding: const EdgeInsets.fromLTRB(
                                  10,
                                  12,
                                  6,
                                  8,
                                ),
                                child: Text(
                                  taskLabel(category.key),
                                  style: const TextStyle(
                                    fontSize: 10,
                                    color: Color(0xFF9098AC),
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ),
                            for (final task in category.value.where(
                              (task) =>
                                  task.toLowerCase().contains(_search) ||
                                  taskLabel(
                                    task,
                                  ).toLowerCase().contains(_search),
                            ))
                              Padding(
                                padding: const EdgeInsets.only(bottom: 3),
                                child: Material(
                                  color: _task == task
                                      ? solanaBlue.withValues(alpha: .08)
                                      : Colors.transparent,
                                  borderRadius: BorderRadius.circular(8),
                                  child: ListTile(
                                    dense: true,
                                    selected: _task == task,
                                    selectedColor: solanaBlue,
                                    shape: RoundedRectangleBorder(
                                      borderRadius: BorderRadius.circular(8),
                                    ),
                                    title: Text(
                                      taskLabel(task),
                                      style: const TextStyle(fontSize: 12),
                                    ),
                                    trailing: _task == task
                                        ? const Icon(
                                            Icons.chevron_right_rounded,
                                            size: 16,
                                          )
                                        : null,
                                    onTap: () => _select(task),
                                  ),
                                ),
                              ),
                          ],
                        ],
                      ),
              ),
            ],
          ),
        );
        final editor = Surface(
          padding: const EdgeInsets.all(18),
          child: _task == null
              ? const EmptyState(
                  '选择任务，开始配置',
                  '保留原有分组表单与字段校验。修改后点击保存才会提交。',
                  icon: Icons.tune_rounded,
                )
              : Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    SectionHeading(
                      taskLabel(_task),
                      subtitle:
                          '${_session?.scopeNames.join('、') ?? textValue(widget.controller.profile['name'])} · 修改后保存，运行中的目标从下次运行生效',
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          if (!_loadingForm)
                            TaskJsonTransferActions(
                              configName: textValue(
                                widget.controller.profile['name'],
                                _loadedProfile!,
                              ),
                              taskName: _task!,
                              onImported: () => _loadForm(_task!),
                            ),
                          IconButton(
                            tooltip: '重新加载参数',
                            onPressed: _loadingForm
                                ? null
                                : () async {
                                    if (await canLeave()) {
                                      await _loadForm(_task!);
                                    }
                                  },
                            icon: const Icon(Icons.refresh_rounded, size: 18),
                          ),
                        ],
                      ),
                    ),
                    if (!_loadingForm &&
                        objects(
                          _session?.sourceArgs['scheduler'],
                        ).any((field) => field['name'] == 'next_run')) ...[
                      const SizedBox(height: 10),
                      Wrap(
                        spacing: 12,
                        runSpacing: 6,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          FilledButton.icon(
                            key: const ValueKey('task-execute-now'),
                            onPressed:
                                widget.controller.canControl &&
                                    !widget.controller.controlReviewRequired &&
                                    !_saveBlocked
                                ? _executeTask
                                : null,
                            icon: const Icon(
                              Icons.play_arrow_rounded,
                              size: 18,
                            ),
                            label: const Text('立即执行'),
                          ),
                          const Text(
                            '将本任务排到当前可执行队列；调度器停止时需点击“执行任务”。',
                            style: TextStyle(
                              fontSize: 11,
                              color: Color(0xFF82729E),
                            ),
                          ),
                        ],
                      ),
                    ],
                    const SizedBox(height: 16),
                    if (_error != null) ...[
                      Notice(_error!, error: true),
                      const SizedBox(height: 12),
                    ],
                    Expanded(
                      child: _loadingForm
                          ? const Center(
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : _session == null
                          ? EmptyState(
                              '参数暂不可用',
                              '重新加载后再编辑。',
                              action: TextButton(
                                onPressed: () => _loadForm(_task!),
                                child: const Text('重试'),
                              ),
                            )
                          : AbsorbPointer(
                              absorbing: !widget.controller.connected,
                              child: Args(
                                key: ValueKey(
                                  '$_loadedProfile/$_task/$_editorKey',
                                ),
                                scriptName: _session!.sourceName,
                                taskName: _task,
                                stagingMode: true,
                                readableLayout: true,
                                groupDraggable: false,
                                onCancel: () async {
                                  await args.discardDraftChanges();
                                  if (mounted) setState(() => _editorKey++);
                                },
                              ),
                            ),
                    ),
                  ],
                ),
        );
        if (!widget.showCatalog) return editor;
        if (bounds.maxWidth < 700) {
          return Column(
            children: [
              SizedBox(height: 200, child: catalog),
              const SizedBox(height: 14),
              Expanded(child: editor),
            ],
          );
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(width: 240, child: catalog),
            const SizedBox(width: 18),
            Expanded(child: editor),
          ],
        );
      },
    );
  }
}
