import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:styled_widget/styled_widget.dart';

import 'package:oasx/modules/settings/controllers/settings_controller.dart';
import 'package:oasx/service/autostart_service.dart';
import 'package:oasx/service/app_exit_service.dart';
import 'package:oasx/service/app_update_service.dart';
import 'package:oasx/service/locale_service.dart';
import 'package:oasx/service/window_service.dart';
import 'package:oasx/translation/i18n_content.dart';
import 'package:oasx/utils/check_version.dart';
import 'package:oasx/utils/platform_utils.dart';

class LanguageToggle extends StatelessWidget {
  const LanguageToggle({super.key});

  @override
  Widget build(BuildContext context) {
    final localeService = Get.find<LocaleService>();
    return Obx(() {
      final isSelected = switch (localeService.language.value) {
        'zh-CN' => [true, false],
        'en-US' => [false, true],
        _ => [true, false],
      };
      return ToggleButtons(
        isSelected: isSelected,
        onPressed: (index) {
          localeService.switchLanguage(index == 0 ? 'zh-CN' : 'en-US');
        },
        borderRadius: BorderRadius.circular(10),
        children: [
          Text(I18n.zhCn.tr).paddingSymmetric(horizontal: 10),
          Text(I18n.enUs.tr).paddingSymmetric(horizontal: 10),
        ],
      ).constrained(maxHeight: 40);
    });
  }
}

class WindowStateSwitch extends StatelessWidget {
  const WindowStateSwitch({super.key});

  @override
  Widget build(BuildContext context) {
    final windowService = Get.find<WindowService>();
    return Obx(
      () => Switch(
        value: windowService.enableWindowState.value,
        onChanged: windowService.updateWindowStateEnable,
      ),
    );
  }
}

class SystemTraySwitch extends StatelessWidget {
  const SystemTraySwitch({super.key});

  @override
  Widget build(BuildContext context) {
    final windowService = Get.find<WindowService>();
    return Obx(
      () => Switch(
        value: windowService.enableSystemTray.value,
        onChanged: windowService.updateSystemTrayEnable,
      ),
    );
  }
}

class ShutdownOasOnExitSwitch extends StatelessWidget {
  const ShutdownOasOnExitSwitch({super.key});

  @override
  Widget build(BuildContext context) {
    final appExitService = Get.find<AppExitService>();
    return Obx(
      () => Switch(
        value: appExitService.shutdownOasOnExit.value,
        onChanged: appExitService.updateShutdownOasOnExit,
      ),
    );
  }
}

class LaunchAtStartupSwitch extends StatelessWidget {
  const LaunchAtStartupSwitch({super.key});

  @override
  Widget build(BuildContext context) {
    final autoStartService = Get.find<AutoStartService>();
    return Obx(
      () => Switch(
        value: autoStartService.enableLaunchAtStartup.value,
        onChanged: autoStartService.isApplying.value
            ? null
            : autoStartService.updateLaunchAtStartupEnable,
      ),
    );
  }
}

class UpdateProxyUrlField extends StatefulWidget {
  const UpdateProxyUrlField({super.key});

  @override
  State<UpdateProxyUrlField> createState() => _UpdateProxyUrlFieldState();
}

class _UpdateProxyUrlFieldState extends State<UpdateProxyUrlField> {
  late final TextEditingController _controller;
  late final FocusNode _focusNode;
  late final SettingsController _settingsController;

  @override
  void initState() {
    super.initState();
    _settingsController = Get.find<SettingsController>();
    _controller = TextEditingController(
      text: _settingsController.updateProxyUrl.value,
    );
    _focusNode = FocusNode();
  }

  @override
  void dispose() {
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Obx(() {
      _syncTextController();
      return SizedBox(
        width: 220,
        child: TextField(
          controller: _controller,
          focusNode: _focusNode,
          keyboardType: TextInputType.url,
          scrollPadding: EdgeInsets.only(
            left: 12,
            top: 12,
            right: 12,
            bottom: MediaQuery.viewInsetsOf(context).bottom + 24,
          ),
          textInputAction: TextInputAction.done,
          decoration: const InputDecoration(hintText: 'http://127.0.0.1:7897'),
          onTapOutside: PlatformUtils.isWeb
              ? null
              : (_) => _focusNode.unfocus(),
          onEditingComplete: PlatformUtils.isWeb ? null : _focusNode.unfocus,
          onChanged: _settingsController.updateUpdateProxyUrl,
        ),
      );
    });
  }

  void _syncTextController() {
    if (_focusNode.hasFocus) {
      return;
    }
    final current = _settingsController.updateProxyUrl.value;
    if (_controller.text == current) {
      return;
    }
    _controller.value = TextEditingValue(
      text: current,
      selection: TextSelection.collapsed(offset: current.length),
    );
  }
}

class CheckUpdateButton extends StatelessWidget {
  const CheckUpdateButton({super.key});

  @override
  Widget build(BuildContext context) {
    final appUpdateService = Get.find<AppUpdateService>();
    return Obx(() {
      return ElevatedButton(
        onPressed: appUpdateService.isCheckingForUpdates.value
            ? null
            : () async => await checkUpdate(showTip: true, forceCheck: true),
        child: appUpdateService.isCheckingForUpdates.value
            ? const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : Text(I18n.executeUpdate.tr),
      );
    });
  }
}
