import 'package:device_preview/device_preview.dart';
import 'package:flutter/foundation.dart' show kReleaseMode;
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:get_storage/get_storage.dart';
import 'package:oasx/solana/solana_widgets.dart' show solanaTheme;
import 'package:oasx/modules/settings/controllers/settings_controller.dart';
import 'package:oasx/service/app_exit_service.dart';
import 'package:oasx/service/autostart_service.dart';
import 'package:oasx/service/app_update_service.dart';
import 'package:oasx/service/locale_service.dart';
import 'package:oasx/service/script_service.dart';
import 'package:oasx/service/system_tray_service.dart';
import 'package:oasx/service/theme_service.dart';
import 'package:oasx/service/websocket_service.dart';
import 'package:oasx/service/window_service.dart';
import 'package:oasx/translation/i18n.dart';
import 'package:oasx/utils/logger.dart';
import 'package:oasx/utils/platform_utils.dart';
import 'package:oasx/routes.dart';
import 'package:oasx/solana/launch_options.dart';
import 'package:responsive_builder/responsive_builder.dart';

void main(List<String> args) async {
  WidgetsFlutterBinding.ensureInitialized();
  final launchOptions = LaunchOptions.parse(args);
  await launchOptions.initializeIsolatedStorage();
  await initService();

  runApp(
    DevicePreview(
      enabled: !kReleaseMode && PlatformUtils.isWindows,
      builder: (context) =>
          OASXApp(skipStartupActions: launchOptions.skipStartupActions),
    ),
  );
}

class OASXApp extends StatelessWidget {
  final bool skipStartupActions;
  const OASXApp({super.key, this.skipStartupActions = false});

  @override
  Widget build(BuildContext context) {
    final localeService = Get.find<LocaleService>();

    return ResponsiveApp(
      builder: (context) {
        return GetMaterialApp(
          debugShowCheckedModeBanner: false,
          builder: DevicePreview.appBuilder,
          scrollBehavior: GlobalBehavior(),
          translations: Messages(),
          locale: localeService.currentLocale,
          fallbackLocale: localeService.fallbackLocale,
          title: 'OASX',
          initialRoute: Routes.initial,
          getPages: Routes.build(skipStartupActions: skipStartupActions),
          theme: solanaTheme(Brightness.light),
          darkTheme: solanaTheme(Brightness.dark),
        );
      },
    );
  }
}

class GlobalBehavior extends MaterialScrollBehavior {
  @override
  Set<PointerDeviceKind> get dragDevices => {
    PointerDeviceKind.touch,
    PointerDeviceKind.mouse,
  };
}

Future<void> initService() async {
  await GetStorage.init();

  Get.put(SettingsController(), permanent: true);
  Get.put(AppExitService(), permanent: true);
  if (PlatformUtils.isDesktop) {
    Get.put(SystemTrayService(), permanent: true);
  }
  Get.lazyPut<WebSocketService>(() => WebSocketService(), fenix: true);
  final windowService = Get.put(WindowService());

  await Future.wait([
    initLogger(),
    Get.putAsync(() async => LocaleService()),
    Get.putAsync(() async => ThemeService()),
    Get.putAsync(() async => AutoStartService(), permanent: true),
    Get.putAsync(() async => AppUpdateService(), permanent: true),
    windowService.ready,
    Get.putAsync(() async => ScriptService(), permanent: true),
  ]);
}
