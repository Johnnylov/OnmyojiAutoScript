import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/log/log_browser_models.dart';
import 'package:oasx/modules/log/script_log_browser_controller.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:oasx/solana/solana_terminal.dart';
import 'package:oasx/translation/i18n.dart';

class _RealReadOnlyHttp extends HttpOverrides {}

class _SnapshotApi extends SolanaApi {
  final Map<String, JsonObject> snapshot;
  _SnapshotApi(String address, this.snapshot) : super(address: () => address);
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async =>
      snapshot[path] ?? <String, dynamic>{};
  @override
  Future<JsonObject> request(
    String method,
    String path, {
    JsonObject? body,
    JsonObject? query,
  }) => throw StateError('Read-only snapshot cannot issue mutations');
}

/// Opt-in integration test. Every network request in this file is a GET.
/// No GetStorage initialization or production address preference is changed.
void main() {
  final backend = Platform.environment['SOLANA_TEST_BACKEND'];
  testWidgets(
    'live backend read-only contract and workspace snapshot',
    (tester) async {
      Get.testMode = true;
      await tester.binding.setSurfaceSize(const Size(1124, 751));
      final snapshot = <String, JsonObject>{};
      late JsonObject profile;
      late JsonObject logWindow;
      await tester.runAsync(
        () => HttpOverrides.runWithHttpOverrides(() async {
          final api = SolanaApi(address: () => backend!);
          try {
            snapshot['/api/v2/capabilities'] = await api.get(
              '/api/v2/capabilities',
            );
            expect(snapshot['/api/v2/capabilities']?['api_version'], 2);
            snapshot['/api/v2/overview'] = await api.get('/api/v2/overview');
            final profiles = objects(snapshot['/api/v2/overview']?['profiles']);
            expect(profiles, isNotEmpty);
            profile = profiles.first;
            expect(profile['id'], isA<String>());
            expect(profile['name'], isA<String>());
            final second = await api.get('/api/v2/overview');
            expect(objects(second['profiles']).first['id'], profile['id']);
            final query = <String, dynamic>{'profile_id': profile['id']};
            for (final path in [
              '/api/v2/scheduler',
              '/api/v2/storage',
              '/api/v2/recovery',
              '/api/v2/recovery/operations',
              '/api/v2/preview',
              '/api/v2/statistics',
              '/api/v2/audit',
              '/api/v2/runs',
              '/script_menu',
            ]) {
              snapshot[path] = await api.get(path, query: query);
            }
            expect(snapshot['/api/v2/scheduler']?['policy'], isA<Map>());
            expect(snapshot['/api/v2/recovery']?['items'], isA<List>());
            expect(
              snapshot['/api/v2/recovery/operations']?['items'],
              isA<List>(),
            );
            expect(snapshot['/api/v2/storage']?['policy'], isA<Map>());
            expect(snapshot['/api/v2/preview']?['available'], isA<bool>());
            final menu = snapshot['/script_menu']!;
            expect(menu, isNotEmpty);
            final tasks = menu.entries
                .expand(
                  (entry) =>
                      entry.value is List && (entry.value as List).isNotEmpty
                      ? (entry.value as List).map((item) => item.toString())
                      : [entry.key],
                )
                .where(
                  (task) =>
                      !['Overview', 'Home', 'Updater', 'Tool'].contains(task),
                )
                .toList();
            final task = tasks.firstWhere(
              (task) => task.toLowerCase() == 'orochi',
              orElse: () => tasks.first,
            );
            final args = await api.get(
              '/api/v2/config/${Uri.encodeComponent(profile['id'])}/${Uri.encodeComponent(task)}/args',
            );
            expect(args['args'], isA<Map>());
            expect(args['revision'], isA<String>());
            logWindow = await api.get(
              '/logs/${Uri.encodeComponent(profile['name'])}',
              query: {'limit_lines': 100},
            );
            expect(logWindow['lines'], isA<List>());
          } finally {
            api.dispose();
          }
        }, _RealReadOnlyHttp()),
      );
      for (final entry in [
        ('Microsoft YaHei', r'C:\Windows\Fonts\msyh.ttc'),
        ('Consolas', r'C:\Windows\Fonts\consola.ttf'),
      ]) {
        final font = File(entry.$2);
        if (font.existsSync()) {
          await (FontLoader(entry.$1)..addFont(
                Future.value(ByteData.sublistView(font.readAsBytesSync())),
              ))
              .load();
        }
      }
      await (FontLoader(
        'MaterialIcons',
      )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
      final c = SolanaController(api: _SnapshotApi(backend!, snapshot));
      c.capabilities.data = snapshot['/api/v2/capabilities'];
      c.overview.data = snapshot['/api/v2/overview'];
      c.scheduler.data = snapshot['/api/v2/scheduler'];
      c.statistics.data = snapshot['/api/v2/statistics'];
      c.storage.data = snapshot['/api/v2/storage'];
      c.recovery.data = snapshot['/api/v2/recovery'];
      c.recoveryOperations.data = snapshot['/api/v2/recovery/operations'];
      c.preview.data = snapshot['/api/v2/preview'];
      c.audit.data = snapshot['/api/v2/audit'];
      c.runs.data = snapshot['/api/v2/runs'];
      c.selectedProfile = profile['id'];
      c.connected = true;
      final logs = ScriptLogBrowserController(scriptName: profile['name']);
      logs.lines.addAll(
        objects(logWindow['lines']).map(ScriptLogLine.fromJson),
      );
      final key = GlobalKey();
      await tester.pumpWidget(
        GetMaterialApp(
          translations: Messages(),
          locale: const Locale('zh', 'CN'),
          home: RepaintBoundary(
            key: key,
            child: Stack(
              children: [
                SolanaShell(
                  controller: c,
                  autoStart: false,
                  showCaption: false,
                  terminalBuilder: (name) => SolanaTerminal(
                    scriptName: name,
                    autoConnect: false,
                    controller: logs,
                  ),
                ),
                const Positioned(
                  bottom: 1,
                  right: 12,
                  child: Text(
                    'LIVE READ-ONLY SNAPSHOT',
                    style: TextStyle(
                      fontFamily: 'Microsoft YaHei',
                      fontSize: 8,
                      color: Colors.grey,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.runAsync(() async {
        for (final path in [
          'assets/images/Icon-app.png',
          for (final name in [
            'stamina',
            'jade',
            'coins',
            'summon_tickets',
            'gold_scales',
            'spirit_tickets',
            'raid_tickets',
            'purple_scales',
          ])
            'assets/solana/$name.png',
        ]) {
          await precacheImage(
            AssetImage(path),
            tester.element(find.byType(SolanaShell)),
          );
        }
      });
      await tester.pump();
      expect(tester.takeException(), isNull);
      await tester.runAsync(() async {
        final output = Directory('build/solana-live')
          ..createSync(recursive: true);
        final boundary =
            key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
        final image = await boundary.toImage(pixelRatio: 1);
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        await File(
          '${output.path}/live-overview.png',
        ).writeAsBytes(bytes!.buffer.asUint8List());
        image.dispose();
      });
      await tester.pumpWidget(const SizedBox());
      c.dispose();
      Get.reset();
      await tester.binding.setSurfaceSize(null);
    },
    skip: backend == null || backend.isEmpty,
  );
}
