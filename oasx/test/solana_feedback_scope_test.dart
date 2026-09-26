import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_shell.dart';

import 'solana_linker_test.dart' show LinkApi, controller;

class _MutationApi extends LinkApi {
  final mutationGate = Completer<void>();

  @override
  Future<JsonObject> request(
    String method,
    String path, {
    JsonObject? body,
    JsonObject? query,
  }) async {
    if (path == '/api/v2/storage/policy') {
      await mutationGate.future;
      return {'saved': true};
    }
    return super.request(method, path, body: body, query: query);
  }
}

void main() {
  test(
    'feedback belongs to initiating profile and B uses its own state version',
    () async {
      final api = LinkApi();
      final c = controller(api)..linker.setEnabled(false);
      addTearDown(c.dispose);
      await c.control('start');
      expect(c.operationMessage, contains('A：操作已执行'));
      await c.selectProfile('id-b');
      expect(c.operationMessage, isNull);
      expect(c.operationFailed, isFalse);
      expect(c.lastControlResult, isNull);
      await c.control('pause');
      expect(api.calls.last['profile_id'], 'id-b');
      expect(api.calls.last['expected_state_version'], 22);
      expect(c.operationMessage, contains('B：操作已执行'));
      expect(c.operationMessage, isNot(contains('A：')));
      await c.selectProfile('id-a');
      expect(c.operationMessage, contains('A：操作已执行'));
    },
  );

  test(
    'delayed A completion after selecting B cannot publish an A banner on B',
    () async {
      final api = LinkApi()..gate = Completer<void>();
      final c = controller(api)..linker.setEnabled(false);
      addTearDown(c.dispose);
      final pending = c.control('start');
      await Future<void>.delayed(Duration.zero);
      expect(api.calls.single['profile_id'], 'id-a');
      await c.selectProfile('id-b');
      expect(c.operationMessage, isNull);
      api.gate!.complete();
      await pending;
      expect(c.selectedProfile, 'id-b');
      expect(c.operationMessage, isNull);
      expect(c.lastControlResult, isNull);
      expect(c.controlReviewRequired, isFalse);
      await c.control('start');
      expect(api.calls.last['profile_id'], 'id-b');
      expect(api.calls.last['expected_state_version'], 22);
      expect(c.operationMessage, contains('B：操作已执行'));
    },
  );

  test(
    'unconfirmed A result survives switching but does not block unrelated B',
    () async {
      final api = LinkApi()
        ..gate = Completer<void>()
        ..unpersisted.add('id-a');
      final c = controller(api)..linker.setEnabled(false);
      addTearDown(c.dispose);
      final pending = c.control('start');
      await c.selectProfile('id-b');
      api.gate!.complete();
      await pending;
      expect(c.operationMessage, isNull);
      expect(c.controlReviewRequired, isFalse);
      await c.control('start');
      expect(api.calls.last['profile_id'], 'id-b');
      await c.reloadLinkedControl();
      await c.selectProfile('id-a');
      expect(c.controlReviewRequired, isTrue);
      expect(c.operationFailed, isTrue);
      expect(c.operationMessage, contains('记录未保存'));
      final count = api.calls.length;
      await c.control('start');
      expect(api.calls, hasLength(count));
      await c.reloadLinkedControl();
      expect(c.controlReviewRequired, isFalse);
    },
  );

  test(
    'linked scope names source and retains review for every affected profile',
    () async {
      final api = LinkApi()..fail.add('id-b');
      final c = controller(api);
      addTearDown(c.dispose);
      await c.control('start');
      expect(c.operationMessage, contains('联动操作（由 A 发起）'));
      expect(c.operationMessage, contains('A：操作已执行'));
      expect(c.operationMessage, contains('B：配置已被修改'));
      await c.selectProfile('id-c');
      expect(c.operationMessage, isNull);
      expect(c.controlReviewRequired, isFalse);
      await c.selectProfile('id-b');
      expect(c.operationMessage, isNull);
      expect(c.controlReviewRequired, isTrue);
      c.linker.setEnabled(false);
      expect(c.controlReviewRequired, isTrue);
      await c.reloadLinkedControl();
      expect(c.controlReviewRequired, isFalse);
      await c.selectProfile('id-a');
      expect(c.controlReviewRequired, isTrue);
    },
  );

  test(
    'a confirmed safety request never silently clears an earlier uncertainty',
    () async {
      final api = LinkApi()..unpersisted.add('id-a');
      final c = controller(api)..linker.setEnabled(false);
      addTearDown(c.dispose);
      await c.control('start');
      expect(c.controlReviewRequired, isTrue);
      api.unpersisted.clear();
      await c.control('safe_stop');
      expect(api.calls.last['action'], 'safe_stop');
      expect(c.operationFailed, isFalse);
      expect(c.controlReviewRequired, isTrue);
    },
  );

  test(
    'quick schedule and ordinary mutations preserve their captured owner',
    () async {
      final api = LinkApi()
        ..gate = Completer<void>()
        ..scheduler = {
          'ready': <JsonObject>[],
          'waiting': [
            {'profile_id': 'id-a', 'task_id': 'Orochi'},
          ],
          'running': <JsonObject>[],
        };
      final c = controller(api)..linker.setEnabled(false);
      addTearDown(c.dispose);
      final pending = c.quickScheduleTask('Orochi');
      while (api.calls.isEmpty) {
        await Future<void>.delayed(Duration.zero);
      }
      await c.selectProfile('id-b');
      api.gate!.complete();
      await pending;
      expect(c.operationMessage, isNull);
      await c.selectProfile('id-a');
      expect(c.operationMessage, contains('A：Orochi 已安排立即执行'));

      final otherApi = _MutationApi();
      final other = controller(otherApi)..linker.setEnabled(false);
      addTearDown(other.dispose);
      final saved = other.saveStorage({'mode': 'standard'});
      await other.selectProfile('id-b');
      otherApi.mutationGate.complete();
      await saved;
      expect(other.operationMessage, isNull);
      await other.selectProfile('id-a');
      expect(other.operationMessage, '存储设置已保存');
    },
  );

  testWidgets('the OAS2 view hides an OAS1 result throughout a late response', (
    tester,
  ) async {
    final api = LinkApi()..gate = Completer<void>();
    api.profiles[0]['name'] = 'OAS1';
    api.profiles[1]['name'] = 'OAS2';
    final c = controller(api)..linker.setEnabled(false);
    await tester.binding.setSurfaceSize(const Size(1124, 751));
    addTearDown(() async {
      await tester.pumpWidget(const SizedBox());
      c.dispose();
      await tester.binding.setSurfaceSize(null);
    });
    await tester.pumpWidget(
      MaterialApp(
        home: SolanaShell(
          controller: c,
          autoStart: false,
          showCaption: false,
          terminalBuilder: (_) => const SizedBox(),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final pending = c.control('start');
    await c.selectProfile('id-b');
    await tester.pumpAndSettle();
    expect(find.textContaining('OAS1：操作已执行'), findsNothing);
    api.gate!.complete();
    await pending;
    await tester.pumpAndSettle();
    expect(find.textContaining('OAS1：操作已执行'), findsNothing);
    expect(c.profile['name'], 'OAS2');
    expect(find.text('OAS2'), findsWidgets);
    expect(tester.takeException(), isNull);
  });
}
