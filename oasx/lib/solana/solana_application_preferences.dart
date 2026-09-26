import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:oasx/config/global.dart';
import 'package:oasx/modules/settings/controllers/settings_controller.dart';
import 'package:oasx/modules/settings/system_card.dart'
    show
        LanguageToggle,
        WindowStateSwitch,
        SystemTraySwitch,
        ShutdownOasOnExitSwitch,
        LaunchAtStartupSwitch,
        UpdateProxyUrlField,
        CheckUpdateButton;
import 'package:oasx/modules/settings/oas_card.dart'
    show DeploySwitcher, LoginAfterDeploySwitcher, notifyTest;
import 'package:oasx/modules/settings/oas_card_extra.dart'
    show AutoScriptButton, updater, killServer;
import 'package:oasx/service/app_exit_service.dart';
import 'package:oasx/service/app_update_service.dart';
import 'package:oasx/service/autostart_service.dart';
import 'package:oasx/service/locale_service.dart';
import 'package:oasx/service/script_service.dart';
import 'package:oasx/service/window_service.dart';
import 'package:oasx/utils/platform_utils.dart';

import 'solana_widgets.dart';

/// Existing service preferences inside the single new workbench, without an
/// alternate settings page or a second theme toggle.
class SolanaApplicationPreferences extends StatelessWidget {
  const SolanaApplicationPreferences({super.key});

  Widget _row(String label, Widget control, {String? help}) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 7),
    child: Row(
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: const TextStyle(fontSize: 12.5)),
              if (help != null) ...[
                const SizedBox(height: 3),
                Text(
                  help,
                  style: const TextStyle(
                    fontSize: 11,
                    color: Color(0xFF756B82),
                  ),
                ),
              ],
            ],
          ),
        ),
        const SizedBox(width: 12),
        Flexible(child: control),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    if (!Get.isRegistered<SettingsController>()) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Surface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SectionHeading('应用与启动设置'),
              if (Get.isRegistered<LocaleService>())
                _row('语言', const LanguageToggle()),
              if (PlatformUtils.isDesktop &&
                  Get.isRegistered<WindowService>()) ...[
                _row('记住窗口位置和大小', const WindowStateSwitch()),
                _row('最小化到系统托盘', const SystemTraySwitch()),
              ],
              if (Get.isRegistered<AppExitService>())
                _row('退出客户端时关闭后端', const ShutdownOasOnExitSwitch()),
              if (PlatformUtils.isDesktop &&
                  Get.isRegistered<AutoStartService>())
                _row('开机启动', const LaunchAtStartupSwitch()),
              if (PlatformUtils.isDesktop) ...[
                _row('自动部署后端', const DeploySwitcher()),
                _row('部署完成后连接', const LoginAfterDeploySwitcher()),
              ],
              if (Get.isRegistered<ScriptService>())
                _row('自动运行配置列表', const AutoScriptButton()),
              if (!PlatformUtils.isWeb)
                _row('更新代理', const UpdateProxyUrlField()),
              if (Get.isRegistered<AppUpdateService>())
                _row('客户端版本 ${GlobalVar.version}', const CheckUpdateButton()),
            ],
          ),
        ),
        const SizedBox(height: 18),
        Surface(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const SectionHeading('后端维护'),
              const SizedBox(height: 12),
              Wrap(
                spacing: 10,
                runSpacing: 8,
                children: [
                  OutlinedButton.icon(
                    onPressed: notifyTest,
                    icon: const Icon(Icons.notifications_outlined, size: 17),
                    label: const Text('通知测试'),
                  ),
                  OutlinedButton.icon(
                    onPressed: updater,
                    icon: const Icon(Icons.system_update_alt, size: 17),
                    label: const Text('检查后端更新'),
                  ),
                  OutlinedButton.icon(
                    onPressed: killServer,
                    icon: const Icon(Icons.power_settings_new, size: 17),
                    label: const Text('结束后端服务'),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}
