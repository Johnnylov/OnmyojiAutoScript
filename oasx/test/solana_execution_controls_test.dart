import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:oasx/solana/solana_tasks.dart';
import 'package:oasx/solana/solana_widgets.dart';

import 'solana_console_test.dart' show FixtureApi, fixtureController;

const _capture = bool.fromEnvironment('SOLANA_CAPTURE');
const _primary = ValueKey('execution-primary');

class ExecutionApi extends FixtureApi {
  String state;
  bool executorAlive;
  bool uncertain;
  int version = 7;
  Completer<JsonObject>? overviewGate;
  JsonObject resolution = {
    'executed': true,
    'persisted': true,
    'status': 'resolved',
  };

  ExecutionApi({
    this.state = 'inactive',
    this.executorAlive = false,
    this.uncertain = false,
  });

  JsonObject get retainedRun => {
    'run_id': 'failed-run',
    'profile_id': 'profile-1',
    'task_id': 'Orochi',
    'state': 'needs_reconciliation',
    'executor_alive': false,
  };

  JsonObject get snapshot => {
    'profiles': [
      {
        'id': 'profile-1',
        'name': '测试配置',
        'state': state,
        'executor_alive': executorAlive,
        'state_version': version,
      },
    ],
    'current_runs': uncertain ? [retainedRun] : <JsonObject>[],
    'dispatch_blocked': false,
    'today': <String, dynamic>{},
  };

  JsonObject get schedule => {
    'policy': {'mode': 'legacy', 'batch_seconds': 120, 'weights': {}},
    'running': <JsonObject>[],
    'ready': <JsonObject>[],
    'waiting': [
      {'profile_id': 'profile-1', 'task_id': 'Orochi'},
      {'profile_id': 'profile-1', 'task_id': 'Trifles'},
    ],
    'decisions': <JsonObject>[],
  };

  List<JsonObject> get writes =>
      calls.where((call) => call['method'] != null).toList();

  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    if (path == '/api/v2/overview' && overviewGate != null) {
      calls.add({'path': path, 'query': query});
      return overviewGate!.future;
    }
    final JsonObject? result = switch (path) {
      '/api/v2/overview' => snapshot,
      '/api/v2/scheduler' => schedule,
      '/api/v2/recovery' => {
        'items': uncertain ? [retainedRun] : <JsonObject>[],
      },
      '/api/v2/recovery/operations' => {'items': <JsonObject>[]},
      '/api/v2/config/profile-1/Orochi/args' => {
        'revision': 'task-revision-1',
        'args': {
          'scheduler': [
            {'name': 'enable', 'type': 'boolean', 'value': true},
            {
              'name': 'next_run',
              'type': 'date_time',
              'value': '2026-09-26 12:00:00',
            },
          ],
        },
      },
      _ => null,
    };
    if (result == null) return super.get(path, query: query);
    calls.add({'path': path, 'query': query});
    return result;
  }

  @override
  Future<JsonObject> request(
    String method,
    String path, {
    JsonObject? body,
    JsonObject? query,
  }) async {
    calls.add({'method': method, 'path': path, 'body': body, 'query': query});
    if (path == '/api/v2/recovery/resolve') {
      if (resolution['executed'] == true &&
          resolution['persisted'] == true &&
          resolution['status'] == 'resolved') {
        uncertain = false;
        state = 'inactive';
        version = 19;
      }
      return resolution;
    }
    if (path == '/api/v2/config/value') {
      return {'saved': true, 'persisted': true, 'revision': 'task-revision-2'};
    }
    return {
      'accepted': true,
      'executed': true,
      'persisted': true,
      'status': 'completed',
    };
  }
}

SolanaController controllerFor(ExecutionApi api) => fixtureController(api: api)
  ..overview.data = api.snapshot
  ..scheduler.data = api.schedule;

Future<void> mountShell(
  WidgetTester tester,
  SolanaController controller, {
  GlobalKey? screenshotKey,
}) async {
  await tester.binding.setSurfaceSize(const Size(1124, 751));
  addTearDown(() async {
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
    await tester.binding.setSurfaceSize(null);
  });
  await tester.pumpWidget(
    MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: solanaTheme(Brightness.light),
      home: RepaintBoundary(
        key: screenshotKey,
        child: SolanaShell(
          controller: controller,
          autoStart: false,
          terminalBuilder: (_) => const Center(child: Text('测试日志：任务异常退出')),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
  expect(tester.takeException(), isNull);
}

FilledButton primaryButton(WidgetTester tester) =>
    tester.widget<FilledButton>(find.byKey(_primary));

void main() {
  setUpAll(() async {
    if (!_capture) return;
    final font = File(r'C:\Windows\Fonts\msyh.ttc');
    expect(font.existsSync(), isTrue, reason: 'Visual capture requires YaHei.');
    await (FontLoader('Microsoft YaHei')
          ..addFont(Future.value(ByteData.sublistView(font.readAsBytesSync()))))
        .load();
    await (FontLoader(
      'MaterialIcons',
    )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
  });

  testWidgets('inactive profile has a visible enabled execute task button', (
    tester,
  ) async {
    final api = ExecutionApi();
    await mountShell(tester, controllerFor(api));
    expect(find.text('执行任务').hitTestable(), findsOneWidget);
    expect(primaryButton(tester).onPressed, isNotNull);
    expect(
      primaryButton(tester).style?.textStyle?.resolve({})?.fontFamily,
      'Microsoft YaHei',
    );
    expect(find.text('暂停'), findsNothing);
    expect(api.writes, isEmpty);
    await tester.tap(find.byKey(_primary));
    await tester.pumpAndSettle();
    expect(api.writes.single['path'], '/api/v2/control');
    expect(object(api.writes.single['body'])['action'], 'start');
  });

  testWidgets('retained uncertain run offers retry instead of a pause button', (
    tester,
  ) async {
    final api = ExecutionApi(state: 'warning', uncertain: true);
    final key = GlobalKey();
    await mountShell(tester, controllerFor(api), screenshotKey: key);
    expect(find.text('重新运行').hitTestable(), findsOneWidget);
    expect(primaryButton(tester).onPressed, isNotNull);
    expect(find.text('暂停'), findsNothing);
    expect(find.byTooltip('安全暂停'), findsNothing);
    expect(api.writes, isEmpty);
    if (_capture) {
      await tester.runAsync(() async {
        final boundary =
            key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
        final image = await boundary.toImage(pixelRatio: 1);
        final data = await image.toByteData(format: ui.ImageByteFormat.png);
        final directory = Directory('build/solana-controls')
          ..createSync(recursive: true);
        await File(
          '${directory.path}/error-retry.png',
        ).writeAsBytes(data!.buffer.asUint8List());
        image.dispose();
      });
    }
  });

  for (final entry in [
    ('running', '暂停', 'pause'),
    ('paused', '继续运行', 'resume'),
  ]) {
    testWidgets(
      '${entry.$1} sends ${entry.$3} through the single primary action',
      (tester) async {
        final api = ExecutionApi(state: entry.$1, executorAlive: true);
        await mountShell(tester, controllerFor(api));
        expect(find.text(entry.$2).hitTestable(), findsOneWidget);
        expect(find.byTooltip('安全暂停'), findsNothing);
        await tester.tap(find.byKey(_primary));
        await tester.pumpAndSettle();
        expect(api.writes.single['path'], '/api/v2/control');
        expect(object(api.writes.single['body'])['action'], entry.$3);
        expect(object(api.writes.single['body'])['expected_state_version'], 7);
        expect(tester.takeException(), isNull);
      },
    );
  }

  testWidgets('pausing executor cannot receive duplicate primary control', (
    tester,
  ) async {
    final api = ExecutionApi(state: 'pausing', executorAlive: true);
    await mountShell(tester, controllerFor(api));
    expect(find.text('正在暂停…'), findsOneWidget);
    expect(primaryButton(tester).onPressed, isNull);
    await tester.tap(find.byKey(_primary));
    await tester.pumpAndSettle();
    expect(api.writes, isEmpty);
  });

  testWidgets('offline profile keeps execute visible but disabled', (
    tester,
  ) async {
    final api = ExecutionApi();
    final controller = controllerFor(api)..connected = false;
    await mountShell(tester, controller);
    expect(find.text('执行任务'), findsOneWidget);
    expect(primaryButton(tester).onPressed, isNull);
    await tester.tap(find.byKey(_primary));
    await tester.pumpAndSettle();
    expect(api.writes, isEmpty);
  });

  testWidgets('warning without uncertain records restarts using current CAS', (
    tester,
  ) async {
    final api = ExecutionApi(state: 'warning');
    final controller = controllerFor(api);
    api.version = 13;
    await mountShell(tester, controller);
    await tester.tap(find.byKey(_primary));
    await tester.pumpAndSettle();
    expect(find.byType(AlertDialog), findsNothing);
    expect(api.writes.single['path'], '/api/v2/control');
    final body = object(api.writes.single['body']);
    expect(body['action'], 'start');
    expect(body['expected_state_version'], 13);
  });

  testWidgets('start preflight blocks duplicate start and task scheduling', (
    tester,
  ) async {
    final api = ExecutionApi()..overviewGate = Completer<JsonObject>();
    final controller = controllerFor(api);
    await mountShell(tester, controller);
    await tester.tap(find.byKey(_primary));
    await tester.pump();
    expect(controller.executionPreparing, isTrue);
    expect(primaryButton(tester).onPressed, isNull);
    final scheduleButton = tester.widget<IconButton>(
      find.ancestor(
        of: find.byTooltip('立即全部执行任务'),
        matching: find.byType(IconButton),
      ),
    );
    expect(scheduleButton.onPressed, isNull);
    await controller.quickScheduleTask('Orochi');
    await tester.tap(find.byKey(_primary));
    await tester.pump();
    expect(api.writes, isEmpty);
    api.overviewGate!.complete(api.snapshot);
    await tester.pumpAndSettle();
    expect(controller.executionPreparing, isFalse);
    expect(api.writes.single['path'], '/api/v2/control');
    expect(object(api.writes.single['body'])['action'], 'start');
  });

  testWidgets(
    'retry waits for review then resolves old run before fresh start',
    (tester) async {
      final api = ExecutionApi(state: 'warning', uncertain: true);
      await mountShell(tester, controllerFor(api));
      await tester.tap(find.byKey(_primary));
      await tester.pumpAndSettle();
      expect(find.byType(AlertDialog), findsOneWidget);
      final confirm = find.widgetWithText(FilledButton, '结束旧记录并重新运行');
      expect(tester.widget<FilledButton>(confirm).onPressed, isNull);
      expect(api.writes, isEmpty);
      await tester.tap(find.text('已检查游戏进度'));
      await tester.pump();
      expect(
        api.writes,
        isEmpty,
        reason: 'Checking progress alone cannot submit.',
      );
      await tester.tap(confirm);
      await tester.pumpAndSettle();
      expect(api.writes.map((call) => call['path']).toList(), [
        '/api/v2/recovery/resolve',
        '/api/v2/control',
      ]);
      final resolution = object(api.writes[0]['body']);
      expect(resolution['run_id'], 'failed-run');
      expect(resolution['resolution'], 'close_interrupted');
      expect(resolution['game_state_reviewed'], isTrue);
      final start = object(api.writes[1]['body']);
      expect(start['action'], 'start');
      expect(start['expected_state_version'], 19);
      final resolvedAt = api.calls.indexOf(api.writes[0]);
      final startedAt = api.calls.indexOf(api.writes[1]);
      expect(
        api.calls
            .sublist(resolvedAt + 1, startedAt)
            .any((call) => call['path'] == '/api/v2/overview'),
        isTrue,
        reason: 'Refresh state after resolving the old record.',
      );
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('cancelling recovery performs no writes or start', (
    tester,
  ) async {
    final api = ExecutionApi(state: 'warning', uncertain: true);
    await mountShell(tester, controllerFor(api));
    await tester.tap(find.byKey(_primary));
    await tester.pumpAndSettle();
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(api.writes, isEmpty);
    expect(primaryButton(tester).onPressed, isNotNull);
  });

  for (final response in [
    {'executed': false, 'persisted': true, 'status': 'resolved'},
    {'executed': true, 'persisted': false, 'status': 'resolved'},
    {'executed': true, 'persisted': true, 'status': 'requested'},
  ]) {
    testWidgets('unconfirmed recovery $response never starts executor', (
      tester,
    ) async {
      final api = ExecutionApi(state: 'warning', uncertain: true)
        ..resolution = response;
      final controller = controllerFor(api);
      await mountShell(tester, controller);
      await tester.tap(find.byKey(_primary));
      await tester.pumpAndSettle();
      await tester.tap(find.text('已检查游戏进度'));
      await tester.pump();
      await tester.tap(find.widgetWithText(FilledButton, '结束旧记录并重新运行'));
      await tester.pumpAndSettle();
      expect(api.writes.single['path'], '/api/v2/recovery/resolve');
      expect(controller.operationFailed, isTrue);
      expect(controller.operationMessage, contains('未重新启动'));
    });
  }

  testWidgets(
    'task form restores immediate execute for just the selected task',
    (tester) async {
      Get.testMode = true;
      final api = ExecutionApi();
      final controller = controllerFor(api);
      await tester.binding.setSurfaceSize(const Size(1000, 720));
      addTearDown(() async {
        await tester.pumpWidget(const SizedBox());
        controller.dispose();
        Get.reset();
        await tester.binding.setSurfaceSize(null);
      });
      await tester.pumpWidget(
        MaterialApp(
          theme: solanaTheme(Brightness.light),
          home: Scaffold(
            body: SolanaTasks(
              controller: controller,
              initialTask: 'Orochi',
              showCatalog: false,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('task-execute-now')).hitTestable(),
        findsOneWidget,
      );
      await tester.tap(find.byKey(const ValueKey('task-execute-now')));
      await tester.pumpAndSettle();
      expect(api.writes.single['path'], '/api/v2/config/value');
      final body = object(api.writes.single['body']);
      expect(body['task'], 'Orochi');
      expect(body['argument'], 'next_run');
      expect(body['expected_revision'], 'task-revision-1');
      expect(tester.takeException(), isNull);
    },
  );
}
