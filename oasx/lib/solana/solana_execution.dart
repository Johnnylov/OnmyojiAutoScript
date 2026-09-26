import 'solana_api.dart';

/// Execution controls follow the executor, not a retained audit/recovery run.
class SolanaExecutionState {
  final JsonObject profile;
  final bool hasUncertainRun;
  final bool supportsRestart;
  const SolanaExecutionState(
    this.profile, {
    this.hasUncertainRun = false,
    this.supportsRestart = false,
  });

  String get state => textValue(profile['state'], 'unknown');
  bool get active => profile['executor_alive'] is bool
      ? profile['executor_alive'] == true
      : const {
          'starting',
          'running',
          'waiting',
          'waiting_resource',
          'paused',
          'yielded',
          'pausing',
          'stopping',
          'control_pending',
        }.contains(state);
  bool get retry =>
      (supportsRestart || !active) &&
      (hasUncertainRun ||
          const {
            'warning',
            'failed',
            'crashed',
            'interrupted',
            'needs_reconciliation',
            'recovery_requested',
          }.contains(state));
  String? get action {
    if (retry && supportsRestart) return 'restart';
    if (!active) return 'start';
    if (state == 'paused') return 'resume';
    if (const {
      'running',
      'waiting',
      'waiting_resource',
      'yielded',
    }.contains(state)) {
      return 'pause';
    }
    return null;
  }

  String get label => switch (action) {
    'restart' => '重新运行',
    'start' => retry ? '重新运行' : '执行任务',
    'resume' => '继续运行',
    'pause' => '暂停',
    _ => switch (state) {
      'pausing' => '正在暂停…',
      'stopping' => '正在停止…',
      'starting' => '正在启动…',
      _ => '等待状态确认',
    },
  };
  String get explanation => switch (action) {
    'restart' => '结束异常执行器，旧运行记为中断，再按已保存的任务计划重新启动。',
    'start' => retry ? '重新启动当前配置，按已保存的任务计划执行。' : '启动当前配置，执行已启用且到期的任务。',
    'resume' => '从暂停处继续执行，保留已有进度。',
    'pause' => '到安全位置后暂停，保留执行进程，可点击“继续运行”。',
    _ => '等待执行器确认操作，请勿重复提交。',
  };

  String get powerAction => retry && supportsRestart
      ? 'restart'
      : active
      ? 'safe_stop'
      : 'start';
  String get powerExplanation => switch (powerAction) {
    'restart' => '重新运行：结束旧执行器后，按已保存的计划重新启动',
    'safe_stop' => '安全停止：到安全位置后结束进程，下次需重新启动',
    _ => '启动任务：执行当前配置中已启用且到期的任务',
  };
}
