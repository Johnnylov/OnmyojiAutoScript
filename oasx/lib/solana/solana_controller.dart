import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'solana_api.dart';
import 'solana_linker.dart';

class RemoteData {
  JsonObject? data;
  SolanaApiException? error;
  bool loading = false;
  DateTime? updatedAt;
  bool get empty => data == null;
}

class _OperationFeedback {
  String? message;
  bool failed = false;
  SolanaBatchResult? controlResult;
}

class SolanaController extends ChangeNotifier {
  final SolanaApi api;
  late final SolanaLinker linker;
  final _operationFeedback = <String?, _OperationFeedback>{};
  final _controlReviewProfiles = <String>{};

  _OperationFeedback _feedbackFor(String? profileId) =>
      _operationFeedback.putIfAbsent(profileId, _OperationFeedback.new);

  SolanaBatchResult? get lastControlResult =>
      _operationFeedback[selectedProfile]?.controlResult;

  Set<String> get _controlScopeIds {
    final names = linker.scopeFor(textValue(profile['name'], '')).toSet();
    return {
      if (selectedProfile != null) selectedProfile!,
      for (final target in profiles)
        if (names.contains(target['name'])) textValue(target['id']),
    };
  }

  bool get controlReviewRequired =>
      _controlScopeIds.any(_controlReviewProfiles.contains);

  void _requireControlReview(List<JsonObject> targets) => _controlReviewProfiles
      .addAll(targets.map((target) => textValue(target['id'])));
  final capabilities = RemoteData();
  final overview = RemoteData();
  final scheduler = RemoteData();
  final statistics = RemoteData();
  final runs = RemoteData();
  final audit = RemoteData();
  final storage = RemoteData();
  final preview = RemoteData();
  final recovery = RemoteData();
  final recoveryOperations = RemoteData();
  String? selectedProfile;
  String taskFilter = '';
  DateTime from = DateTime.now().subtract(const Duration(days: 6));
  DateTime to = DateTime.now();
  bool connected = false;
  bool streamConnected = false;
  bool busy = false;
  bool _executionPreparing = false;
  bool get executionPreparing => _executionPreparing;
  void setExecutionPreparing(bool value) {
    _executionPreparing = value;
    _emit();
  }

  String? get operationMessage => _operationFeedback[selectedProfile]?.message;
  set operationMessage(String? value) =>
      _feedbackFor(selectedProfile).message = value;
  bool get operationFailed =>
      _operationFeedback[selectedProfile]?.failed ?? false;
  set operationFailed(bool value) =>
      _feedbackFor(selectedProfile).failed = value;
  bool _disposed = false;
  bool _refreshing = false;
  bool _liveRefreshPending = false;
  final previewChanges = ChangeNotifier();
  final deadlineChanges = ValueNotifier<JsonObject?>(null);
  bool _switchingBackend = false;
  int _filterGeneration = 0;
  Timer? _poll;
  Timer? _debounce;
  Timer? _reconnect;
  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;
  String? _streamId;
  int? _streamSeq;
  int _connectionGeneration = 0;
  int _previewGeneration = 0;
  int _backendGeneration = 0;
  int get backendGeneration => _backendGeneration;

  SolanaController({SolanaApi? api, SolanaLinker? linker})
    : api = api ?? SolanaApi() {
    this.linker = linker ?? SolanaLinker(profiles: () => profiles);
    this.linker.addListener(_emit);
  }

  List<JsonObject> get profiles => objects(overview.data?['profiles']);
  JsonObject get profile => profiles.firstWhere(
    (item) => textValue(item['id']) == selectedProfile,
    orElse: () => <String, dynamic>{},
  );
  bool get supported => capabilities.data != null;
  bool get canControl =>
      connected &&
      supported &&
      selectedProfile != null &&
      !busy &&
      !_executionPreparing;
  bool get canAttemptSafetyControl =>
      supported && selectedProfile != null && !busy && !_executionPreparing;

  JsonObject get filter => {
    'start_date': date(from),
    'end_date': date(to),
    if (selectedProfile != null) 'profile_id': selectedProfile,
    if (taskFilter.trim().isNotEmpty) 'task_id': taskFilter.trim(),
  };

  static String date(DateTime value) =>
      '${value.year}-${value.month.toString().padLeft(2, '0')}-${value.day.toString().padLeft(2, '0')}';

  static String requestId() {
    final random = Random.secure();
    final bytes = List<int>.generate(16, (_) => random.nextInt(256));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
    return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
  }

  Future<void> start() async {
    if (_disposed || _switchingBackend) return;
    final backend = _backendGeneration;
    ++_connectionGeneration;
    streamConnected = false;
    final subscription = _subscription;
    final channel = _channel;
    _subscription = null;
    _channel = null;
    await subscription?.cancel();
    await channel?.sink.close();
    if (_disposed || backend != _backendGeneration) return;
    capabilities.data = null;
    await _load(capabilities, '/api/v2/capabilities');
    if (_disposed || backend != _backendGeneration) return;
    if (capabilities.data != null) {
      await refreshAll();
      if (_disposed || backend != _backendGeneration) return;
      await _connectStream();
    }
    if (_disposed || backend != _backendGeneration) return;
    _poll ??= Timer.periodic(const Duration(seconds: 15), (_) async {
      final backend = _backendGeneration;
      if (!supported) await _load(capabilities, '/api/v2/capabilities');
      if (_disposed || backend != _backendGeneration) return;
      if (supported) {
        await refreshAll();
        if (_disposed || backend != _backendGeneration) return;
        if (!streamConnected) await _connectStream();
      }
    });
  }

  /// Changes a backend as a new connection session. Old HTTP completions and
  /// socket callbacks cannot repopulate the newly cleared view.
  Future<void> reconnectBackend({
    Future<void> Function()? updateConnection,
  }) async {
    if (busy) {
      throw const SolanaApiException(
        'operation_in_progress',
        '请等待当前操作完成后再修改连接。',
        409,
      );
    }
    if (_disposed) return;
    busy = true;
    _switchingBackend = true;
    ++_backendGeneration;
    ++_connectionGeneration;
    ++_filterGeneration;
    ++_previewGeneration;
    _poll?.cancel();
    _poll = null;
    _debounce?.cancel();
    _reconnect?.cancel();
    _refreshing = false;
    _liveRefreshPending = false;
    final subscription = _subscription;
    final channel = _channel;
    _subscription = null;
    _channel = null;
    _streamId = null;
    _streamSeq = null;
    streamConnected = false;
    connected = false;
    selectedProfile = null;
    taskFilter = '';
    _controlReviewProfiles.clear();
    _operationFeedback.clear();
    linker.setEnabled(false);
    for (final remote in [
      capabilities,
      overview,
      scheduler,
      statistics,
      runs,
      audit,
      storage,
      preview,
      recovery,
      recoveryOperations,
    ]) {
      remote.data = null;
      remote.error = null;
      remote.updatedAt = null;
      remote.loading = false;
    }
    _emit();
    try {
      await subscription?.cancel();
      await channel?.sink.close();
      await updateConnection?.call();
      _switchingBackend = false;
      if (!_disposed) await start();
    } catch (_) {
      // Saving may be rejected after old streams are already closed. Restore
      // read-only monitoring of whichever address is currently saved, while
      // preserving the original save/guard exception for the dialog.
      _switchingBackend = false;
      if (!_disposed) {
        try {
          await start();
        } catch (_) {
          // Recovery failure must not replace the original error.
        }
      }
      rethrow;
    } finally {
      _switchingBackend = false;
      busy = false;
      _emit();
    }
  }

  Future<void> _load(
    RemoteData target,
    String path, {
    JsonObject? query,
    int? generation,
    bool notify = true,
  }) async {
    if (_disposed || _switchingBackend) return;
    final backend = _backendGeneration;
    target.loading = true;
    if (notify && target.data == null) _emit();
    try {
      final value = await api.get(path, query: query);
      if (_disposed ||
          backend != _backendGeneration ||
          (generation != null && generation != _filterGeneration)) {
        return;
      }
      target.data = value;
      target.error = null;
      target.updatedAt = DateTime.now();
      if (identical(target, overview)) {
        connected = true;
        final available = profiles;
        if (selectedProfile == null && available.isNotEmpty) {
          selectedProfile = textValue(available.first['id']);
        }
        if (selectedProfile != null &&
            !available.any(
              (item) => textValue(item['id']) == selectedProfile,
            )) {
          selectedProfile = available.isEmpty
              ? null
              : textValue(available.first['id']);
        }
      }
    } catch (error) {
      if (_disposed ||
          backend != _backendGeneration ||
          (generation != null && generation != _filterGeneration)) {
        return;
      }
      target.error = error is SolanaApiException
          ? error
          : const SolanaApiException('unexpected', '加载失败，请重试');
      if (identical(target, overview) || identical(target, capabilities)) {
        connected = false;
      }
    } finally {
      if (backend == _backendGeneration &&
          (generation == null || generation == _filterGeneration)) {
        target.loading = false;
      }
      if (notify) _emit();
    }
  }

  Future<void> refreshAll() async {
    if (_refreshing || _disposed || _switchingBackend) return;
    final backend = _backendGeneration;
    _refreshing = true;
    try {
      await _load(overview, '/api/v2/overview', notify: false);
      if (_disposed || backend != _backendGeneration) return;
      final generation = _filterGeneration;
      final reads = Future.wait([
        _load(
          scheduler,
          '/api/v2/scheduler',
          query: {if (selectedProfile != null) 'profile_id': selectedProfile},
          generation: generation,
          notify: false,
        ),
        _load(storage, '/api/v2/storage', notify: false),
        refreshRecovery(notify: false),
        refreshHistory(notify: false),
      ]);
      if (scheduler.data == null) _emit();
      await reads;
    } finally {
      if (backend == _backendGeneration) {
        _refreshing = false;
        _emit();
        if (_liveRefreshPending) _scheduleLiveRefresh();
      }
    }
  }

  /// Streaming events update the live workbench only. History/storage/recovery
  /// remain on the 15-second poll and explicit user refreshes.
  Future<void> refreshLive() async {
    if (_disposed || _switchingBackend) return;
    if (_refreshing) {
      _liveRefreshPending = true;
      return;
    }
    _liveRefreshPending = false;
    _refreshing = true;
    final backend = _backendGeneration;
    try {
      await _load(overview, '/api/v2/overview', notify: false);
      if (_disposed || backend != _backendGeneration) return;
      await _load(
        scheduler,
        '/api/v2/scheduler',
        query: {if (selectedProfile != null) 'profile_id': selectedProfile},
        generation: _filterGeneration,
        notify: false,
      );
    } finally {
      if (backend == _backendGeneration) {
        _refreshing = false;
        _emit();
        if (_liveRefreshPending) _scheduleLiveRefresh();
      }
    }
  }

  void _scheduleLiveRefresh() {
    if (_disposed || _switchingBackend || (_debounce?.isActive ?? false)) {
      return;
    }
    // A fixed window also updates under a continuous stream; a trailing debounce
    // could postpone the display forever while tasks keep producing events.
    _debounce = Timer(const Duration(seconds: 1), () {
      _debounce = null;
      unawaited(refreshLive());
    });
  }

  Future<void> refreshHistory({bool notify = true}) async {
    final generation = _filterGeneration;
    final query = filter;
    await Future.wait([
      _load(
        statistics,
        '/api/v2/statistics',
        query: query,
        generation: generation,
        notify: false,
      ),
      _load(
        runs,
        '/api/v2/runs',
        query: {...query, 'limit': 50},
        generation: generation,
        notify: false,
      ),
      _load(
        audit,
        '/api/v2/audit',
        query: {...query, 'limit': 50},
        generation: generation,
        notify: false,
      ),
    ]);
    if (notify) _emit();
  }

  Future<void> selectProfile(String value) async {
    selectedProfile = value;
    preview.data = null;
    preview.error = null;
    previewChanges.notifyListeners();
    preview.loading = false;
    ++_previewGeneration;
    _filterGeneration++;
    for (final remote in [
      scheduler,
      statistics,
      runs,
      audit,
      recovery,
      recoveryOperations,
    ]) {
      remote.data = null;
      remote.error = null;
    }
    _emit();
    final generation = _filterGeneration;
    await Future.wait([
      _load(
        scheduler,
        '/api/v2/scheduler',
        query: {'profile_id': value},
        generation: generation,
      ),
      refreshHistory(),
      refreshRecovery(),
    ]);
  }

  Future<void> setRange(DateTime start, DateTime end) async {
    from = start;
    to = end;
    _filterGeneration++;
    statistics.data = null;
    runs.data = null;
    audit.data = null;
    await refreshHistory();
  }

  Future<void> refreshPreview() async {
    if (_disposed || preview.loading || !connected || selectedProfile == null) {
      return;
    }
    final generation = ++_previewGeneration;
    final profile = selectedProfile;
    preview.loading = true;
    try {
      final data = await api.get(
        '/api/v2/preview',
        query: {'profile_id': profile},
      );
      if (_disposed || generation != _previewGeneration) return;
      preview.data = data;
      preview.error = null;
      preview.updatedAt = DateTime.now();
    } catch (error) {
      if (!_disposed && generation == _previewGeneration) {
        preview.error = error is SolanaApiException
            ? error
            : const SolanaApiException('preview_error', '设备画面暂不可用');
      }
    } finally {
      if (generation == _previewGeneration) preview.loading = false;
      if (!_disposed && generation == _previewGeneration) {
        previewChanges.notifyListeners();
      }
    }
  }

  Future<void> refreshRecovery({bool notify = true}) async {
    final query = <String, dynamic>{
      if (selectedProfile != null) 'profile_id': selectedProfile,
    };
    final generation = _filterGeneration;
    await Future.wait([
      _load(
        recovery,
        '/api/v2/recovery',
        query: query,
        generation: generation,
        notify: false,
      ),
      _load(
        recoveryOperations,
        '/api/v2/recovery/operations',
        query: query,
        generation: generation,
        notify: false,
      ),
    ]);
    if (notify) _emit();
  }

  Future<void> resolveOperation(
    JsonObject operation, {
    required bool reviewed,
  }) async {
    if (!reviewed || operation['resolvable'] != true) return;
    await _mutate(
      'POST',
      '/api/v2/recovery/operations/resolve',
      {
        'kind': operation['kind'],
        'key': operation['key'],
        'expected_revisions': operation['expected_revisions'],
        'reviewed': true,
        'request_id': operation['resolution_request_id'] ?? requestId(),
      },
      (_) => '已接受核验后的当前配置状态，未重放原操作，也未将原操作标记成功。',
    );
  }

  Future<void> resolveRecovery(
    String runId, {
    required bool gameStateReviewed,
  }) async {
    if (!gameStateReviewed) return;
    await _mutate('POST', '/api/v2/recovery/resolve', {
      'profile_id': selectedProfile,
      'run_id': runId,
      'request_id': requestId(),
      'resolution': 'close_interrupted',
      'game_state_reviewed': true,
    }, (_) => '已结束未确认运行并记录为中断。没有自动补跑任务。');
  }

  Future<void> reconcileStorage() => _mutate(
    'POST',
    '/api/v2/storage/reconcile',
    {},
    (_) => '存储核验已完成，当前状态已刷新；任务不会自动启动。',
  );

  Future<void> setTaskFilter(String value) async {
    taskFilter = value;
    _filterGeneration++;
    statistics.data = null;
    runs.data = null;
    audit.data = null;
    await refreshHistory();
  }

  Future<void> nextPage(RemoteData target, String endpoint) async {
    final cursor = target.data?['next_cursor'];
    if (cursor == null || target.loading) return;
    final generation = _filterGeneration;
    target.loading = true;
    _emit();
    try {
      final next = await api.get(
        endpoint,
        query: {...filter, 'limit': 50, 'cursor': cursor},
      );
      if (_disposed || generation != _filterGeneration) return;
      target.data = {
        ...next,
        'items': [...objects(target.data?['items']), ...objects(next['items'])],
      };
      target.error = null;
    } catch (error) {
      if (generation == _filterGeneration) {
        target.error = error is SolanaApiException
            ? error
            : const SolanaApiException('request_failed', '加载下一页失败');
      }
    } finally {
      if (generation == _filterGeneration) target.loading = false;
      _emit();
    }
  }

  Future<void> control(String action) async {
    final safety = action == 'safe_stop' || action == 'immediate_stop';
    if (!(safety ? canAttemptSafetyControl : canControl)) return;
    if (controlReviewRequired && !safety) {
      operationFailed = true;
      operationMessage = '上次操作有未确认结果，请先重新加载联动状态；不会重复提交已成功配置。';
      _emit();
      return;
    }
    final targets = linker.snapshotFor(textValue(profile['name'], ''));
    if (targets.isEmpty) return;
    final source = selectedProfile;
    final sourceName = textValue(profile['name']);
    final controlBackend = backendGeneration;
    // Capture ownership before awaiting. Selecting another profile only changes
    // which feedback is visible; it must never relabel or retarget this request.
    final feedback = _feedbackFor(source);
    busy = true;
    feedback.message = null;
    feedback.failed = false;
    _emit();
    final outcomes = <SolanaTargetResult>[];
    try {
      for (final target in targets) {
        final id = textValue(target['id']);
        final name = textValue(target['name']);
        try {
          final latest = await api.get('/api/v2/overview');
          if (controlBackend != backendGeneration) return;
          final current = objects(
            latest['profiles'],
          ).where((p) => p['id'] == id).firstOrNull;
          if (current == null || current['state_version'] == null) {
            throw const SolanaApiException(
              'state_version_missing',
              '状态版本不可用，请重新加载',
              409,
            );
          }
          final result = await api.request(
            'POST',
            '/api/v2/control',
            body: {
              'profile_id': id,
              'action': action,
              'request_id': requestId(),
              'expected_state_version': current['state_version'],
            },
          );
          final message = _controlMessage(action, result);
          outcomes.add(
            SolanaTargetResult(
              id,
              name,
              true,
              message,
              needsReview: result['persisted'] == false,
            ),
          );
        } catch (error) {
          // These responses reject the request before performing it. A stale
          // version is not an uncertain side effect and must not latch Start.
          final rejectedBeforeExecution =
              error is SolanaApiException &&
              error.status == 409 &&
              const {
                'state_conflict',
                'executor_not_running',
                'already_running',
                'control_in_progress',
                'control_superseded',
                'execution_failed',
              }.contains(error.code);
          outcomes.add(
            SolanaTargetResult(
              id,
              name,
              false,
              error is SolanaApiException
                  ? error.code == 'state_conflict'
                        ? '任务状态刚刚变化，已刷新；请再次点击操作'
                        : error.message
                  : '操作结果未确认，请先核验',
              needsReview: !rejectedBeforeExecution,
            ),
          );
        }
        // A stop must reach every target, even after one backend rejects it.
      }
      final result = SolanaBatchResult(outcomes);
      feedback.controlResult = result;
      final needsReview = outcomes.any((outcome) => outcome.needsReview);
      if (needsReview) _requireControlReview(targets);
      feedback.failed = needsReview || !result.allSuccess;
      feedback.message =
          (targets.length > 1 ? '联动操作（由 $sourceName 发起）：' : '') +
          result.message +
          (needsReview ? '。请重新加载联动状态后再操作；未自动重试。' : '');
      await refreshAll();
    } finally {
      busy = false;
      _emit();
    }
  }

  /// Explicit review action; background polling cannot authorize another retry.
  Future<void> reloadLinkedControl() async {
    if (busy) return;
    final scope = _controlScopeIds;
    final feedback = _feedbackFor(selectedProfile);
    busy = true;
    _emit();
    try {
      await _load(overview, '/api/v2/overview');
      if (overview.error == null) {
        _controlReviewProfiles.removeAll(scope);
        feedback.message = '联动状态已重新加载，请核验各配置当前状态后操作。';
        feedback.failed = false;
      }
    } finally {
      busy = false;
      _emit();
    }
  }

  Future<void> bulkQuickSchedule({bool runNow = true}) async {
    if (!canControl) return;
    if (!runNow) {
      operationFailed = true;
      operationMessage = '当前快捷操作仅支持立即全部执行。';
      _emit();
      return;
    }
    await _quickSchedule();
  }

  Future<void> quickScheduleTask(String task) async {
    final chosen = task.trim();
    if (chosen.isEmpty) return;
    await _quickSchedule(chosenTasks: [chosen]);
  }

  Future<void> _quickSchedule({List<String>? chosenTasks}) async {
    if (!canControl) return;
    if (controlReviewRequired) {
      operationFailed = true;
      operationMessage = '请先重新加载联动状态，未重复提交上次操作。';
      _emit();
      return;
    }
    final source = selectedProfile!;
    final sourceName = textValue(profile['name']);
    final targets = linker.snapshotFor(textValue(profile['name'], ''));
    final feedback = _feedbackFor(source);
    busy = true;
    feedback.message = null;
    feedback.failed = false;
    _emit();
    try {
      final result = await scheduleLinkedTasksNow(
        api: api,
        requestId: requestId,
        sourceId: source,
        targets: targets,
        chosenTasks: chosenTasks,
      );
      if (result.needsReview) _requireControlReview(targets);
      feedback.failed = result.needsReview;
      feedback.message =
          '${targets.length > 1 ? '联动操作（由 $sourceName 发起）：' : ''}${result.message}。${result.needsReview ? '请重新加载核验，未自动重试。' : '若执行器已停止，请点击「执行任务」启动调度器。'}';
      await refreshAll();
    } catch (error) {
      feedback.failed = true;
      _requireControlReview(targets);
      feedback.message = error is SolanaApiException
          ? error.message
          : '任务安排结果未确认，请重新加载核验。';
    } finally {
      busy = false;
      _emit();
    }
  }

  String _controlMessage(String action, JsonObject result) {
    if (['failed', 'rejected', 'not_saved'].contains(result['status'])) {
      throw SolanaApiException(
        result['persisted'] == true && result['executed'] == false
            ? 'execution_failed'
            : 'control_failed',
        textValue(
          result['message'],
          result['persisted'] == true && result['executed'] == false
              ? '操作执行失败，已刷新状态，可再次点击操作'
              : '操作执行失败，请查看审计记录并核验当前状态',
        ),
        409,
      );
    }
    if (result['persisted'] == false && result['executed'] == true) {
      return '操作已执行，但记录未保存。恢复后需要核验状态。';
    }
    if (result['accepted'] == false) {
      throw SolanaApiException(
        'rejected',
        textValue(result['message'], '当前状态不允许此操作'),
        409,
      );
    }
    if (result['executed'] != true ||
        [
          'pending',
          'waiting_boundary',
          'waiting_safe_boundary',
          'requested',
          'pausing',
          'stopping',
        ].contains(result['status'])) {
      return action == 'pause' || action == 'safe_stop'
          ? '请求已接收，正在等待安全位置'
          : '请求已接收，等待执行器确认';
    }
    return '操作已执行，状态已更新';
  }

  Future<void> saveScheduler(JsonObject policy) => _mutate(
    'PUT',
    '/api/v2/scheduler/policy',
    policy,
    (_) => '调度策略已保存，将在安全边界生效',
  );

  Future<void> saveStorage(JsonObject policy) =>
      _mutate('PUT', '/api/v2/storage/policy', policy, (_) => '存储设置已保存');

  Future<void> cleanup() => _mutate(
    'POST',
    '/api/v2/storage/cleanup',
    {'request_id': requestId()},
    (result) =>
        '清理完成，释放 ${formatBytes(result['freed_bytes'] ?? result['reclaimed_bytes'])}',
  );

  Future<void> _mutate(
    String method,
    String path,
    JsonObject payload,
    String Function(JsonObject) success,
  ) async {
    if (busy) return;
    final feedback = _feedbackFor(selectedProfile);
    busy = true;
    feedback.message = null;
    feedback.failed = false;
    _emit();
    try {
      final result = await api.request(method, path, body: payload);
      feedback.message = success(result);
      await refreshAll();
    } catch (error) {
      feedback.failed = true;
      feedback.message = error is SolanaApiException
          ? '${error.message}（${error.code}）${error.status == null ? ' · 操作结果未确认，请先刷新核验。' : ''}'
          : '操作结果未确认，请刷新核验；不要重复提交';
    } finally {
      busy = false;
      _emit();
    }
  }

  Future<void> _connectStream() async {
    if (_disposed || !supported || !connected || streamConnected) return;
    _reconnect?.cancel();
    final backend = _backendGeneration;
    final generation = ++_connectionGeneration;
    final subscription = _subscription;
    final previousChannel = _channel;
    _subscription = null;
    _channel = null;
    await subscription?.cancel();
    await previousChannel?.sink.close();
    if (_disposed ||
        backend != _backendGeneration ||
        generation != _connectionGeneration) {
      return;
    }
    _streamId = overview.data?['stream_id']?.toString();
    _streamSeq = numberValue(overview.data?['stream_seq'])?.toInt();
    try {
      final channel = api.connect(streamId: _streamId, after: _streamSeq);
      _channel = channel;
      await channel.ready.timeout(const Duration(seconds: 8));
      if (_disposed || generation != _connectionGeneration) {
        await channel.sink.close();
        return;
      }
      streamConnected = true;
      _emit();
      _subscription = channel.stream.listen(
        (dynamic message) {
          if (_disposed || generation != _connectionGeneration) return;
          try {
            final event = object(jsonDecode(message.toString()));
            final type = event['type'];
            final seq = numberValue(event['stream_seq'])?.toInt();
            final stream = event['stream_id']?.toString();
            if (type == 'resync_required' ||
                (_streamId != null && stream != null && stream != _streamId) ||
                (seq != null && _streamSeq != null && seq > _streamSeq! + 1)) {
              _resync();
              return;
            }
            if (seq != null && _streamSeq != null && seq <= _streamSeq!) return;
            if (seq != null) _streamSeq = seq;
            if (stream != null) _streamId = stream;
            if (type == 'config.changed') {
              final record = object(event['payload']);
              final change = object(record['payload']);
              if (change['reason'] == 'deadline_expired') {
                deadlineChanges.value = {
                  'profile_id': record['profile_id'],
                  'task_id': change['task_id'],
                  'event_id': event['event_id'],
                };
              }
            }
            if (type != 'heartbeat' && type != 'connected') {
              _scheduleLiveRefresh();
            }
          } catch (_) {
            _resync();
          }
        },
        onError: (_) => _streamLost(generation),
        onDone: () => _streamLost(generation),
      );
    } catch (_) {
      _streamLost(generation);
    }
  }

  void _streamLost(int generation) {
    if (_disposed || generation != _connectionGeneration) return;
    streamConnected = false;
    _emit();
    _reconnect?.cancel();
    _reconnect = Timer(const Duration(seconds: 4), () async {
      if (_disposed || generation != _connectionGeneration) return;
      final backend = _backendGeneration;
      await _load(overview, '/api/v2/overview');
      if (_disposed || backend != _backendGeneration) return;
      await _connectStream();
    });
  }

  Future<void> _resync() async {
    final backend = _backendGeneration;
    ++_connectionGeneration;
    streamConnected = false;
    final subscription = _subscription;
    final channel = _channel;
    _subscription = null;
    _channel = null;
    await subscription?.cancel();
    await channel?.sink.close();
    if (_disposed || backend != _backendGeneration) return;
    await _load(overview, '/api/v2/overview');
    if (_disposed || backend != _backendGeneration) return;
    await _connectStream();
  }

  void dismissMessage() {
    operationMessage = null;
    _emit();
  }

  void _emit() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    ++_connectionGeneration;
    _poll?.cancel();
    _debounce?.cancel();
    _reconnect?.cancel();
    _subscription?.cancel();
    _channel?.sink.close();
    linker.removeListener(_emit);
    linker.dispose();
    previewChanges.dispose();
    deadlineChanges.dispose();
    api.dispose();
    super.dispose();
  }
}

String formatBytes(Object? value) {
  final number = numberValue(value);
  if (number == null) return '—';
  if (number < 1024) return '$number B';
  if (number < 1024 * 1024) return '${(number / 1024).toStringAsFixed(1)} KiB';
  return '${(number / (1024 * 1024)).toStringAsFixed(1)} MiB';
}

String formatDuration(Object? value) {
  final seconds = numberValue(value)?.round();
  if (seconds == null) return '—';
  if (seconds < 60) return '$seconds 秒';
  if (seconds < 3600) return '${seconds ~/ 60} 分 ${seconds % 60} 秒';
  return '${seconds ~/ 3600} 小时 ${(seconds % 3600) ~/ 60} 分';
}
