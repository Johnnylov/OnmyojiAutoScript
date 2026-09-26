import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:oasx/routes.dart';

class LegacyBackendApi extends SolanaApi {
  LegacyBackendApi() : super(address: () => 'http://127.0.0.1:22288');
  final paths = <String>[];
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    paths.add(path);
    throw const SolanaApiException('unsupported', '后端未提供此接口', 404);
  }
}

void main() {
  test('the app exposes only the new console route', () {
    expect(Routes.initial, '/console');
    expect(Routes.routes.map((route) => route.name).toList(), ['/console']);
  });

  testWidgets(
    'an unsupported backend offers connection settings without a legacy UI',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(1200, 800));
      final api = LegacyBackendApi();
      final controller = SolanaController(api: api);
      await controller.start();
      await tester.pumpWidget(
        MaterialApp(
          home: SolanaShell(
            controller: controller,
            autoStart: false,
            showCaption: false,
          ),
        ),
      );
      expect(find.textContaining('当前后端尚未提供控制台接口'), findsOneWidget);
      await tester.tap(find.byTooltip('应用设置'));
      await tester.pumpAndSettle();
      expect(find.textContaining('经典工作台'), findsNothing);
      expect(find.text('连接设置'), findsWidgets);
      expect(find.text('后端部署'), findsOneWidget);
      expect(controller.canControl, isFalse);
      expect(controller.overview.data, isNull);
      expect(api.paths.first, '/api/v2/capabilities');
      expect(api.paths.contains('/api/v2/control'), isFalse);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      controller.dispose();
      await tester.binding.setSurfaceSize(null);
    },
  );
}
