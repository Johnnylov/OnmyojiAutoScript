import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/services.dart';
import 'package:get/get.dart';
import 'package:get_storage/get_storage.dart';
import 'package:oasx/modules/args/index.dart';
import 'package:oasx/modules/home/controllers/dashboard_controller.dart';
import 'package:oasx/modules/home/home_binding.dart';
import 'package:oasx/service/script_service.dart';
import 'package:oasx/service/websocket_service.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_linker.dart';

class MemoryStorage implements HomeDashboardStorage {
  @override
  dynamic read(String key) => null;
  @override
  void write(String key, dynamic value) {}
}

class OfflineScriptService extends ScriptService {
  // Do not run production reload/stop lifecycle in an isolated unit fixture.
  @override
  // ignore: must_call_super
  Future<void> onInit() async {}
  @override
  // ignore: must_call_super
  Future<void> onClose() async {}
}

class NoGameWebSocketService extends WebSocketService {
  int calls = 0;
  @override
  Future<WebSocketClient> connect({
    required String name,
    String? url,
    MessageListener? listener,
    bool force = false,
  }) async {
    calls++;
    throw StateError('This test must never connect to a game executor');
  }

  @override
  Future<void> send(String name, String message) async {
    calls++;
    throw StateError('This test must never send a control command');
  }
}

class LinkApi extends SolanaApi {
  LinkApi() : super(address: () => 'http://fake.invalid');
  final profiles = <JsonObject>[
    {'id': 'id-a', 'name': 'A', 'state_version': 11},
    {'id': 'id-b', 'name': 'B', 'state_version': 22},
    {'id': 'id-c', 'name': 'C', 'state_version': 33},
  ];
  final calls = <JsonObject>[];
  final reads = <String>[];
  final fail = <String>{};
  final revision = {'id-a': 1, 'id-b': 5, 'id-c': 8};
  final missingTask = <String>{};
  final unpersisted = <String>{};
  final disabledTasks = <String>{};
  JsonObject scheduler = {
    'ready': <JsonObject>[],
    'waiting': <JsonObject>[],
    'running': <JsonObject>[],
  };
  Completer<void>? gate;
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    reads.add(path);
    if (path == '/api/v2/overview') return {'profiles': profiles};
    if (path == '/api/v2/scheduler') return scheduler;
    if (path.endsWith('/args')) {
      final id = path.split('/')[4];
      final task = path.split('/')[5];
      if (missingTask.contains(id)) {
        throw const SolanaApiException('task_missing', '任务不存在', 404);
      }
      return object(
        jsonDecode(
          jsonEncode({
            'revision': '$id-r${revision[id]}',
            'args': {
              'scheduler': [
                {
                  'name': 'enable',
                  'type': 'boolean',
                  'value': !disabledTasks.contains('$id/$task'),
                },
                {
                  'name': 'next_run',
                  'type': 'date_time',
                  'value': '2030-01-01 00:00:00',
                },
              ],
              'settings': [
                {'name': 'count', 'type': 'integer', 'value': 10},
                {'name': 'enabled', 'type': 'boolean', 'value': true},
              ],
            },
          }),
        ),
      );
    }
    return {'items': <JsonObject>[]};
  }

  @override
  Future<JsonObject> request(
    String method,
    String path, {
    JsonObject? body,
    JsonObject? query,
  }) async {
    final data = Map<String, dynamic>.from(body!);
    calls.add({'path': path, ...data});
    if (gate != null) await gate!.future;
    final id = data['profile_id'] as String;
    if (fail.contains(id)) {
      throw const SolanaApiException('revision_conflict', '配置已被修改', 409);
    }
    if (path == '/api/v2/control') {
      return {
        'accepted': true,
        'executed': true,
        'persisted': !unpersisted.contains(id),
        'status': 'started',
      };
    }
    expect(data['expected_revision'], '$id-r${revision[id]}');
    revision[id] = revision[id]! + 1;
    return {
      'saved': true,
      'persisted': !unpersisted.contains(id),
      'revision': '$id-r${revision[id]}',
    };
  }
}

SolanaController controller(LinkApi api) {
  final c = SolanaController(api: api);
  c.overview.data = {'profiles': api.profiles};
  c.capabilities.data = {'api_version': 2};
  c.connected = true;
  c.selectedProfile = 'id-a';
  c.linker.setEnabled(true);
  c.linker.setSelection(['A', 'B']);
  return c;
}

Future<SolanaLinkedTaskSession> session(SolanaController c) =>
    SolanaLinkedTaskSession.load(
      api: c.api,
      requestId: SolanaController.requestId,
      sourceId: 'id-a',
      task: 'Orochi',
      targets: c.linker.snapshotFor('A'),
    );

void main() {
  test(
    'console before classic binding registers one shared real dashboard without control',
    () async {
      TestWidgetsFlutterBinding.ensureInitialized();
      Get.testMode = true;
      final directory = await Directory.systemTemp.createTemp(
        'solana-linker-test-',
      );
      const pathChannel = MethodChannel('plugins.flutter.io/path_provider');
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(pathChannel, (_) async => directory.path);
      final storage = GetStorage('GetStorage', directory.path);
      await storage.initStorage;
      final socket =
          Get.put<WebSocketService>(NoGameWebSocketService())
              as NoGameWebSocketService;
      final scripts = OfflineScriptService()
        ..addScriptModel('A')
        ..addScriptModel('B');
      Get.put<ScriptService>(scripts);
      final api = LinkApi();
      final linker = SolanaLinker(profiles: () => api.profiles);
      try {
        expect(Get.isRegistered<HomeDashboardController>(), isTrue);
        expect(Get.find<HomeDashboardController>(), same(linker.legacy));
        linker.setEnabled(true);
        linker.setSelection(['A', 'B']);
        HomeBinding().dependencies();
        final classic = Get.find<HomeDashboardController>();
        expect(classic, same(linker.legacy));
        expect(classic.linkedScopeScriptsFor('A'), ['A', 'B']);
        classic.setScriptLinked('B', false);
        expect(linker.scopeFor('A'), ['A']);
        final anotherConsole = SolanaLinker(profiles: () => api.profiles);
        expect(anotherConsole.legacy, same(classic));
        anotherConsole.dispose();
        expect(socket.calls, 0);
      } finally {
        linker.dispose();
        api.dispose();
        Get.reset();
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
            .setMockMethodCallHandler(pathChannel, null);
        try {
          await directory.delete(recursive: true);
        } on FileSystemException catch (error) {
          // GetStorage 2.x has no close API and holds its file until process
          // exit on Windows. The isolated OS-temp fixture contains no user data.
          if (!Platform.isWindows || error.osError?.errorCode != 32) rethrow;
        }
      }
    },
  );

  test(
    'bridge shares original controller state and source opt-in semantics',
    () {
      final api = LinkApi();
      final legacy = HomeDashboardController(storage: MemoryStorage());
      final linker = SolanaLinker(profiles: () => api.profiles, legacy: legacy);
      legacy.toggleLinkMode();
      linker.setSelection([' B ', 'C', 'deleted']);
      expect(legacy.linkedScriptList, ['B', 'C']);
      expect(linker.scopeFor('A'), ['A']);
      legacy.setScriptLinked('A', true);
      expect(linker.scopeFor('A'), ['A', 'B', 'C']);
      api.profiles.removeWhere((p) => p['name'] == 'C');
      expect(linker.scopeFor('A'), ['A', 'B']);
      linker.setEnabled(false);
      expect(legacy.linkedScriptList, isEmpty);
      expect(linker.scopeFor('B'), ['B']);
      linker.dispose();
      api.dispose();
    },
  );

  test(
    'form freezes scope and advances each independent revision after saves',
    () async {
      final api = LinkApi();
      final c = controller(api);
      final form = await session(c);
      c.linker.setSelection(['A', 'C']);
      expect(form.scopeNames, ['A', 'B']);
      expect(
        (await form.saveField('settings', 'count', 'integer', 12)).allSuccess,
        isTrue,
      );
      expect(
        (await form.saveField(
          'settings',
          'enabled',
          'boolean',
          false,
        )).allSuccess,
        isTrue,
      );
      expect(api.calls.map((x) => x['profile_id']), [
        'id-a',
        'id-b',
        'id-a',
        'id-b',
      ]);
      expect(api.calls.map((x) => x['expected_revision']), [
        'id-a-r1',
        'id-b-r5',
        'id-a-r2',
        'id-b-r6',
      ]);
      expect(api.calls.map((x) => x['request_id']).toSet(), hasLength(4));
      c.dispose();
    },
  );

  test(
    'every task and version must load before a linked form can write',
    () async {
      final api = LinkApi()..missingTask.add('id-b');
      final c = controller(api);
      await expectLater(
        session(c),
        throwsA(
          isA<SolanaApiException>().having(
            (e) => e.message,
            'name',
            contains('B'),
          ),
        ),
      );
      expect(api.calls, isEmpty);
      c.dispose();
    },
  );

  test(
    'real staged args preserve failed draft and never repeat successful manual effects',
    () async {
      final api = LinkApi()..fail.add('id-b');
      final c = controller(api);
      final form = await session(c);
      final args = ArgsController();
      await args.loadGroups(
        config: form.sourceName,
        task: form.task,
        stagingMode: true,
        scopeScripts: form.scopeNames,
        preloadedGroups: form.sourceArgs,
        saveArgumentOverride: (_, __, group, arg, type, value) async =>
            (await form.saveField(group, arg, type, value)).allSuccess,
      );
      args.stageArgumentChange(
        'scheduler',
        'next_run',
        '2031-01-01 00:00:00',
        'date_time',
      );
      args.stageArgumentChange('settings', 'count', 15, 'integer');
      expect(await args.saveDraftChanges(), isFalse);
      expect(args.hasDraftChanges, isTrue);
      expect(args.scopeScriptCount.value, 2);
      expect(form.lastResult!.message, contains('A：已保存'));
      expect(form.lastResult!.message, contains('B：配置已被修改'));
      expect(api.calls, hasLength(2));
      expect(await args.saveDraftChanges(), isFalse);
      expect(api.calls, hasLength(2));
      c.dispose();
    },
  );

  test(
    'saved but unpersisted stops further form submissions until reload',
    () async {
      final api = LinkApi()..unpersisted.add('id-a');
      final c = controller(api);
      final form = await session(c);
      final result = await form.saveField('settings', 'count', 'integer', 12);
      expect(result.allSuccess, isFalse);
      expect(result.message, contains('A：参数已保存'));
      expect(form.blocked, isTrue);
      await form.saveField('settings', 'count', 'integer', 13);
      expect(api.calls, hasLength(2));
      c.dispose();
    },
  );

  test(
    'safe stop reaches all targets with own state versions after first failure',
    () async {
      final api = LinkApi()..fail.add('id-a');
      final c = controller(api);
      c.connected = false; // Safety still tries the observed, versioned scope.
      await c.control('safe_stop');
      expect(api.calls.map((x) => x['profile_id']), ['id-a', 'id-b']);
      expect(api.calls.map((x) => x['expected_state_version']), [11, 22]);
      expect(c.operationMessage, contains('A：配置已被修改'));
      expect(c.operationMessage, contains('B：操作已执行'));
      expect(c.operationFailed, isTrue);
      c.dispose();
    },
  );

  test(
    'partial start cannot retry successes until explicit state reload',
    () async {
      final api = LinkApi()..fail.add('id-b');
      final c = controller(api);
      await c.control('start');
      expect(c.controlReviewRequired, isTrue);
      await c.refreshAll();
      await c.control('start');
      expect(api.calls, hasLength(2));
      await c.reloadLinkedControl();
      expect(c.controlReviewRequired, isFalse);
      c.dispose();
    },
  );

  test(
    'control also freezes scope across asynchronous network waits',
    () async {
      final api = LinkApi()..gate = Completer<void>();
      final c = controller(api);
      final operation = c.control('start');
      await Future<void>.delayed(Duration.zero);
      c.linker.setSelection(['A', 'C']);
      api.gate!.complete();
      await operation;
      expect(api.calls.map((x) => x['profile_id']), ['id-a', 'id-b']);
      c.dispose();
    },
  );

  test(
    'single immediate task freezes linked scope and only schedules the chosen task',
    () async {
      final api = LinkApi()..gate = Completer<void>();
      api.scheduler = {
        'ready': [
          for (final id in ['id-a', 'id-b', 'id-c'])
            for (final task in ['Orochi', 'MysteryShop'])
              {'profile_id': id, 'task_id': task},
        ],
        'waiting': [],
        'running': [],
      };
      final c = controller(api);
      final operation = c.quickScheduleTask('Orochi');
      await Future<void>.delayed(Duration.zero);
      c.linker.setSelection(['A', 'C']);
      await c.quickScheduleTask('MysteryShop');
      expect(api.calls, hasLength(1));
      api.gate!.complete();
      await operation;
      expect(api.calls.map((call) => '${call['profile_id']}/${call['task']}'), [
        'id-a/Orochi',
        'id-b/Orochi',
      ]);
      expect(api.calls.map((call) => call['expected_revision']), [
        'id-a-r1',
        'id-b-r5',
      ]);
      expect(
        api.calls.every(
          (call) =>
              call['path'] == '/api/v2/config/value' &&
              call['group'] == 'scheduler' &&
              call['argument'] == 'next_run' &&
              call['types'] == 'next_run',
        ),
        isTrue,
      );
      expect(c.operationMessage, contains('请点击「执行任务」启动调度器'));
      expect(c.operationFailed, isFalse);
      c.dispose();
    },
  );

  test(
    'single immediate task skips running and disabled linked targets without starting or enabling',
    () async {
      final api = LinkApi();
      api.scheduler = {
        'ready': [
          for (final id in ['id-a', 'id-b', 'id-c'])
            {'profile_id': id, 'task_id': 'Orochi'},
        ],
        'waiting': [],
        'running': [
          {'profile_id': 'id-b', 'task_id': 'Orochi'},
        ],
      };
      api.disabledTasks.add('id-c/Orochi');
      final c = controller(api);
      c.linker.setSelection(['A', 'B', 'C']);
      await c.quickScheduleTask('Orochi');
      expect(api.calls, hasLength(1));
      expect(api.calls.single['path'], '/api/v2/config/value');
      expect(api.calls.single['profile_id'], 'id-a');
      expect(api.calls.single['argument'], 'next_run');
      expect(c.operationMessage, contains('B：Orochi 已跳过'));
      expect(c.operationMessage, contains('C：Orochi 已跳过（当前未启用）'));
      c.dispose();
    },
  );

  test(
    'single task absent from source queue or already running never changes linked schedules',
    () async {
      final api = LinkApi();
      api.scheduler = {
        'ready': [
          {'profile_id': 'id-a', 'task_id': 'AlreadyRunning'},
          {'profile_id': 'id-b', 'task_id': 'AlreadyRunning'},
          {'profile_id': 'id-b', 'task_id': 'MissingFromSource'},
        ],
        'waiting': [],
        'running': [
          {'profile_id': 'id-a', 'task_id': 'AlreadyRunning'},
        ],
      };
      final c = controller(api);
      await c.quickScheduleTask('MissingFromSource');
      await c.quickScheduleTask('AlreadyRunning');
      expect(api.calls, isEmpty);
      expect(c.operationMessage, contains('没有可立即安排的已启用任务'));
      c.dispose();
    },
  );

  test(
    'single task CAS failure blocks further scheduling until explicit review without retrying success',
    () async {
      final api = LinkApi()..fail.add('id-b');
      api.scheduler = {
        'ready': [
          for (final id in ['id-a', 'id-b'])
            {'profile_id': id, 'task_id': 'Orochi'},
        ],
        'waiting': [],
        'running': [],
      };
      final c = controller(api);
      await c.quickScheduleTask('Orochi');
      expect(c.controlReviewRequired, isTrue);
      expect(c.operationMessage, contains('A：Orochi 已安排立即执行'));
      expect(c.operationMessage, contains('B：Orochi 配置已被修改'));
      await c.refreshAll();
      await c.quickScheduleTask('Orochi');
      await c.bulkQuickSchedule();
      expect(api.calls, hasLength(2));
      expect(
        api.calls.every((call) => call['path'] == '/api/v2/config/value'),
        isTrue,
      );
      c.dispose();
    },
  );

  test(
    'bulk immediate scheduling uses next_run only and advances each profile revision',
    () async {
      final api = LinkApi();
      api.scheduler = {
        'ready': [
          for (final id in ['id-a', 'id-b'])
            {'profile_id': id, 'task_id': 'Orochi'},
        ],
        'waiting': [
          for (final id in ['id-a', 'id-b'])
            {'profile_id': id, 'task_id': 'MysteryShop'},
        ],
        'running': [
          {'profile_id': 'id-a', 'task_id': 'AlreadyRunning'},
        ],
      };
      final c = controller(api);
      await c.bulkQuickSchedule();
      expect(api.calls.map((x) => x['profile_id']), [
        'id-a',
        'id-a',
        'id-b',
        'id-b',
      ]);
      expect(api.calls.map((x) => x['expected_revision']), [
        'id-a-r1',
        'id-a-r2',
        'id-b-r5',
        'id-b-r6',
      ]);
      expect(
        api.calls.every(
          (x) =>
              x['path'] == '/api/v2/config/value' &&
              x['types'] == 'next_run' &&
              x['group'] == 'scheduler' &&
              x['argument'] == 'next_run',
        ),
        isTrue,
      );
      expect(api.calls.map((x) => x['task']), [
        'Orochi',
        'MysteryShop',
        'Orochi',
        'MysteryShop',
      ]);
      expect(
        DateTime.parse(api.calls.first['value']).isBefore(DateTime.now()),
        isTrue,
      );
      expect(c.operationFailed, isFalse);
      c.dispose();
    },
  );

  test(
    'bulk skips source and target running tasks plus disabled target without enabling',
    () async {
      final api = LinkApi();
      api.scheduler = {
        'ready': [
          for (final id in ['id-a', 'id-b'])
            for (final task in ['Orochi', 'MysteryShop', 'SourceRunning'])
              {'profile_id': id, 'task_id': task},
        ],
        'waiting': [],
        'running': [
          {'profile_id': 'id-a', 'task_id': 'SourceRunning'},
          {'profile_id': 'id-b', 'task_id': 'Orochi'},
        ],
      };
      api.disabledTasks.add('id-b/MysteryShop');
      final c = controller(api);
      await c.bulkQuickSchedule();
      expect(api.calls.map((x) => '${x['profile_id']}/${x['task']}'), [
        'id-a/Orochi',
        'id-a/MysteryShop',
      ]);
      expect(c.operationMessage, contains('已跳过'));
      c.dispose();
    },
  );

  test(
    'bulk failure stops uncertain profile but tries others and forbids automatic retry',
    () async {
      final api = LinkApi()..fail.add('id-a');
      api.scheduler = {
        'ready': [
          for (final id in ['id-a', 'id-b'])
            for (final task in ['Orochi', 'MysteryShop'])
              {'profile_id': id, 'task_id': task},
        ],
        'waiting': [],
        'running': [],
      };
      final c = controller(api);
      await c.bulkQuickSchedule();
      expect(api.calls.map((x) => '${x['profile_id']}/${x['task']}'), [
        'id-a/Orochi',
        'id-b/Orochi',
        'id-b/MysteryShop',
      ]);
      expect(c.operationMessage, contains('MysteryShop 未提交'));
      expect(c.operationMessage, contains('B：MysteryShop 已安排立即执行'));
      expect(c.controlReviewRequired, isTrue);
      await c.bulkQuickSchedule();
      expect(api.calls, hasLength(3));
      c.dispose();
    },
  );

  testWidgets(
    'bulk guard survives six seconds until actual request completion',
    (tester) async {
      final api = LinkApi()..gate = Completer<void>();
      api.scheduler = {
        'ready': [
          for (final id in ['id-a', 'id-b'])
            {'profile_id': id, 'task_id': 'Orochi'},
        ],
        'waiting': [],
        'running': [],
      };
      final c = controller(api);
      final operation = c.bulkQuickSchedule();
      await tester.pump();
      expect(api.calls, hasLength(1));
      await tester.pump(const Duration(seconds: 6));
      expect(c.busy, isTrue);
      c.linker.setSelection(['A', 'C']);
      await c.bulkQuickSchedule();
      expect(api.calls, hasLength(1));
      api.gate!.complete();
      await tester.pump();
      await operation;
      expect(c.busy, isFalse);
      expect(api.calls.map((x) => x['profile_id']), ['id-a', 'id-b']);
      c.dispose();
    },
  );
}
