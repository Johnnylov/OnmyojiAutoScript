import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:get_storage/get_storage.dart';
import 'package:oasx/modules/home/controllers/dashboard_controller.dart';
import 'package:oasx/modules/settings/controllers/settings_controller.dart';
import 'package:oasx/service/script_service.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'solana_connection_settings_test.dart' show TestSocket;
import 'solana_console_test.dart' show FixtureApi;

class _StartupApi extends FixtureApi {
  @override
  WebSocketChannel connect({String? streamId, int? after}) => TestSocket();
}

void main() {
  late Directory storageDirectory;
  late GetStorage storage;
  setUpAll(() async {
    storageDirectory = await Directory.systemTemp.createTemp('oasx-startup-');
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('plugins.flutter.io/path_provider'),
          (_) async => storageDirectory.path,
        );
    storage = GetStorage('GetStorage', storageDirectory.path);
    await storage.initStorage;
    await storage.write('autoDeploy', true);
    await storage.write('autoScriptList', ['oas1', 'oas2']);
  });
  tearDownAll(() async {
    // GetStorage retains its RandomAccessFile for the test process lifetime on
    // Windows and exposes no close API; this isolated temp folder may be locked.
    try {
      await storageDirectory.delete(recursive: true);
    } on PathAccessException {
      // The OS closes it when the test runner exits.
    }
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('plugins.flutter.io/path_provider'),
          null,
        );
  });

  testWidgets(
    'suppressed startup still loads backend data and preserves saved actions',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(1124, 751));
      final api = _StartupApi();
      final controller = SolanaController(api: api);
      var startupAttempts = 0;
      // A startup action cannot reach any real service in this test. The
      // registered dependencies make the same startup guard eligible as the app.
      Get.lazyPut<HomeDashboardController>(() {
        startupAttempts++;
        throw StateError('Startup action intercepted by test');
      });
      Get.lazyPut<SettingsController>(
        () => throw StateError('No real settings'),
      );
      Get.lazyPut<ScriptService>(() => throw StateError('No real executor'));

      Widget shell(bool skip) => MaterialApp(
        home: SolanaShell(
          controller: controller,
          skipStartupActions: skip,
          showCaption: false,
          terminalBuilder: (_) => const SizedBox(),
        ),
      );
      await tester.pumpWidget(shell(true));
      await tester.pumpAndSettle();
      expect(startupAttempts, 0);
      expect(controller.connected, isTrue);
      expect(controller.profiles, isNotEmpty);
      expect(
        api.calls.map((call) => call['path']),
        containsAll([
          '/api/v2/capabilities',
          '/api/v2/overview',
          '/script_menu',
        ]),
      );
      expect(api.calls.where((call) => call['method'] != null), isEmpty);
      expect(storage.read<bool>('autoDeploy'), isTrue);
      expect(storage.read<List>('autoScriptList'), ['oas1', 'oas2']);
      expect(storage.hasData('skipStartupActions'), isFalse);
      expect(tester.takeException(), isNull);

      // Recreating the shell without the one-launch flag restores the ordinary
      // startup path; intercepted before any backend mutation is possible.
      await tester.pumpWidget(const SizedBox());
      await tester.pumpWidget(shell(false));
      await tester.pumpAndSettle();
      expect(startupAttempts, 1);
      expect(controller.operationFailed, isTrue);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
      Get.reset();
      await tester.binding.setSurfaceSize(null);
    },
  );
}
