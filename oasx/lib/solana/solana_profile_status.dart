import 'package:flutter/material.dart';

import 'solana_api.dart';
import 'solana_execution.dart';
import 'solana_widgets.dart';

/// A profile's executor can be running while it waits for its next task.
/// The scheduler dot and sidebar always use the same state interpretation.
class SolanaProfileStatus {
  final JsonObject profile;
  final bool connected;
  final bool hasUncertainRun;
  const SolanaProfileStatus(
    this.profile, {
    required this.connected,
    this.hasUncertainRun = false,
  });

  SolanaExecutionState get execution => SolanaExecutionState(profile);
  bool get failed =>
      connected &&
      (const {
            'warning',
            'error',
            'failed',
            'crashed',
            'interrupted',
            'needs_reconciliation',
            'recovery_requested',
          }.contains(execution.state) ||
          (!execution.active && hasUncertainRun));
  bool get paused =>
      connected &&
      execution.active &&
      const {'paused', 'pausing'}.contains(execution.state);
  bool get running => connected && execution.active && !failed && !paused;

  Color get color => !connected
      ? Colors.grey
      : failed
      ? solanaRed
      : paused
      ? solanaAmber
      : running
      ? solanaGreen
      : solanaBlue;
  IconData get icon => failed
      ? Icons.error
      : running
      ? Icons.pause_circle_filled
      : Icons.play_circle_fill;
  String get label => !connected
      ? '状态未知'
      : failed
      ? '运行异常'
      : paused
      ? (execution.state == 'pausing' ? '正在暂停' : '已暂停')
      : running
      ? stateLabel(execution.state)
      : '未启动';
}

/// Sort only the waiting display; ready tasks retain the scheduler's order.
/// Keep equal/unknown times stable to prevent rows moving on every refresh.
List<JsonObject> waitingByNextRun(Object? value) {
  final indexed = objects(value).indexed.toList();
  indexed.sort((a, b) {
    final left = DateTime.tryParse(a.$2['next_run']?.toString() ?? '');
    final right = DateTime.tryParse(b.$2['next_run']?.toString() ?? '');
    final order = left == null
        ? (right == null ? 0 : 1)
        : right == null
        ? -1
        : left.compareTo(right);
    return order == 0 ? a.$1.compareTo(b.$1) : order;
  });
  return indexed.map((entry) => entry.$2).toList();
}
