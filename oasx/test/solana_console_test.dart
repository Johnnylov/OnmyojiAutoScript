import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:oasx/solana/solana_widgets.dart';
import 'package:oasx/solana/solana_settings.dart';

class FixtureApi extends SolanaApi {
  FixtureApi() : super(address: () => 'http://127.0.0.1:22288');
  final calls = <JsonObject>[];
  JsonObject controlResult = {
    'accepted': true,
    'executed': false,
    'persisted': true,
    'status': 'waiting_boundary',
  };
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    calls.add({'path': path, 'query': query});
    return switch (path) {
      '/api/v2/overview' => fixtureOverview,
      '/api/v2/scheduler' => fixtureScheduler,
      '/api/v2/statistics' => fixtureStatistics,
      '/api/v2/storage' => fixtureStorage,
      '/script_menu' => {
        '日常任务': ['Trifles', 'Guild'],
        '资源收集': ['Orochi', 'Exploration'],
      },
      '/api/v2/config/profile-1/Orochi/args' => {
        'revision': 'revision-1',
        'args': {
          '调度设置': [
            {
              'name': '启用任务',
              'type': 'boolean',
              'value': true,
              'description': '按已保存的计划安排执行',
            },
            {
              'name': '下次运行',
              'type': 'date_time',
              'value': '2026-09-24 12:00:00',
            },
          ],
          '御魂设置': [
            {
              'name': '目标场次',
              'type': 'integer',
              'value': 30,
              'minimum': 1,
              'maximum': 999,
            },
            {
              'name': '副本层数',
              'type': 'enum',
              'value': '魂土',
              'enumEnum': ['魂十', '魂土'],
            },
            {'name': '独立作战', 'type': 'boolean', 'value': true},
          ],
        },
      },
      _ => {'items': <JsonObject>[], 'next_cursor': null},
    };
  }

  @override
  Future<JsonObject> request(
    String method,
    String path, {
    JsonObject? body,
    JsonObject? query,
  }) async {
    calls.add({'method': method, 'path': path, 'body': body, 'query': query});
    return controlResult;
  }
}

class PreviewRaceApi extends FixtureApi {
  final pending = <Completer<JsonObject>>[];
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) {
    if (path == '/api/v2/preview') {
      final completer = Completer<JsonObject>();
      pending.add(completer);
      return completer.future;
    }
    return super.get(path, query: query);
  }
}

final fixtureOverview = <String, dynamic>{
  'profiles': [
    {'id': 'profile-1', 'name': '测试配置', 'state': 'running', 'state_version': 7},
  ],
  'current_runs': [
    {
      'run_id': 'run-1',
      'profile_id': 'profile-1',
      'task_id': 'Orochi',
      'device_id': 'device-1',
      'state': 'running',
      'duration_seconds': 90,
    },
  ],
  'today': {
    'started': 3,
    'succeeded': 1,
    'failed': 1,
    'cancelled': 1,
    'interrupted': 0,
    'crashed': 0,
    'device_seconds': 600,
  },
  'stream_id': 'stream-1',
  'stream_seq': 10,
};
final fixtureScheduler = <String, dynamic>{
  'policy': {'mode': 'eevdf_shadow', 'batch_seconds': 120, 'weights': {}},
  'running': fixtureOverview['current_runs'],
  'ready': <JsonObject>[],
  'waiting': <JsonObject>[],
  'decisions': <JsonObject>[],
};
final fixtureStatistics = <String, dynamic>{
  'totals': fixtureOverview['today'],
  'days': [
    {'date': '2026-09-24', 'totals': fixtureOverview['today']},
  ],
  'timezone': 'Asia/Shanghai',
  'incomplete': false,
  'gaps': [],
};
final fixtureStorage = <String, dynamic>{
  'state': 'healthy',
  'total_bytes': 2048,
  'bytes': {
    'events': 1024,
    'summaries': 512,
    'checkpoints': 512,
    'meta': 0,
    'temp': 0,
  },
  'policy': {
    'mode': 'standard',
    'event_retention_days': 14,
    'summary_retention_days': 180,
    'normal_budget_bytes': 67108864,
    'temporary_reserve_bytes': 16777216,
    'timezone': 'Asia/Shanghai',
  },
};

SolanaController fixtureController({FixtureApi? api, bool data = true}) {
  final c = SolanaController(api: api ?? FixtureApi());
  if (data) {
    c.capabilities.data = {'api_version': 2};
    c.overview.data = fixtureOverview;
    c.scheduler.data = fixtureScheduler;
    c.statistics.data = fixtureStatistics;
    c.storage.data = fixtureStorage;
    c.runs.data = {'items': <JsonObject>[]};
    c.audit.data = {'items': <JsonObject>[]};
    c.selectedProfile = 'profile-1';
    c.connected = true;
  }
  return c;
}

void main() {
  testWidgets(
    'scheduler explains every mode and distinguishes a preserved draft from the current policy',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(620, 800));
      final api = FixtureApi();
      final c = fixtureController(api: api);
      Widget editor(String mode) => MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: SchedulerPolicyEditor(
              controller: c,
              policy: {
                'mode': mode,
                'batch_seconds': 120.0,
                'weights': const <String, dynamic>{},
              },
              tasks: const [],
            ),
          ),
        ),
      );
      await tester.pumpWidget(editor('legacy'));
      for (final mode in ['legacy', 'eevdf_shadow', 'eevdf']) {
        expect(
          find.byKey(ValueKey('scheduler-mode-help-$mode')),
          findsOneWidget,
        );
      }
      expect(find.textContaining('当前策略：原有策略'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('scheduler-policy-draft')),
        findsNothing,
      );
      await tester.tap(find.text('公平调度 · 试算'));
      await tester.pump();
      expect(find.textContaining('当前策略：原有策略'), findsOneWidget);
      expect(find.textContaining('未应用修改：公平调度 · 试算'), findsOneWidget);
      expect(api.calls, isEmpty);
      // A server refresh must not silently replace a user's unsubmitted choice.
      await tester.pumpWidget(editor('eevdf'));
      final trial = tester.widget<ChoiceChip>(
        find.ancestor(
          of: find.text('公平调度 · 试算'),
          matching: find.byType(ChoiceChip),
        ),
      );
      expect(trial.selected, isTrue);
      expect(find.textContaining('当前策略：公平调度'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('scheduler-policy-draft')),
        findsOneWidget,
      );
      await tester.pumpWidget(editor('eevdf_shadow'));
      expect(
        find.byKey(const ValueKey('scheduler-policy-draft')),
        findsNothing,
      );
      await tester.pumpWidget(editor('legacy'));
      expect(
        tester
            .widget<ChoiceChip>(
              find.ancestor(
                of: find.text('原有策略'),
                matching: find.byType(ChoiceChip),
              ),
            )
            .selected,
        isTrue,
      );
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
      await tester.binding.setSurfaceSize(null);
    },
  );

  testWidgets('scheduler accepts floating-point batch value from live API', (
    tester,
  ) async {
    final api = FixtureApi();
    final c = fixtureController(api: api);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SchedulerPolicyEditor(
            controller: c,
            policy: const {
              'mode': 'legacy',
              'batch_seconds': 120.0,
              'weights': <String, dynamic>{},
            },
            tasks: const [],
          ),
        ),
      ),
    );
    await tester.tap(find.text('应用调度策略'));
    await tester.pumpAndSettle();
    final call = api.calls.firstWhere(
      (call) => call['path'] == '/api/v2/scheduler/policy',
    );
    expect(object(call['body'])['batch_seconds'], 120.0);
    expect(c.operationFailed, isFalse);
    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });
  test(
    'preview from a previous profile cannot replace the current profile image',
    () async {
      final api = PreviewRaceApi();
      final c = fixtureController(api: api);
      final oldRequest = c.refreshPreview();
      await c.selectProfile('profile-2');
      final newRequest = c.refreshPreview();
      api.pending[1].complete({'available': true, 'frame_id': 'new-device'});
      await newRequest;
      api.pending[0].complete({'available': true, 'frame_id': 'old-device'});
      await oldRequest;
      expect(c.preview.data?['frame_id'], 'new-device');
      expect(c.preview.loading, isFalse);
      c.dispose();
    },
  );

  test('recovery resolution requires an explicit reviewed state', () async {
    final api = FixtureApi();
    final c = fixtureController(api: api);
    await c.resolveRecovery('run-1', gameStateReviewed: false);
    expect(api.calls, isEmpty);
    await c.resolveRecovery('run-1', gameStateReviewed: true);
    final body = object(
      api.calls.firstWhere(
        (call) => call['path'] == '/api/v2/recovery/resolve',
      )['body'],
    );
    expect(body['game_state_reviewed'], isTrue);
    expect(body['resolution'], 'close_interrupted');
    expect(body['profile_id'], 'profile-1');
    c.dispose();
  });

  test(
    'operation recovery requires consent and preserves revision and retry identity',
    () async {
      final api = FixtureApi();
      final c = fixtureController(api: api);
      final operation = <String, dynamic>{
        'kind': 'config_requests',
        'key': 'pending-1',
        'expected_revisions': {'oas1': 'revision-1'},
        'resolution_request_id': '4b234d1f-2088-4c1c-bd75-a07b202473c3',
        'resolvable': true,
      };
      await c.resolveOperation(operation, reviewed: false);
      await c.resolveOperation({
        ...operation,
        'resolvable': false,
      }, reviewed: true);
      expect(api.calls, isEmpty);
      await c.resolveOperation(operation, reviewed: true);
      final body = object(
        api.calls.firstWhere(
          (call) => call['path'] == '/api/v2/recovery/operations/resolve',
        )['body'],
      );
      expect(body, {
        'kind': 'config_requests',
        'key': 'pending-1',
        'expected_revisions': {'oas1': 'revision-1'},
        'reviewed': true,
        'request_id': operation['resolution_request_id'],
      });
      expect(c.operationMessage, contains('未重放'));
      c.dispose();
    },
  );

  test(
    'a confirmed failed control is not described as waiting for completion',
    () async {
      final api = FixtureApi()
        ..controlResult = {
          'accepted': true,
          'executed': false,
          'persisted': true,
          'status': 'failed',
        };
      final c = fixtureController(api: api);
      await c.control('start');
      expect(c.operationFailed, isTrue);
      expect(c.operationMessage, contains('操作执行失败'));
      expect(c.operationMessage, isNot(contains('当前任务段结束')));
      c.dispose();
    },
  );

  test(
    'control carries UUID and observed state version; boundary wait is not completion',
    () async {
      final api = FixtureApi();
      final c = fixtureController(api: api);
      await c.control('pause');
      final request = api.calls.firstWhere(
        (call) => call['path'] == '/api/v2/control',
      );
      final body = object(request['body']);
      expect(body['expected_state_version'], 7);
      expect(body['action'], 'pause');
      expect(
        body['request_id'],
        matches(
          RegExp(
            r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
          ),
        ),
      );
      expect(c.operationMessage, contains('当前任务段结束'));
      expect(c.profile['state'], 'running');
      c.dispose();
    },
  );

  test('history filters use API date names and profile identity', () async {
    final api = FixtureApi();
    final c = fixtureController(api: api);
    await c.setRange(DateTime(2026, 9, 1), DateTime(2026, 9, 7));
    final query = object(
      api.calls.firstWhere(
        (call) => call['path'] == '/api/v2/statistics',
      )['query'],
    );
    expect(query['start_date'], '2026-09-01');
    expect(query['end_date'], '2026-09-07');
    expect(query['profile_id'], 'profile-1');
    c.dispose();
  });

  test(
    'executed but not saved is never reported as safely persisted',
    () async {
      final api = FixtureApi()
        ..controlResult = {
          'accepted': true,
          'executed': true,
          'persisted': false,
        };
      final c = fixtureController(api: api);
      await c.control('safe_stop');
      expect(c.operationMessage, contains('未保存'));
      c.dispose();
    },
  );

  testWidgets('unknown data stays unknown and control is disabled offline', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1200, 800));
    final c = fixtureController(data: false);
    await tester.pumpWidget(
      MaterialApp(
        home: SolanaShell(controller: c, autoStart: false, showCaption: false),
      ),
    );
    expect(find.text('状态未知'), findsOneWidget);
    expect(find.textContaining('未识别'), findsNWidgets(8));
    expect(find.text('0.0%'), findsNothing);
    final start = tester.widget<IconButton>(
      find
          .ancestor(
            of: find.byIcon(Icons.power_settings_new),
            matching: find.byWidgetPredicate((widget) => widget is IconButton),
          )
          .first,
    );
    expect(start.onPressed, isNull);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    c.dispose();
    await tester.binding.setSurfaceSize(null);
  });

  for (final size in [
    const Size(1200, 800),
    const Size(960, 640),
    const Size(600, 850),
  ]) {
    testWidgets('console fits ${size.width} × ${size.height}', (tester) async {
      await tester.binding.setSurfaceSize(size);
      final c = fixtureController();
      await tester.pumpWidget(
        MaterialApp(
          home: SolanaShell(
            controller: c,
            autoStart: false,
            showCaption: false,
          ),
        ),
      );
      expect(find.text('执行次数 · 所选范围'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
      await tester.binding.setSurfaceSize(null);
    });
  }

  testWidgets('statistics explicitly displays completeness gaps', (
    tester,
  ) async {
    final remote = RemoteData()
      ..data = {
        'incomplete': true,
        'gaps': [
          {'reason': 'storage_unavailable'},
        ],
      };
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: RemoteBody(remote: remote, builder: (_) => const Text('真实汇总')),
        ),
      ),
    );
    expect(find.textContaining('存在记录缺口'), findsOneWidget);
    expect(find.text('真实汇总'), findsOneWidget);
  });

  for (final factor in [1.0, 1.25, 1.5]) {
    testWidgets(
      'simulated DPR and text scale $factor keep controls reachable',
      (tester) async {
        tester.view.physicalSize = const Size(1200, 800);
        tester.view.devicePixelRatio = factor;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final c = fixtureController();
        await tester.pumpWidget(
          MaterialApp(
            builder: (context, child) => MediaQuery(
              data: MediaQuery.of(
                context,
              ).copyWith(textScaler: TextScaler.linear(factor)),
              child: child!,
            ),
            home: SolanaShell(
              controller: c,
              autoStart: false,
              showCaption: false,
            ),
          ),
        );
        await tester.pumpAndSettle();
        expect(
          find.byTooltip('立即停止：结束脚本并交还游戏操作，下次点击重新启动').hitTestable(),
          findsOneWidget,
        );
        expect(
          find.byKey(const ValueKey('execution-primary')).hitTestable(),
          findsOneWidget,
        );
        expect(find.text('暂停'), findsOneWidget);
        expect(find.byTooltip('安全暂停'), findsNothing);
        expect(find.byTooltip('立即停止').hitTestable(), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester.tap(find.byKey(const ValueKey('execution-primary')));
        await tester.pumpAndSettle();
        expect(c.operationMessage, contains('当前任务段结束'));
        expect(tester.takeException(), isNull);
        await tester.tap(find.byTooltip('运行统计'));
        await tester.pumpAndSettle();
        expect(find.text('任务成功率'), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
        c.dispose();
      },
    );
  }
}
