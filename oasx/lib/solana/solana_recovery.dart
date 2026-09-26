import 'package:flutter/material.dart';

import 'solana_api.dart';
import 'solana_controller.dart';
import 'solana_widgets.dart';

String _operationTitle(JsonObject operation) {
  final action =
      const {
        'config.change': '修改配置',
        'config.import': '导入配置',
        'config.copy': '复制配置',
        'config.delete': '删除配置',
        'config.rename': '重命名配置',
        'config.create': '新增配置',
        'start': '启动任务',
        'restart': '重新运行任务',
        'stop': '停止任务',
        'safe_stop': '安全停止',
        'pause': '暂停任务',
        'resume': '继续任务',
      }[operation['action']] ??
      '待核验操作';
  final name = operation['name'];
  return name == null ? action : '$action · $name';
}

String _operationExplanation(Object? value) {
  final explanation = textValue(value, '');
  return const {
        'Older record has no reliable profile identity; manual inspection is required.':
            '旧记录无法确定对应配置，需要手动检查。',
        'Rename identity must be proven from the original source hash; correct conflicting files, then retry reconciliation.':
            '需要核对重命名前后的配置文件。请先处理冲突文件，再重试状态核验。',
        'Review the current configuration. Accepting keeps it unchanged and records the original outcome as unknown; no side effects are repeated.':
            '请检查当前配置。接受后保留当前内容，将原操作结果记为未知，不会重复执行原操作。',
        'Stop all executors before reviewing this control receipt.':
            '请先停止所有正在运行的任务，再核验这次操作。',
        'No live executor is registered. Review current state; acceptance records the historical control outcome as unknown and does not start or stop a process.':
            '当前没有运行中的任务。请检查当前状态；接受后将原操作结果记为未知，不会启动或停止任务。',
        'Configuration identity or file cannot be validated; resolve the file problem first.':
            '无法核对配置名称或文件，请先处理配置文件问题。',
      }[explanation] ??
      (RegExp(r'[\u4e00-\u9fff]').hasMatch(explanation)
          ? explanation
          : '请检查当前配置状态，详细说明可在诊断信息中查看。');
}

/// Retry never treats a retained run as a live process or silently replays it.
Future<void> startSolanaExecution(
  BuildContext context,
  SolanaController controller,
) async {
  if (!controller.canControl || controller.controlReviewRequired) return;
  final backend = controller.backendGeneration;
  final profile = controller.selectedProfile;
  final targets = controller.linker.snapshotFor(
    textValue(controller.profile['name'], ''),
  );
  final ids = targets.map((p) => textValue(p['id'])).toSet();
  controller.setExecutionPreparing(true);
  bool sameScope() {
    final current = controller.linker
        .snapshotFor(textValue(controller.profile['name'], ''))
        .map((p) => textValue(p['id']))
        .toSet();
    return backend == controller.backendGeneration &&
        profile == controller.selectedProfile &&
        ids.length == current.length &&
        ids.containsAll(current);
  }

  try {
    final overview = await controller.api.get('/api/v2/overview');
    final operations = await controller.api.get('/api/v2/recovery/operations');
    if (!context.mounted || !sameScope()) return;
    if (overview['dispatch_blocked'] == true ||
        objects(operations['items']).isNotEmpty) {
      controller.setExecutionPreparing(false);
      await showSolanaRecovery(context, controller, restartRequested: true);
      return;
    }
    final pending = <JsonObject>[];
    for (final target in targets) {
      final recovery = await controller.api.get(
        '/api/v2/recovery',
        query: {'profile_id': target['id']},
      );
      pending.addAll(objects(recovery['items']));
    }
    if (!context.mounted || !sameScope()) return;
    if (pending.any((run) => run['executor_alive'] == true)) {
      throw const SolanaApiException(
        'executor_still_running',
        '执行器仍在运行，请先停止并等待退出，再重新运行。',
      );
    }
    if (pending.isNotEmpty) {
      var reviewed = false;
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => StatefulBuilder(
          builder: (context, change) => AlertDialog(
            title: const Text('重新运行'),
            content: SizedBox(
              width: 430,
              child: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      '以下任务异常退出，执行结果未确认：${pending.map((r) {
                        final name = targets.where((p) => p['id'] == r['profile_id']).firstOrNull?['name'];
                        return '${textValue(name, '当前配置')} · ${taskLabel(r['task_id'])}';
                      }).join('、')}。',
                    ),
                    const SizedBox(height: 10),
                    const Text('请先检查游戏页面和已完成进度。继续后将旧记录记为中断，再按已保存的计划重新运行。'),
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      value: reviewed,
                      onChanged: (value) =>
                          change(() => reviewed = value == true),
                      controlAffinity: ListTileControlAffinity.leading,
                      title: const Text('已检查游戏进度'),
                    ),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('取消'),
              ),
              FilledButton(
                onPressed: reviewed ? () => Navigator.pop(context, true) : null,
                child: const Text('结束旧记录并重新运行'),
              ),
            ],
          ),
        ),
      );
      if (confirmed != true || !reviewed || !context.mounted || !sameScope()) {
        return;
      }
      for (final run in pending) {
        final result = await controller.api.request(
          'POST',
          '/api/v2/recovery/resolve',
          body: {
            'profile_id': run['profile_id'],
            'run_id': run['run_id'],
            'request_id': SolanaController.requestId(),
            'resolution': 'close_interrupted',
            'game_state_reviewed': true,
          },
        );
        if (result['executed'] != true ||
            result['persisted'] != true ||
            result['status'] != 'resolved') {
          throw const SolanaApiException(
            'recovery_unconfirmed',
            '旧记录处理结果未确认，未重新启动。请刷新核验。',
          );
        }
        if (!context.mounted || !sameScope()) return;
      }
    }
    // Always use a fresh state version after recovery and after the dialog.
    final latest = await controller.api.get('/api/v2/overview');
    if (!context.mounted || !sameScope()) return;
    controller.overview.data = latest;
    controller.setExecutionPreparing(false);
    await controller.control('start');
  } catch (error) {
    if (!sameScope()) return;
    controller.operationFailed = true;
    controller.operationMessage = error is SolanaApiException
        ? error.message
        : '重新运行未完成，请检查连接并刷新状态。';
    await controller.refreshAll();
  } finally {
    controller.setExecutionPreparing(false);
  }
}

Future<void> showSolanaRecovery(
  BuildContext context,
  SolanaController controller, {
  bool restartRequested = false,
}) async {
  await controller.refreshRecovery();
  if (!context.mounted) return;
  await showDialog<void>(
    context: context,
    builder: (context) => AnimatedBuilder(
      animation: controller,
      builder: (context, _) => AlertDialog(
        title: const Text('异常恢复与状态核验'),
        content: SizedBox(
          width: 540,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (restartRequested) ...[
                  const Notice(
                    '重新运行前需处理下方未确认记录并核验存储状态。完成后关闭此窗口，再点击“重新运行”或“执行任务”。',
                  ),
                  const SizedBox(height: 12),
                ],
                const Text(
                  '先检查游戏中的实际进度，再处理未确认的运行。结束未确认运行会记录为中断，不会标记成功，也不会自动补跑任务。',
                  style: TextStyle(fontSize: 12),
                ),
                const SizedBox(height: 14),
                if (controller.operationMessage != null) ...[
                  Notice(
                    controller.operationMessage!,
                    error: controller.operationFailed,
                  ),
                  const SizedBox(height: 10),
                ],
                RemoteBody(
                  remote: controller.recovery,
                  retry: controller.refreshRecovery,
                  builder: (data) => Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      for (final run in objects(data['items']))
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          title: Text(
                            taskLabel(run['task_name'] ?? run['task_id']),
                            style: const TextStyle(fontSize: 13),
                          ),
                          subtitle: Text(
                            stateLabel(run['state']),
                            style: const TextStyle(fontSize: 11),
                          ),
                          trailing: OutlinedButton(
                            onPressed: controller.busy
                                ? null
                                : () => _reviewRun(context, controller, run),
                            child: const Text('检查并结束'),
                          ),
                        ),
                      if (objects(data['items']).isEmpty)
                        const Padding(
                          padding: EdgeInsets.symmetric(vertical: 12),
                          child: Text(
                            '没有待处理的未确认运行',
                            style: TextStyle(fontSize: 12),
                          ),
                        ),
                    ],
                  ),
                ),
                const Divider(),
                const Text('待核验的配置操作', style: TextStyle(fontSize: 13)),
                const SizedBox(height: 6),
                const Text(
                  '检查当前配置文件、名称或导入结果后，可接受现在的配置状态。不会重放原操作，也不会将历史操作标记成功。',
                  style: TextStyle(fontSize: 11),
                ),
                RemoteBody(
                  remote: controller.recoveryOperations,
                  retry: controller.refreshRecovery,
                  builder: (data) => Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      for (final operation in objects(data['items']))
                        Padding(
                          padding: const EdgeInsets.symmetric(vertical: 8),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              Text(
                                _operationTitle(operation),
                                style: const TextStyle(
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                _operationExplanation(operation['explanation']),
                                style: const TextStyle(fontSize: 11),
                              ),
                              if (operation['path'] != null ||
                                  operation['key'] != null)
                                ExpansionTile(
                                  tilePadding: EdgeInsets.zero,
                                  title: const Text(
                                    '诊断信息',
                                    style: TextStyle(fontSize: 11),
                                  ),
                                  children: [
                                    if (operation['path'] != null)
                                      SelectableText(
                                        '配置路径：${operation['path']}',
                                      ),
                                    if (operation['key'] != null)
                                      SelectableText(
                                        '记录标识：${operation['key']}',
                                      ),
                                    if (operation['explanation'] != null)
                                      SelectableText(
                                        '原始说明：${operation['explanation']}',
                                      ),
                                  ],
                                ),
                              if (operation['resolvable'] == true)
                                Align(
                                  alignment: Alignment.centerRight,
                                  child: OutlinedButton(
                                    onPressed: controller.busy
                                        ? null
                                        : () => _reviewOperation(
                                            context,
                                            controller,
                                            operation,
                                          ),
                                    child: const Text('检查当前配置'),
                                  ),
                                ),
                            ],
                          ),
                        ),
                      if (objects(data['items']).isEmpty)
                        const Padding(
                          padding: EdgeInsets.symmetric(vertical: 12),
                          child: Text(
                            '没有待核验的配置操作',
                            style: TextStyle(fontSize: 12),
                          ),
                        ),
                    ],
                  ),
                ),
                const Divider(),
                const Text(
                  '存储核验会检查活跃执行器、设备控制权及恢复文件的一致性。检查通过后解除派发阻塞，不会自动启动任务。',
                  style: TextStyle(fontSize: 11),
                ),
                const SizedBox(height: 12),
                OutlinedButton.icon(
                  onPressed: controller.busy
                      ? null
                      : controller.reconcileStorage,
                  icon: const Icon(Icons.fact_check_outlined, size: 17),
                  label: const Text('核验存储状态'),
                ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('关闭'),
          ),
        ],
      ),
    ),
  );
}

Future<void> _reviewOperation(
  BuildContext context,
  SolanaController controller,
  JsonObject operation,
) async {
  bool reviewed = false;
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (context) => StatefulBuilder(
      builder: (context, setState) => AlertDialog(
        title: const Text('接受核验后的配置状态'),
        content: SizedBox(
          width: 440,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  _operationExplanation(operation['explanation']),
                  style: const TextStyle(fontSize: 12),
                ),
                const SizedBox(height: 10),
                const Text(
                  '请在任务配置界面或配置文件中检查当前结果。确认仅接受目前状态，不会重新执行导入、复制、删除或保存，不会将原操作标记成功。',
                  style: TextStyle(fontSize: 12),
                ),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: reviewed,
                  onChanged: (value) =>
                      setState(() => reviewed = value == true),
                  title: const Text(
                    '我已检查当前配置状态',
                    style: TextStyle(fontSize: 12),
                  ),
                  controlAffinity: ListTileControlAffinity.leading,
                ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('返回检查'),
          ),
          FilledButton(
            onPressed: reviewed ? () => Navigator.pop(context, true) : null,
            child: const Text('接受当前状态'),
          ),
        ],
      ),
    ),
  );
  if (confirmed == true && reviewed) {
    await controller.resolveOperation(operation, reviewed: true);
  }
}

Future<void> _reviewRun(
  BuildContext context,
  SolanaController controller,
  JsonObject run,
) async {
  bool reviewed = false;
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (context) => StatefulBuilder(
      builder: (context, setState) => AlertDialog(
        title: const Text('结束这次未确认运行'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              '请检查游戏实际页面、已完成场次与已领取奖励。此操作只结束旧运行并记录中断；之后是否重跑由你决定。',
              style: TextStyle(fontSize: 12),
            ),
            const SizedBox(height: 12),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: reviewed,
              onChanged: (value) => setState(() => reviewed = value == true),
              title: const Text('我已检查游戏中的实际进度', style: TextStyle(fontSize: 12)),
              controlAffinity: ListTileControlAffinity.leading,
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('返回检查'),
          ),
          FilledButton(
            onPressed: reviewed ? () => Navigator.pop(context, true) : null,
            child: const Text('记录中断并结束'),
          ),
        ],
      ),
    ),
  );
  if (confirmed == true && reviewed) {
    await controller.resolveRecovery(
      textValue(run['run_id']),
      gameStateReviewed: true,
    );
  }
}
