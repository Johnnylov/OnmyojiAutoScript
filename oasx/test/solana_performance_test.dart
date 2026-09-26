import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_device_preview.dart';
import 'package:oasx/solana/solana_shell.dart';

import 'solana_console_test.dart' show FixtureApi, fixtureController;
import 'solana_connection_settings_test.dart' show ConnectionApi;

const _pixel =
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==';

class _PreviewApi extends FixtureApi {
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    if (path == '/api/v2/preview') {
      return {'available': true, 'image_base64': _pixel};
    }
    return super.get(path, query: query);
  }
}

void main() {
  test('a full background refresh commits one UI notification', () async {
    final api = FixtureApi();
    final c = fixtureController(api: api);
    addTearDown(c.dispose);
    var notifications = 0;
    c.addListener(() => notifications++);
    await c.refreshAll();
    expect(api.calls.length, 8);
    expect(notifications, 1);
  });

  test(
    'live refresh avoids history, audit, storage and recovery queries',
    () async {
      final api = FixtureApi();
      final c = fixtureController(api: api);
      addTearDown(c.dispose);
      var notifications = 0;
      c.addListener(() => notifications++);
      await c.refreshLive();
      expect(api.calls.map((call) => call['path']), [
        '/api/v2/overview',
        '/api/v2/scheduler',
      ]);
      expect(notifications, 1);
    },
  );

  testWidgets('continuous events refresh once per window without starving', (
    tester,
  ) async {
    final api = ConnectionApi();
    final c = SolanaController(api: api);
    await c.start();
    api.requests.clear();
    final socket = api.sockets.single;
    for (var seq = 2; seq < 22; seq++) {
      socket.stream.subscription!.queued(
        jsonEncode({
          'type': 'task.progress',
          'stream_id': 'old-stream',
          'stream_seq': seq,
        }),
      );
      await tester.pump(const Duration(milliseconds: 100));
    }
    expect(
      api.requests.where((r) => r['path'] == '/api/v2/overview').length,
      2,
    );
    expect(
      api.requests.where((r) => r['path'] == '/api/v2/scheduler').length,
      2,
    );
    expect(api.requests.length, 4);
    c.dispose();
    await tester.pump();
  });

  testWidgets(
    'preview updates stay local and reuse image bytes across shell refresh',
    (tester) async {
      final c = fixtureController(api: _PreviewApi());
      await tester.binding.setSurfaceSize(const Size(1124, 751));
      var notifications = 0;
      c.addListener(() => notifications++);
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
      await c.refreshPreview();
      await tester.pumpAndSettle();
      expect(notifications, 0);
      final preview = find.byType(SolanaDevicePreview);
      ImageProvider provider() => tester
          .widget<Image>(
            find.descendant(of: preview, matching: find.byType(Image)),
          )
          .image;
      final first = await provider().obtainKey(ImageConfiguration.empty);
      for (var i = 0; i < 5; i++) {
        await c.refreshAll();
        await c.refreshPreview();
        await tester.pumpAndSettle();
        expect(await provider().obtainKey(ImageConfiguration.empty), first);
      }
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
      await tester.binding.setSurfaceSize(null);
    },
  );
}
