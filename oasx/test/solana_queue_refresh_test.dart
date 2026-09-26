import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:oasx/solana/solana_widgets.dart';

import 'solana_console_test.dart' show FixtureApi, fixtureController;

JsonObject _snapshot({List<JsonObject> ready = const []}) => {
  'policy': {'mode': 'legacy', 'batch_seconds': 120, 'weights': {}},
  'running': <JsonObject>[],
  'ready': ready,
  'waiting': <JsonObject>[],
  'decisions': <JsonObject>[],
};

JsonObject _task(String label, {String profile = 'profile-1'}) => {
  'profile_id': profile,
  'task_id': 'Orochi',
  'task_name': label,
  'next_run': '2026-09-26 12:00:00',
};

final _overview = <String, dynamic>{
  'profiles': [
    {'id': 'profile-1', 'name': 'oas1', 'state': 'inactive'},
    {'id': 'profile-2', 'name': 'oas2', 'state': 'inactive'},
  ],
  'current_runs': <JsonObject>[],
};

class _QueueApi extends FixtureApi {
  final pending = <Completer<JsonObject>>[];
  final requestedProfiles = <String?>[];

  @override
  Future<JsonObject> get(String path, {JsonObject? query}) {
    if (path == '/api/v2/overview') return Future.value(_overview);
    if (path == '/api/v2/scheduler') {
      final gate = Completer<JsonObject>();
      pending.add(gate);
      requestedProfiles.add(query?['profile_id'] as String?);
      return gate.future;
    }
    return super.get(path, query: query);
  }
}

SolanaController _controller(_QueueApi api, {JsonObject? snapshot}) =>
    fixtureController(api: api)
      ..overview.data = _overview
      ..scheduler.data = snapshot;

Future<void> _mount(WidgetTester tester, SolanaController controller) async {
  await tester.binding.setSurfaceSize(const Size(1124, 751));
  addTearDown(() async {
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
    await tester.binding.setSurfaceSize(null);
  });
  await tester.pumpWidget(
    MaterialApp(
      theme: solanaTheme(Brightness.light),
      home: SolanaShell(
        controller: controller,
        autoStart: false,
        showCaption: false,
        terminalBuilder: (_) => const SizedBox(),
      ),
    ),
  );
  await tester.pumpAndSettle();
  expect(tester.takeException(), isNull);
}

Finder get _queue =>
    find.ancestor(of: find.text('队列中'), matching: find.byType(ListView));

Finder _queueText(String text) =>
    find.descendant(of: _queue, matching: find.text(text));

void main() {
  testWidgets(
    'confirmed empty queue stays still through repeated background refreshes',
    (tester) async {
      final api = _QueueApi();
      final c = _controller(api, snapshot: _snapshot());
      await _mount(tester, c);
      final waitingPosition = tester.getTopLeft(_queueText('等待中'));
      final emptyPosition = tester.getTopLeft(_queueText('暂无待执行任务'));

      for (var cycle = 0; cycle < 3; cycle++) {
        final refreshing = c.refreshAll();
        await tester.pump();
        expect(c.scheduler.loading, isTrue);
        expect(api.pending.length, cycle + 1);
        expect(_queueText('暂无待执行任务'), findsOneWidget);
        expect(_queueText('暂无等待任务'), findsOneWidget);
        expect(_queueText('正在读取队列…'), findsNothing);
        expect(tester.getTopLeft(_queueText('等待中')), waitingPosition);
        expect(tester.getTopLeft(_queueText('暂无待执行任务')), emptyPosition);

        api.pending.last.complete(_snapshot());
        await refreshing;
        await tester.pump();
        expect(c.scheduler.loading, isFalse);
        expect(_queueText('暂无待执行任务'), findsOneWidget);
        expect(tester.getTopLeft(_queueText('等待中')), waitingPosition);
        expect(tester.takeException(), isNull);
      }
    },
  );

  testWidgets('queue retains existing tasks until an actual update arrives', (
    tester,
  ) async {
    final api = _QueueApi();
    final c = _controller(api, snapshot: _snapshot(ready: [_task('原有待执行任务')]));
    await _mount(tester, c);
    final waitingPosition = tester.getTopLeft(_queueText('等待中'));

    final refreshing = c.refreshAll();
    await tester.pump();
    expect(c.scheduler.loading, isTrue);
    expect(_queueText('原有待执行任务'), findsOneWidget);
    expect(_queueText('正在读取队列…'), findsNothing);
    expect(tester.getTopLeft(_queueText('等待中')), waitingPosition);

    api.pending.single.complete(_snapshot(ready: [_task('更新后的待执行任务')]));
    await refreshing;
    await tester.pump();
    expect(_queueText('原有待执行任务'), findsNothing);
    expect(_queueText('更新后的待执行任务'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('first scheduler request shows loading before confirmed empty', (
    tester,
  ) async {
    final api = _QueueApi();
    final c = _controller(api);
    final loading = c.refreshAll();
    await _mount(tester, c);
    expect(c.scheduler.data, isNull);
    expect(c.scheduler.loading, isTrue);
    expect(_queueText('正在读取队列…'), findsWidgets);
    expect(_queueText('暂无待执行任务'), findsNothing);

    api.pending.single.complete(_snapshot());
    await loading;
    await tester.pump();
    expect(_queueText('正在读取队列…'), findsNothing);
    expect(_queueText('暂无待执行任务'), findsOneWidget);
    expect(_queueText('暂无等待任务'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('failed background refresh preserves the last confirmed queue', (
    tester,
  ) async {
    final api = _QueueApi();
    final c = _controller(api, snapshot: _snapshot());
    await _mount(tester, c);
    final waitingPosition = tester.getTopLeft(_queueText('等待中'));

    final refreshing = c.refreshAll();
    await tester.pump();
    api.pending.single.completeError(
      const SolanaApiException('unavailable', '模拟暂时断连', 503),
    );
    await refreshing;
    await tester.pump();
    expect(c.scheduler.error?.code, 'unavailable');
    expect(_queueText('暂无待执行任务'), findsOneWidget);
    expect(_queueText('暂无等待任务'), findsOneWidget);
    expect(_queueText('队列数据暂不可用'), findsNothing);
    expect(_queueText('正在读取队列…'), findsNothing);
    expect(tester.getTopLeft(_queueText('等待中')), waitingPosition);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'changing profile clears the previous profile queue immediately',
    (tester) async {
      final api = _QueueApi();
      final c = _controller(
        api,
        snapshot: _snapshot(ready: [_task('OAS1 的待执行任务')]),
      );
      await _mount(tester, c);
      expect(_queueText('OAS1 的待执行任务'), findsOneWidget);

      final selecting = c.selectProfile('profile-2');
      await tester.pump();
      expect(c.scheduler.data, isNull);
      expect(api.requestedProfiles, ['profile-2']);
      expect(_queueText('OAS1 的待执行任务'), findsNothing);
      expect(_queueText('正在读取队列…'), findsWidgets);

      api.pending.single.complete(
        _snapshot(ready: [_task('OAS2 的待执行任务', profile: 'profile-2')]),
      );
      await selecting;
      await tester.pump();
      expect(_queueText('OAS1 的待执行任务'), findsNothing);
      expect(_queueText('OAS2 的待执行任务'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('late background response cannot restore another profile queue', (
    tester,
  ) async {
    final api = _QueueApi();
    final c = _controller(
      api,
      snapshot: _snapshot(ready: [_task('OAS1 原有队列')]),
    );
    await _mount(tester, c);
    final refreshing = c.refreshAll();
    await tester.pump();
    final selecting = c.selectProfile('profile-2');
    await tester.pump();
    expect(api.requestedProfiles, ['profile-1', 'profile-2']);
    expect(_queueText('OAS1 原有队列'), findsNothing);

    api.pending[1].complete(_snapshot());
    await selecting;
    await tester.pump();
    final waitingPosition = tester.getTopLeft(_queueText('等待中'));
    expect(_queueText('暂无待执行任务'), findsOneWidget);

    api.pending[0].complete(_snapshot(ready: [_task('OAS1 迟到的队列')]));
    await refreshing;
    await tester.pump();
    expect(c.selectedProfile, 'profile-2');
    expect(_queueText('OAS1 迟到的队列'), findsNothing);
    expect(_queueText('暂无待执行任务'), findsOneWidget);
    expect(tester.getTopLeft(_queueText('等待中')), waitingPosition);
    expect(tester.takeException(), isNull);
  });
}
