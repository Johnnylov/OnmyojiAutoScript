import 'package:flutter/foundation.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/home/controllers/dashboard_controller.dart';
import 'package:oasx/service/script_service.dart';

import 'solana_api.dart';
import 'solana_labels.dart';

/// Shares the old dashboard's link selection, without its legacy mutation API.
class SolanaLinker extends ChangeNotifier {
  final HomeDashboardController legacy;
  final List<JsonObject> Function() _profiles;
  late final Worker _worker;

  SolanaLinker({
    required List<JsonObject> Function() profiles,
    HomeDashboardController? legacy,
  }) : _profiles = profiles,
       legacy = legacy ?? _sharedDashboard() {
    _worker = everAll([
      this.legacy.isLinkModeEnabled,
      this.legacy.linkedScriptList,
    ], (_) => notifyListeners());
  }

  bool get enabled => legacy.isLinkModeEnabled.value;
  List<JsonObject> get profiles => List.unmodifiable(_profiles());
  List<String> get selectedNames {
    final available = profiles.map((p) => textValue(p['name'], '')).toSet();
    return legacy.linkedScriptList.where(available.contains).toSet().toList()
      ..sort();
  }

  bool isSelected(String name) => selectedNames.contains(name.trim());
  void setEnabled(bool value) {
    if (value != enabled) legacy.toggleLinkMode();
  }

  void setScriptLinked(String name, bool selected) {
    if (!profiles.any((p) => p['name'] == name.trim())) return;
    legacy.setScriptLinked(name, selected);
  }

  void setSelection(Iterable<String> names) {
    if (!enabled) return;
    final next = names.map((name) => name.trim()).toSet();
    for (final name in legacy.linkedScriptList.toList()) {
      if (!next.contains(name)) legacy.setScriptLinked(name, false);
    }
    for (final name in next) {
      setScriptLinked(name, true);
    }
  }

  List<String> scopeFor(String sourceName) {
    final source = sourceName.trim();
    if (source.isEmpty) return const [];
    // Same cascade rule as HomeDashboardLinkingX: source must be checked too.
    return List.unmodifiable(
      enabled && isSelected(source) ? selectedNames : [source],
    );
  }

  List<JsonObject> snapshotFor(String sourceName) {
    final available = {
      for (final profile in profiles) profile['name']: profile,
    };
    return List.unmodifiable(
      scopeFor(sourceName).map((name) {
        final profile = available[name];
        if (profile == null || profile['id'] == null) {
          throw SolanaApiException(
            'profile_missing',
            '$name 配置已不存在，请重新加载。',
            409,
          );
        }
        return Map<String, dynamic>.unmodifiable(profile);
      }),
    );
  }

  @override
  void dispose() {
    _worker.dispose();
    // The permanent legacy controller belongs to the app, not this bridge.
    super.dispose();
  }
}

HomeDashboardController _sharedDashboard() {
  if (Get.isRegistered<HomeDashboardController>()) {
    return Get.find<HomeDashboardController>();
  }
  if (Get.isRegistered<ScriptService>()) {
    // Main normally enters through HomeBinding. Also cover a directly embedded
    // console after initService, so a later classic HomeBinding reuses the same
    // permanent controller. onInit only loads layout/selection and observes the
    // already registered ScriptService; it does not start an executor.
    return Get.put<HomeDashboardController>(
      HomeDashboardController(),
      permanent: true,
    );
  }
  // Standalone previews/tests do not initialize app storage or game services.
  return HomeDashboardController(storage: _VolatileStorage());
}

class _VolatileStorage implements HomeDashboardStorage {
  @override
  dynamic read(String key) => null;
  @override
  void write(String key, dynamic value) {}
}

class SolanaTargetResult {
  final String profileId;
  final String name;
  final bool success;
  final bool needsReview;
  final String message;
  const SolanaTargetResult(
    this.profileId,
    this.name,
    this.success,
    this.message, {
    this.needsReview = false,
  });
}

class SolanaBatchResult {
  final List<SolanaTargetResult> items;
  SolanaBatchResult(Iterable<SolanaTargetResult> items)
    : items = List.unmodifiable(items);
  bool get allSuccess =>
      items.isNotEmpty && items.every((item) => item.success);
  bool get needsReview =>
      items.any((item) => item.needsReview || !item.success);
  String get message =>
      items.map((item) => '${item.name}：${item.message}').join('；');
}

/// Frozen scheduling plan. Only next_run is edited, never enable/control.
Future<SolanaBatchResult> scheduleLinkedTasksNow({
  required SolanaApi api,
  required String Function() requestId,
  required String sourceId,
  required List<JsonObject> targets,
  Iterable<String>? chosenTasks,
  DateTime? now,
}) async {
  final frozen = targets
      .map((p) => Map<String, dynamic>.unmodifiable(p))
      .toList(growable: false);
  final chosen = chosenTasks == null
      ? null
      : Set<String>.unmodifiable(chosenTasks.map((task) => task.trim()));
  final snapshot = await api.get('/api/v2/scheduler');
  String taskName(JsonObject row) =>
      textValue(row['task_id'] ?? row['task'] ?? row['name'], '');
  String pair(String id, String task) => '$id/$task';
  Set<String> running(JsonObject data) => objects(
    data['running'],
  ).map((row) => pair(textValue(row['profile_id'], ''), taskName(row))).toSet();
  final active = running(snapshot);
  final queued = [
    ...objects(snapshot['ready']),
    ...objects(snapshot['waiting']),
  ];
  final sourceTasks = queued
      .where((row) => row['profile_id'] == sourceId)
      .map(taskName)
      .where(
        (task) =>
            task.isNotEmpty &&
            (chosen == null || chosen.contains(task)) &&
            !active.contains(pair(sourceId, task)),
      )
      .toSet()
      .toList(growable: false);
  final eligible = queued
      .map((row) => pair(textValue(row['profile_id'], ''), taskName(row)))
      .toSet();
  final results = <SolanaTargetResult>[];
  final instant = (now ?? DateTime.now()).subtract(const Duration(days: 1));
  String pad(int value) => value.toString().padLeft(2, '0');
  final nextRun =
      '${instant.year}-${pad(instant.month)}-${pad(instant.day)} '
      '${pad(instant.hour)}:${pad(instant.minute)}:${pad(instant.second)}';
  for (final target in frozen) {
    final id = target['id'] as String;
    final name = target['name'] as String;
    Object? revision;
    var blocked = false;
    if (sourceTasks.isEmpty) {
      results.add(SolanaTargetResult(id, name, true, '没有可立即安排的已启用任务'));
    }
    for (final task in sourceTasks) {
      final key = pair(id, task);
      if (!eligible.contains(key) || active.contains(key)) {
        results.add(
          SolanaTargetResult(
            id,
            name,
            true,
            '${taskLabel(task)} 已跳过（未启用、不存在或正在运行）',
          ),
        );
        continue;
      }
      if (blocked) {
        results.add(
          SolanaTargetResult(
            id,
            name,
            false,
            '${taskLabel(task)} 未提交（前一项需核验）',
            needsReview: true,
          ),
        );
        continue;
      }
      try {
        final form = await api.get(
          '/api/v2/config/${Uri.encodeComponent(id)}/${Uri.encodeComponent(task)}/args',
        );
        final fields = objects(object(form['args'])['scheduler']);
        final enable = fields
            .where((field) => field['name'] == 'enable')
            .firstOrNull?['value'];
        if (enable != true && enable != 1 && enable != 'true') {
          results.add(
            SolanaTargetResult(id, name, true, '${taskLabel(task)} 已跳过（当前未启用）'),
          );
          continue;
        }
        if (!fields.any((field) => field['name'] == 'next_run')) {
          results.add(
            SolanaTargetResult(
              id,
              name,
              true,
              '${taskLabel(task)} 已跳过（没有调度时间字段）',
            ),
          );
          continue;
        }
        if (form['revision'] == null) {
          throw const SolanaApiException('invalid_response', '缺少配置版本');
        }
        if (revision != null && revision != form['revision']) {
          throw const SolanaApiException(
            'revision_conflict',
            '配置在批处理期间被修改',
            409,
          );
        }
        revision ??= form['revision'];
        // Recheck observed running state immediately before each mutation.
        // A subsequent concurrent file change is still rejected by CAS.
        if (running(await api.get('/api/v2/scheduler')).contains(key)) {
          results.add(
            SolanaTargetResult(id, name, true, '${taskLabel(task)} 已跳过（正在运行）'),
          );
          continue;
        }
        final result = await api.request(
          'PUT',
          '/api/v2/config/value',
          body: {
            'profile_id': id,
            'task': task,
            'group': 'scheduler',
            'argument': 'next_run',
            'types': 'next_run',
            'value': nextRun,
            'expected_revision': revision,
            'request_id': requestId(),
          },
        );
        final saved = result['saved'] == true && result['revision'] != null;
        final confirmed =
            saved &&
            result['persisted'] != false &&
            result['needs_reconciliation'] != true;
        if (saved) revision = result['revision'];
        blocked = !confirmed;
        results.add(
          SolanaTargetResult(
            id,
            name,
            confirmed,
            '${taskLabel(task)} ${confirmed
                ? '已安排立即执行'
                : saved
                ? '时间已保存，操作结果待核验'
                : '结果未确认'}',
            needsReview: !confirmed,
          ),
        );
      } catch (error) {
        blocked = true;
        results.add(
          SolanaTargetResult(
            id,
            name,
            false,
            '${taskLabel(task)} ${error is SolanaApiException ? error.message : '操作结果未确认'}',
            needsReview: true,
          ),
        );
      }
    }
  }
  return SolanaBatchResult(results);
}

/// A form owns a frozen set of identities, task schemas and CAS revisions.
/// Link selection changes never modify this object or expand an existing draft.
class SolanaLinkedTaskSession {
  final SolanaApi api;
  final String Function() requestId;
  final String sourceId;
  final String sourceName;
  final String task;
  final List<JsonObject> targets;
  final Map<String, JsonObject> _forms;
  final Map<String, Object> _revisions;
  bool blocked = false;
  bool _saving = false;
  SolanaBatchResult? lastResult;

  SolanaLinkedTaskSession._(
    this.api,
    this.requestId,
    this.sourceId,
    this.sourceName,
    this.task,
    this.targets,
    this._forms,
    this._revisions,
  );

  List<String> get scopeNames =>
      List.unmodifiable(targets.map((p) => p['name'] as String));
  JsonObject get sourceArgs => object(_forms[sourceId]?['args']);

  static Future<SolanaLinkedTaskSession> load({
    required SolanaApi api,
    required String Function() requestId,
    required String sourceId,
    required String task,
    required List<JsonObject> targets,
  }) async {
    final frozen = List<JsonObject>.unmodifiable(
      targets.map((p) => Map<String, dynamic>.unmodifiable(p)),
    );
    final source = frozen.where((p) => p['id'] == sourceId).firstOrNull;
    if (source == null) {
      throw const SolanaApiException('scope_invalid', '当前配置不在参数作用范围内，请重新加载。');
    }
    final forms = <String, JsonObject>{};
    final revisions = <String, Object>{};
    for (final target in frozen) {
      final id = target['id'] as String;
      final name = target['name'] as String;
      try {
        final response = await api.get(
          '/api/v2/config/${Uri.encodeComponent(id)}/${Uri.encodeComponent(task)}/args',
        );
        if (response['revision'] == null ||
            response['args'] is! Map ||
            object(response['args']).isEmpty) {
          throw const SolanaApiException('invalid_response', '任务参数或配置版本不可用');
        }
        forms[id] = response;
        revisions[id] = response['revision'];
      } catch (error) {
        throw SolanaApiException(
          'linked_load_failed',
          '$name：${error is SolanaApiException ? error.message : '任务参数加载失败'}。未提交任何修改，请重新加载。',
        );
      }
    }
    return SolanaLinkedTaskSession._(
      api,
      requestId,
      sourceId,
      source['name'] as String,
      task,
      frozen,
      forms,
      revisions,
    );
  }

  Future<SolanaBatchResult> saveField(
    String group,
    String argument,
    String type,
    dynamic value,
  ) async {
    if (blocked || _saving) {
      return lastResult ??
          SolanaBatchResult([
            SolanaTargetResult(
              sourceId,
              sourceName,
              false,
              '请先重新加载核验，未重复提交。',
              needsReview: true,
            ),
          ]);
    }
    _saving = true;
    final results = <SolanaTargetResult>[];
    try {
      for (final target in targets) {
        final id = target['id'] as String;
        final name = target['name'] as String;
        try {
          final groups = object(_forms[id]?['args']);
          final members = groups[group];
          if (members is! List ||
              !members.whereType<Map>().any(
                (m) => m['name'] == argument || m['title'] == argument,
              )) {
            throw const SolanaApiException(
              'field_missing',
              '该配置没有对应字段，未提交',
              409,
            );
          }
          final result = await api.request(
            'PUT',
            '/api/v2/config/value',
            body: {
              'profile_id': id,
              'task': task,
              'group': group,
              'argument': argument,
              'types': type,
              'value': value,
              'expected_revision': _revisions[id],
              'request_id': requestId(),
            },
          );
          final saved = result['saved'] == true && result['revision'] != null;
          if (saved) _revisions[id] = result['revision'];
          final confirmed =
              saved &&
              result['persisted'] != false &&
              result['needs_reconciliation'] != true;
          results.add(
            SolanaTargetResult(
              id,
              name,
              confirmed,
              confirmed
                  ? '已保存'
                  : saved
                  ? '参数已保存，操作记录或副作用待核验'
                  : '保存结果未确认',
              needsReview: !confirmed,
            ),
          );
        } catch (error) {
          results.add(
            SolanaTargetResult(
              id,
              name,
              false,
              error is SolanaApiException
                  ? '${error.message}（${error.code}）'
                  : '保存结果未确认',
              needsReview: true,
            ),
          );
        }
      }
      lastResult = SolanaBatchResult(results);
      if (!lastResult!.allSuccess) blocked = true;
      return lastResult!;
    } finally {
      _saving = false;
    }
  }
}
