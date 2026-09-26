import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';

import 'solana_execution_controls_test.dart'
    show ExecutionApi, controllerFor, mountShell, primaryButton;

class RestartApi extends ExecutionApi {
  RestartApi({super.state, super.executorAlive, super.uncertain});

  String runState = 'recovery_requested';
  Object? nextFailure;
  JsonObject? nextResult;
  Completer<void>? controlGate;
  bool includeSecond = false;
  int secondVersion = 31;

  @override
  JsonObject get retainedRun => {
    ...super.retainedRun,
    'state': runState,
    'executor_alive': executorAlive,
  };

  @override
  JsonObject get snapshot => {
    ...super.snapshot,
    'profiles': [
      ...objects(super.snapshot['profiles']),
      if (includeSecond)
        {
          'id': 'profile-2',
          'name': 'OAS2',
          'state': 'inactive',
          'executor_alive': false,
          'state_version': secondVersion,
        },
    ],
  };

  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    if (path == '/api/v2/recovery' && query?['profile_id'] == 'profile-2') {
      calls.add({'path': path, 'query': query});
      return {'items': <JsonObject>[]};
    }
    return super.get(path, query: query);
  }

  @override
  Future<JsonObject> request(
    String method,
    String path, {
    JsonObject? body,
    JsonObject? query,
  }) async {
    if (path != '/api/v2/control') {
      return super.request(method, path, body: body, query: query);
    }
    calls.add({'method': method, 'path': path, 'body': body, 'query': query});
    await controlGate?.future;
    final failure = nextFailure;
    nextFailure = null;
    if (failure != null) {
      version++;
      throw failure;
    }
    final result = nextResult;
    nextResult = null;
    if (result != null) return result;
    return {
      'accepted': true,
      'executed': true,
      'persisted': true,
      'status': body?['action'] == 'restart' ? 'restarted' : 'started',
    };
  }
}

SolanaController restartController(RestartApi api) =>
    controllerFor(api)
      ..capabilities.data = {
        'api_version': 2,
        'control_actions': [
          'start',
          'restart',
          'pause',
          'resume',
          'safe_stop',
          'immediate_stop',
        ],
      };

Finder get power => find.widgetWithIcon(IconButton, Icons.power_settings_new);

void main() {
  for (final state in ['running', 'waiting', 'paused', 'pausing', 'stopping']) {
    testWidgets('$state power stops immediately without confirmation', (
      tester,
    ) async {
      final api = RestartApi(state: state, executorAlive: true);
      final c = restartController(api);
      await mountShell(tester, c);
      await tester.tap(power);
      await tester.pumpAndSettle();
      expect(find.byType(AlertDialog), findsNothing);
      expect(api.writes, hasLength(1));
      expect(object(api.writes.single['body'])['action'], 'immediate_stop');
      expect(c.operationMessage, contains('脚本已停止'));
    });
  }

  testWidgets('toolbar stop also immediately returns control', (tester) async {
    final api = RestartApi(state: 'running', executorAlive: true);
    final c = restartController(api);
    await mountShell(tester, c);
    await tester.tap(find.byTooltip('立即停止'));
    await tester.pumpAndSettle();
    expect(find.byType(AlertDialog), findsNothing);
    expect(object(api.writes.single['body'])['action'], 'immediate_stop');
  });

  testWidgets('inactive power starts with the latest version', (tester) async {
    final api = RestartApi();
    final c = restartController(api);
    await mountShell(tester, c);
    api.version = 18;
    expect(power.hitTestable(), findsOneWidget);
    expect(tester.widget<IconButton>(power).onPressed, isNotNull);
    await tester.tap(power);
    await tester.pumpAndSettle();
    expect(api.writes, hasLength(1));
    expect(object(api.writes.single['body']), containsPair('action', 'start'));
    expect(object(api.writes.single['body'])['expected_state_version'], 18);
  });

  for (final alive in [true, false]) {
    testWidgets(
      'recovery requested power restarts with executor alive=$alive',
      (tester) async {
        final api = RestartApi(
          state: alive ? 'waiting' : 'warning',
          executorAlive: alive,
          uncertain: true,
        );
        final c = restartController(api);
        await mountShell(tester, c);
        expect(find.text('重新运行').hitTestable(), findsOneWidget);
        expect(primaryButton(tester).onPressed, isNotNull);
        expect(tester.widget<IconButton>(power).onPressed, isNotNull);
        api.version = 23;
        await tester.tap(power);
        await tester.pumpAndSettle();
        expect(find.byType(AlertDialog), findsNothing);
        expect(api.writes, hasLength(1));
        expect(api.writes.single['path'], '/api/v2/control');
        expect(object(api.writes.single['body'])['action'], 'restart');
        expect(object(api.writes.single['body'])['expected_state_version'], 23);
        expect(c.controlReviewRequired, isFalse);
      },
    );
  }

  testWidgets('primary retry uses the same explicit restart endpoint', (
    tester,
  ) async {
    final api = RestartApi(
      state: 'warning',
      executorAlive: true,
      uncertain: true,
    );
    await mountShell(tester, restartController(api));
    await tester.tap(find.byKey(const ValueKey('execution-primary')));
    await tester.pumpAndSettle();
    expect(api.writes, hasLength(1));
    expect(object(api.writes.single['body'])['action'], 'restart');
  });

  testWidgets('version rejection refreshes and enables a manual retry', (
    tester,
  ) async {
    final api = RestartApi()
      ..nextFailure = const SolanaApiException('state_conflict', '状态已变化', 409);
    final c = restartController(api);
    await mountShell(tester, c);
    await tester.tap(power);
    await tester.pumpAndSettle();
    expect(
      api.writes,
      hasLength(1),
      reason: 'Rejected writes are not retried.',
    );
    expect(c.operationFailed, isTrue);
    expect(c.controlReviewRequired, isFalse);
    expect(c.operationMessage, contains('已刷新'));
    expect(c.profile['state_version'], 8);
    expect(primaryButton(tester).onPressed, isNotNull);
    expect(tester.widget<IconButton>(power).onPressed, isNotNull);
    await tester.tap(power);
    await tester.pumpAndSettle();
    expect(api.writes, hasLength(2));
    expect(object(api.writes.last['body'])['expected_state_version'], 8);
    expect(
      object(api.writes.last['body'])['request_id'],
      isNot(object(api.writes.first['body'])['request_id']),
    );
    expect(c.operationFailed, isFalse);
  });

  testWidgets('uncertain network result stays blocked until explicit review', (
    tester,
  ) async {
    final api = RestartApi(state: 'warning', uncertain: true)
      ..nextFailure = TimeoutException('lost response');
    final c = restartController(api);
    await mountShell(tester, c);
    await tester.tap(power);
    await tester.pumpAndSettle();
    expect(api.writes, hasLength(1));
    expect(c.controlReviewRequired, isTrue);
    expect(c.operationMessage, contains('未确认'));
    expect(tester.widget<IconButton>(power).onPressed, isNull);
    expect(primaryButton(tester).onPressed, isNull);
    await c.refreshAll();
    await tester.pumpAndSettle();
    expect(c.controlReviewRequired, isTrue);
    await c.control('restart');
    expect(api.writes, hasLength(1));
    await c.reloadLinkedControl();
    await tester.pumpAndSettle();
    expect(c.controlReviewRequired, isFalse);
    expect(tester.widget<IconButton>(power).onPressed, isNotNull);
    expect(api.writes, hasLength(1), reason: 'Review alone never retries.');
  });

  for (final persisted in [true, false]) {
    testWidgets(
      'failed restart persisted=$persisted sets the right retry guard',
      (tester) async {
        final api = RestartApi(state: 'warning', uncertain: true)
          ..nextResult = {
            'accepted': true,
            'executed': false,
            'persisted': persisted,
            'status': 'failed',
          };
        final c = restartController(api);
        await mountShell(tester, c);
        await tester.tap(power);
        await tester.pumpAndSettle();
        expect(api.writes, hasLength(1));
        expect(c.operationFailed, isTrue);
        expect(c.controlReviewRequired, !persisted);
        expect(
          tester.widget<IconButton>(power).onPressed,
          persisted ? isNotNull : isNull,
        );
        expect(api.writes, hasLength(1), reason: 'No automatic restart retry.');
      },
    );
  }

  testWidgets('late restart response stays on its initiating profile', (
    tester,
  ) async {
    final api = RestartApi(state: 'warning', uncertain: true)
      ..includeSecond = true
      ..controlGate = Completer<void>();
    final c = restartController(api)..linker.setEnabled(false);
    await mountShell(tester, c);
    api.version = 25;
    await tester.tap(power);
    await tester.pump();
    expect(api.writes, hasLength(1));
    expect(object(api.writes.single['body'])['profile_id'], 'profile-1');
    expect(object(api.writes.single['body'])['expected_state_version'], 25);
    await c.selectProfile('profile-2');
    await tester.pump();
    api.controlGate!.complete();
    await tester.pumpAndSettle();
    expect(c.selectedProfile, 'profile-2');
    expect(c.operationMessage, isNull);
    expect(c.lastControlResult, isNull);
    expect(c.controlReviewRequired, isFalse);
    expect(find.textContaining('测试配置：操作已执行'), findsNothing);
    api.secondVersion = 37;
    await tester.tap(power);
    await tester.pumpAndSettle();
    expect(api.writes, hasLength(2));
    expect(object(api.writes.last['body'])['profile_id'], 'profile-2');
    expect(object(api.writes.last['body'])['expected_state_version'], 37);
    expect(object(api.writes.last['body'])['action'], 'start');
    await c.selectProfile('profile-1');
    await tester.pumpAndSettle();
    expect(c.operationMessage, contains('测试配置：操作已执行'));
  });
}
