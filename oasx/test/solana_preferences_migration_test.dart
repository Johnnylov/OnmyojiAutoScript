import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:get_storage/get_storage.dart';
import 'package:oasx/modules/home/models/config_model.dart';
import 'package:oasx/modules/settings/controllers/settings_controller.dart';
import 'package:oasx/modules/settings/oas_card.dart';
import 'package:oasx/modules/settings/system_card.dart';
import 'package:oasx/service/app_exit_service.dart';
import 'package:oasx/service/app_update_service.dart';
import 'package:oasx/service/autostart_service.dart';
import 'package:oasx/service/locale_service.dart';
import 'package:oasx/service/script_service.dart';
import 'package:oasx/service/window_service.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_application_preferences.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_profile_manager.dart';
import 'package:oasx/solana/solana_widgets.dart';
import 'package:oasx/translation/i18n.dart';

/// Implements (rather than constructs) storage/services, so no inherited host
/// storage, startup registration, process, network or exit hooks can run.
class MemoryStorage implements GetStorage {
  final values = <String, dynamic>{};
  final writes = <String>[];
  @override
  T? read<T>(String key) => values[key] as T?;
  @override
  Future<void> write(String key, dynamic value) async {
    values[key] = value;
    writes.add(key);
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnsupportedError(
    'Unexpected storage call: ${invocation.memberName}',
  );
}

class SettingsFixture extends GetxController implements SettingsController {
  @override
  final MemoryStorage storage = MemoryStorage();
  @override
  final autoDeploy = true.obs;
  @override
  final autoLoginAfterDeploy = false.obs;
  @override
  final updateProxyUrl = 'http://127.0.0.1:7897'.obs;
  @override
  void updateAutoDeploy(bool value) {
    autoDeploy.value = value;
    storage.write('autoDeploy', value);
  }

  @override
  void updateAutoLoginAfterDeploy(bool value) {
    autoLoginAfterDeploy.value = value;
    storage.write('autoLoginAfterDeploy', value);
  }

  @override
  void updateUpdateProxyUrl(String value) {
    updateProxyUrl.value = value.trim();
    storage.write('updateProxyUrl', value.trim());
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnsupportedError(
    'Unexpected settings action: ${invocation.memberName}',
  );
}

class WindowFixture extends GetxService implements WindowService {
  @override
  Future<void> onInit() async => super.onInit();
  @override
  final enableWindowState = true.obs;
  @override
  final enableSystemTray = false.obs;
  @override
  void updateWindowStateEnable(bool value) => enableWindowState.value = value;
  @override
  Future<void> updateSystemTrayEnable(bool value) async =>
      enableSystemTray.value = value;
  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnsupportedError(
    'Unexpected window action: ${invocation.memberName}',
  );
}

class ExitFixture extends GetxService implements AppExitService {
  @override
  final shutdownOasOnExit = true.obs;
  @override
  void updateShutdownOasOnExit(bool value) => shutdownOasOnExit.value = value;
  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnsupportedError(
    'Unexpected exit action: ${invocation.memberName}',
  );
}

class StartupFixture extends GetxService implements AutoStartService {
  @override
  Future<void> onInit() async => super.onInit();
  @override
  final enableLaunchAtStartup = false.obs;
  @override
  final isApplying = false.obs;
  @override
  Future<void> updateLaunchAtStartupEnable(bool value) async =>
      enableLaunchAtStartup.value = value;
  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnsupportedError(
    'Unexpected startup action: ${invocation.memberName}',
  );
}

class LocaleFixture extends GetxService implements LocaleService {
  @override
  final language = 'zh-CN'.obs;
  @override
  void switchLanguage(String value) => language.value = value;
  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnsupportedError(
    'Unexpected locale action: ${invocation.memberName}',
  );
}

class UpdateFixture extends GetxService implements AppUpdateService {
  @override
  final isCheckingForUpdates = false.obs;
  int checks = 0;
  @override
  Future<void> checkForUpdates({
    bool showTip = false,
    bool forceCheck = false,
  }) async {
    checks++;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnsupportedError(
    'Unexpected update action: ${invocation.memberName}',
  );
}

class ScriptFixture extends GetxService implements ScriptService {
  @override
  Future<void> onInit() async => super.onInit();
  @override
  Future<void> onClose() async => super.onClose();
  @override
  final scriptModelMap = <String, ScriptModel>{
    '旧缓存不应显示': ScriptModel('旧缓存不应显示'),
  }.obs;
  @override
  final autoScriptList = <String>[].obs;
  @override
  dynamic noSuchMethod(Invocation invocation) => throw UnsupportedError(
    'Unexpected script action: ${invocation.memberName}',
  );
}

class ProbeApi extends SolanaApi {
  ProbeApi() : super(address: () => 'http://127.0.0.1:1');
  final requests = <String>[];
  @override
  Future<JsonObject> request(
    String method,
    String path, {
    JsonObject? body,
    JsonObject? query,
  }) async {
    requests.add('$method $path');
    throw StateError('This migration test must not issue API requests');
  }
}

class ProjectionController extends SolanaController {
  ProjectionController(ProbeApi probe) : super(api: probe);
  void setConnected(bool value) {
    connected = value;
    notifyListeners();
  }
}

Future<void> prepareVisuals(WidgetTester tester, Size size) async {
  await tester.binding.setSurfaceSize(size);
  final font = File(r'C:\Windows\Fonts\msyh.ttc');
  if (font.existsSync()) {
    await (FontLoader('Microsoft YaHei')
          ..addFont(Future.value(ByteData.sublistView(font.readAsBytesSync()))))
        .load();
  }
  await (FontLoader(
    'MaterialIcons',
  )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
}

Widget host(GlobalKey key, Widget child) => RepaintBoundary(
  key: key,
  child: GetMaterialApp(
    debugShowCheckedModeBanner: false,
    translations: Messages(),
    locale: const Locale('zh', 'CN'),
    theme: solanaTheme(Brightness.light),
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(
        context,
      ).copyWith(textScaler: const TextScaler.linear(1.25)),
      child: child!,
    ),
    home: Scaffold(
      body: Container(
        constraints: const BoxConstraints.expand(),
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            colors: [Color(0xFFDCC7D5), Color(0xFFEAF6F8)],
          ),
        ),
        child: child,
      ),
    ),
  ),
);

Future<void> capture(WidgetTester tester, GlobalKey key, String name) async {
  if (!const bool.fromEnvironment('SOLANA_CAPTURE')) return;
  await tester.runAsync(() async {
    final boundary =
        key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
    final image = await boundary.toImage(pixelRatio: 1);
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    final directory = Directory('build/solana-forms')
      ..createSync(recursive: true);
    await File(
      '${directory.path}/$name.png',
    ).writeAsBytes(bytes!.buffer.asUint8List());
    image.dispose();
  });
}

void main() {
  setUp(() => Get.testMode = true);
  tearDown(() => Get.reset());

  testWidgets(
    'migrated preferences expose saved switches and proxy on a narrow new UI',
    (tester) async {
      await prepareVisuals(tester, const Size(420, 980));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final settings =
          Get.put<SettingsController>(SettingsFixture()) as SettingsFixture;
      final update =
          Get.put<AppUpdateService>(UpdateFixture()) as UpdateFixture;
      Get.put<WindowService>(WindowFixture());
      Get.put<AppExitService>(ExitFixture());
      Get.put<AutoStartService>(StartupFixture());
      Get.put<LocaleService>(LocaleFixture());
      Get.put<ScriptService>(ScriptFixture());
      final key = GlobalKey();
      await tester.pumpWidget(
        host(
          key,
          const SingleChildScrollView(
            child: Padding(
              padding: EdgeInsets.all(16),
              child: SolanaApplicationPreferences(),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      for (final label in [
        '语言',
        '记住窗口位置和大小',
        '最小化到系统托盘',
        '退出客户端时关闭后端',
        '开机启动',
        '自动部署后端',
        '部署完成后连接',
        '自动运行配置列表',
        '更新代理',
        '通知测试',
        '检查后端更新',
        '结束后端服务',
      ]) {
        expect(find.text(label), findsOneWidget, reason: label);
      }
      expect(find.byType(CheckUpdateButton), findsOneWidget);
      expect(settings.storage.writes, isEmpty);
      expect(update.checks, 0);
      final deploy = find.descendant(
        of: find.byType(DeploySwitcher),
        matching: find.byType(Switch),
      );
      expect(tester.widget<Switch>(deploy).value, isTrue);
      final login = find.descendant(
        of: find.byType(LoginAfterDeploySwitcher),
        matching: find.byType(Switch),
      );
      expect(tester.widget<Switch>(login).value, isFalse);
      final proxy = find.descendant(
        of: find.byType(UpdateProxyUrlField),
        matching: find.byType(TextField),
      );
      expect(
        tester.widget<TextField>(proxy).controller!.text,
        'http://127.0.0.1:7897',
      );
      expect(tester.takeException(), isNull);
      await capture(tester, key, 'application-preferences-125');
      await tester.tap(deploy);
      await tester.enterText(proxy, 'http://127.0.0.1:18080');
      await tester.pumpAndSettle();
      expect(settings.autoDeploy.value, isFalse);
      expect(
        settings.storage.read<String>('updateProxyUrl'),
        'http://127.0.0.1:18080',
      );
      expect(settings.storage.writes, ['autoDeploy', 'updateProxyUrl']);
      expect(update.checks, 0);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    },
  );

  testWidgets(
    'profile manager trusts overview, guards offline and running states, and closes without writes',
    (tester) async {
      await prepareVisuals(tester, const Size(440, 720));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final api = ProbeApi();
      final controller = ProjectionController(api);
      // Construct the projection before registering the fake legacy service,
      // so its linker uses the built-in volatile dashboard test storage.
      Get.put<ScriptService>(ScriptFixture());
      addTearDown(controller.dispose);
      controller.overview.data = {
        'profiles': [
          {'id': 'run-profile', 'name': '后端返回的运行配置', 'state': 'running'},
          {'id': 'idle-profile', 'name': '后端返回的停止配置', 'state': 'stopped'},
        ],
      };
      final key = GlobalKey();
      await tester.pumpWidget(
        host(
          key,
          Builder(
            builder: (context) => Center(
              child: FilledButton(
                onPressed: () => showSolanaProfileManager(context, controller),
                child: const Text('打开配置管理'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('打开配置管理'));
      await tester.pumpAndSettle();
      expect(find.text('后端返回的运行配置'), findsOneWidget);
      expect(find.text('后端返回的停止配置'), findsOneWidget);
      expect(find.text('旧缓存不应显示'), findsNothing);
      expect(find.text('配置服务尚未连接。'), findsOneWidget);
      Finder action(String tooltip) => find.byWidgetPredicate(
        (widget) => widget is IconButton && widget.tooltip == tooltip,
      );
      for (final tooltip in ['重命名', '删除配置', '导出脱敏配置']) {
        for (final button in tester.widgetList<IconButton>(action(tooltip))) {
          expect(button.onPressed, isNull);
        }
      }
      Finder addButton() => find.ancestor(
        of: find.text('新增 / 导入配置'),
        matching: find.byWidgetPredicate((widget) => widget is FilledButton),
      );
      expect(tester.widget<FilledButton>(addButton()).onPressed, isNull);
      expect(api.requests, isEmpty);
      controller.setConnected(true);
      await tester.pumpAndSettle();
      Finder row(String name) =>
          find.ancestor(of: find.text(name), matching: find.byType(ListTile));
      for (final tooltip in ['重命名', '删除配置']) {
        final running = find.descendant(
          of: row('后端返回的运行配置'),
          matching: action(tooltip),
        );
        final stopped = find.descendant(
          of: row('后端返回的停止配置'),
          matching: action(tooltip),
        );
        expect(tester.widget<IconButton>(running).onPressed, isNull);
        expect(tester.widget<IconButton>(stopped).onPressed, isNotNull);
      }
      expect(tester.widget<FilledButton>(addButton()).onPressed, isNotNull);
      expect(tester.takeException(), isNull);
      await capture(tester, key, 'profile-manager-125');
      controller.setConnected(false);
      await tester.pumpAndSettle();
      expect(tester.widget<FilledButton>(addButton()).onPressed, isNull);
      for (final button in tester.widgetList<IconButton>(action('重命名'))) {
        expect(button.onPressed, isNull);
      }
      await tester.tap(find.text('关闭'));
      await tester.pumpAndSettle();
      expect(find.text('配置管理'), findsNothing);
      expect(api.requests, isEmpty);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    },
  );
}
