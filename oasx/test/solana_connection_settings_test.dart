import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_connection_settings.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

class TestSubscription implements StreamSubscription<dynamic> {
  final void Function(dynamic)? onMessage;
  bool cancelled = false;
  TestSubscription(this.onMessage);
  @override
  Future<void> cancel() async {
    cancelled = true;
  }

  // Deliberately delivers an already queued callback even after cancellation.
  void queued(Object value) => onMessage?.call(value);
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class TestStream extends Stream<dynamic> {
  TestSubscription? subscription;
  @override
  StreamSubscription<dynamic> listen(
    void Function(dynamic)? onData, {
    Function? onError,
    void Function()? onDone,
    bool? cancelOnError,
  }) {
    return subscription = TestSubscription(onData);
  }
}

class TestSocketSink implements WebSocketSink {
  bool closed = false;
  Completer<void>? closeGate;
  @override
  Future<void> close([int? closeCode, String? closeReason]) async {
    if (closeGate != null) await closeGate!.future;
    closed = true;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class TestSocket implements WebSocketChannel {
  @override
  final TestStream stream = TestStream();
  @override
  final TestSocketSink sink = TestSocketSink();
  final Completer<void>? readyGate;
  TestSocket([this.readyGate]);
  @override
  Future<void> get ready => readyGate?.future ?? Future<void>.value();
  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class ConnectionApi extends SolanaApi {
  String host = 'old';
  final requests = <JsonObject>[];
  final sockets = <TestSocket>[];
  Completer<JsonObject>? heldOverview;
  Completer<void>? firstReady;
  bool unavailable = false;
  ConnectionApi() : super(address: () => 'http://fake.invalid');
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    requests.add({'host': host, 'path': path});
    if (unavailable) {
      throw const SolanaApiException('connection_error', 'unavailable');
    }
    if (host == 'old' && path == '/api/v2/overview' && heldOverview != null) {
      return heldOverview!.future;
    }
    if (path == '/api/v2/overview') {
      return {
        'profiles': [
          {'id': 'same-id', 'name': host, 'state_version': 1},
        ],
        'server': host,
        'stream_id': '$host-stream',
        'stream_seq': 1,
      };
    }
    return {'server': host, 'items': <JsonObject>[]};
  }

  @override
  WebSocketChannel connect({String? streamId, int? after}) {
    final socket = TestSocket(sockets.isEmpty ? firstReady : null);
    sockets.add(socket);
    return socket;
  }

  @override
  Future<JsonObject> request(
    String method,
    String path, {
    JsonObject? body,
    JsonObject? query,
  }) => throw StateError(
    'Connection settings must never send control, config, or credentials',
  );
}

class MemoryPreferences implements SolanaConnectionPreferences {
  final ConnectionApi api;
  @override
  String address = 'http://old';
  @override
  String username = 'existing-user';
  @override
  String password = 'existing-secret';
  int writes = 0;
  int syncs = 0;
  MemoryPreferences(this.api);
  @override
  Future<void> save({
    required String address,
    required String username,
    required String password,
  }) async {
    expectSync(api.sockets.every((socket) => socket.sink.closed), isTrue);
    writes++;
    this.address = address;
    this.username = username;
    this.password = password;
    api.host = 'new';
  }

  @override
  Future<void> synchronize() async {
    syncs++;
  }
}

Future<void> openDialog(
  WidgetTester tester,
  SolanaController c,
  MemoryPreferences preferences,
  ValueChanged<bool> onResult, {
  bool Function()? isStartupChecking,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: Builder(
          builder: (context) => TextButton(
            onPressed: () async => onResult(
              await showSolanaConnectionSettings(
                context,
                c,
                preferences: preferences,
                isStartupChecking: isStartupChecking,
              ),
            ),
            child: const Text('Open'),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('Open'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'startup check disables connection save while cancel remains available',
    (tester) async {
      final api = ConnectionApi();
      final c = SolanaController(api: api);
      final preferences = MemoryPreferences(api);
      bool? result;
      await openDialog(
        tester,
        c,
        preferences,
        (value) => result = value,
        isStartupChecking: () => true,
      );
      expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, '保存并连接'))
            .onPressed,
        isNull,
      );
      expect(find.textContaining('启动连接检查尚未结束'), findsOneWidget);
      await tester.tap(find.text('取消'));
      await tester.pumpAndSettle();
      expect(result, isFalse);
      expect(preferences.writes, 0);
      expect(api.requests, isEmpty);
      c.dispose();
    },
  );

  testWidgets(
    'submit rechecks startup state even before the button can rebuild',
    (tester) async {
      final api = ConnectionApi();
      final c = SolanaController(api: api);
      final preferences = MemoryPreferences(api);
      var checking = false;
      await openDialog(
        tester,
        c,
        preferences,
        (_) {},
        isStartupChecking: () => checking,
      );
      expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, '保存并连接'))
            .onPressed,
        isNotNull,
      );
      checking = true;
      await tester.tap(find.text('保存并连接'));
      await tester.pump();
      expect(preferences.writes, 0);
      expect(c.backendGeneration, 0);
      expect(api.requests, isEmpty);
      await tester.tap(find.text('取消'));
      await tester.pumpAndSettle();
      c.dispose();
    },
  );

  testWidgets(
    'startup beginning during stream shutdown also prevents preferences write',
    (tester) async {
      final api = ConnectionApi();
      final c = SolanaController(api: api);
      await c.start();
      final gate = Completer<void>();
      api.sockets.single.sink.closeGate = gate;
      final preferences = MemoryPreferences(api);
      var checking = false;
      await openDialog(
        tester,
        c,
        preferences,
        (_) {},
        isStartupChecking: () => checking,
      );
      await tester.tap(find.text('保存并连接'));
      await tester.pump();
      expect(c.busy, isTrue);
      checking = true;
      gate.complete();
      await tester.pumpAndSettle();
      expect(preferences.writes, 0);
      expect(preferences.address, 'http://old');
      expect(c.connected, isTrue);
      expect(c.profile['name'], 'old');
      expect(find.textContaining('启动连接检查尚未结束'), findsOneWidget);
      await tester.tap(find.text('取消'));
      await tester.pumpAndSettle();
      c.dispose();
    },
  );

  testWidgets(
    'failed settings update restores old backend polling and preserves original error',
    (tester) async {
      final api = ConnectionApi();
      final c = SolanaController(api: api);
      await c.start();
      final oldSocket = api.sockets.single;
      final failure = StateError('fixture save rejected');
      await expectLater(
        c.reconnectBackend(
          updateConnection: () async {
            throw failure;
          },
        ),
        throwsA(same(failure)),
      );
      expect(oldSocket.sink.closed, isTrue);
      expect(c.connected, isTrue);
      expect(c.streamConnected, isTrue);
      expect(c.busy, isFalse);
      expect(c.profile['name'], 'old');
      expect(api.sockets, hasLength(2));
      final before = api.requests.length;
      await tester.pump(const Duration(seconds: 16));
      expect(api.requests.length, greaterThan(before));
      expect(api.requests.every((item) => item['host'] == 'old'), isTrue);
      c.dispose();
    },
  );

  testWidgets(
    'switch rejects old HTTP completion and queued old stream callback',
    (tester) async {
      final api = ConnectionApi();
      final c = SolanaController(api: api);
      await c.start();
      final oldSocket = api.sockets.single;
      final oldListener = oldSocket.stream.subscription!;
      api.heldOverview = Completer<JsonObject>();
      final staleRefresh = c.refreshAll();
      await tester.pump();
      c.preview.data = {'server': 'old'};
      final updateGate = Completer<void>();
      final reconnect = c.reconnectBackend(
        updateConnection: () async {
          expectSync(oldSocket.sink.closed, isTrue);
          await updateGate.future;
          api.host = 'new';
        },
      );
      await tester.pump();
      expect(c.capabilities.data, isNull);
      expect(c.overview.data, isNull);
      expect(c.preview.data, isNull);
      expect(c.selectedProfile, isNull);
      expect(c.busy, isTrue);
      final countWhileChanging = api.requests.length;
      await c.refreshAll();
      expect(api.requests, hasLength(countWhileChanging));
      updateGate.complete();
      await tester.pump();
      await reconnect;
      expect(c.profile['name'], 'new');
      expect(c.streamConnected, isTrue);
      final count = api.requests.length;
      api.heldOverview!.complete({
        'server': 'old',
        'profiles': [
          {'id': 'same-id', 'name': 'old'},
        ],
      });
      await staleRefresh;
      oldListener.queued(
        jsonEncode({
          'type': 'resync_required',
          'stream_id': 'old-stream',
          'stream_seq': 99,
        }),
      );
      await tester.pump(const Duration(seconds: 5));
      expect(api.requests, hasLength(count));
      expect(c.overview.data!['server'], 'new');
      for (final remote in [
        c.capabilities,
        c.scheduler,
        c.storage,
        c.statistics,
        c.runs,
        c.audit,
        c.recovery,
      ]) {
        expect(remote.data!['server'], 'new');
      }
      expect(oldListener.cancelled, isTrue);
      c.dispose();
    },
  );

  testWidgets(
    'old socket handshake finishing late cannot replace new connection',
    (tester) async {
      final api = ConnectionApi()..firstReady = Completer<void>();
      final c = SolanaController(api: api);
      final initial = c.start();
      await tester.pump();
      expect(api.sockets, hasLength(1));
      await c.reconnectBackend(
        updateConnection: () async {
          api.host = 'new';
        },
      );
      api.firstReady!.complete();
      await initial;
      expect(api.sockets, hasLength(2));
      expect(api.sockets.first.sink.closed, isTrue);
      expect(api.sockets.last.sink.closed, isFalse);
      expect(c.profile['name'], 'new');
      c.dispose();
    },
  );

  testWidgets('cancel edits no preferences and starts no connection', (
    tester,
  ) async {
    final api = ConnectionApi();
    final c = SolanaController(api: api);
    final preferences = MemoryPreferences(api);
    bool? result;
    await openDialog(tester, c, preferences, (value) => result = value);
    await tester.enterText(
      find.byKey(const ValueKey('connection-address')),
      'http://new',
    );
    await tester.enterText(
      find.byKey(const ValueKey('connection-password')),
      'draft-secret',
    );
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(result, isFalse);
    expect(preferences.writes, 0);
    expect(preferences.password, 'existing-secret');
    expect(api.requests, isEmpty);
    c.dispose();
  });

  testWidgets(
    'save applies local fields once and reconnects without credential API calls',
    (tester) async {
      final api = ConnectionApi();
      final c = SolanaController(api: api);
      await c.start();
      final preferences = MemoryPreferences(api);
      bool? result;
      await openDialog(tester, c, preferences, (value) => result = value);
      await tester.enterText(
        find.byKey(const ValueKey('connection-address')),
        'http://new',
      );
      await tester.enterText(
        find.byKey(const ValueKey('connection-username')),
        'new-user',
      );
      await tester.enterText(
        find.byKey(const ValueKey('connection-password')),
        'new-secret',
      );
      expect(preferences.writes, 0);
      await tester.tap(find.text('保存并连接'));
      await tester.pumpAndSettle();
      expect(result, isTrue);
      expect(preferences.writes, 1);
      expect(preferences.syncs, 1);
      expect(preferences.username, 'new-user');
      expect(preferences.password, 'new-secret');
      expect(c.profile['name'], 'new');
      expect(api.requests.toString(), isNot(contains('new-secret')));
      c.dispose();
    },
  );

  testWidgets(
    'invalid address stays local and disconnected saved result is explicit',
    (tester) async {
      final api = ConnectionApi();
      final c = SolanaController(api: api);
      final preferences = MemoryPreferences(api);
      bool? result;
      await openDialog(tester, c, preferences, (value) => result = value);
      await tester.enterText(
        find.byKey(const ValueKey('connection-address')),
        'ftp://new',
      );
      await tester.tap(find.text('保存并连接'));
      await tester.pump();
      expect(preferences.writes, 0);
      expect(find.text('请输入有效的 HTTP/HTTPS 后端地址'), findsOneWidget);
      await tester.enterText(
        find.byKey(const ValueKey('connection-address')),
        'http://new',
      );
      api.unavailable = true;
      await tester.tap(find.text('保存并连接'));
      await tester.pumpAndSettle();
      expect(preferences.writes, 1);
      expect(find.textContaining('设置已保存，但后端尚未连接'), findsOneWidget);
      expect(result, isNull);
      await tester.tap(find.text('关闭'));
      await tester.pumpAndSettle();
      expect(result, isTrue);
      c.dispose();
    },
  );
}
