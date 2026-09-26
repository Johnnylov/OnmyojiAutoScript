import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/home/controllers/dashboard_controller.dart';
import 'package:oasx/modules/settings/controllers/settings_controller.dart';
import 'package:oasx/service/script_service.dart';

import 'solana_controller.dart';

const _startupBusyMessage = '启动连接检查尚未结束，请等待完成后再保存连接设置。';
bool _startupChecking() =>
    Get.isRegistered<HomeDashboardController>() &&
    Get.find<HomeDashboardController>().isStartupChecking.value;

class _StartupConnectionInProgress implements Exception {}

/// Local preferences only. Credentials never enter Solana's mutation/audit API.
abstract class SolanaConnectionPreferences {
  String get address;
  String get username;
  String get password;
  Future<void> save({
    required String address,
    required String username,
    required String password,
  });
  Future<void> synchronize();
}

class SettingsConnectionPreferences implements SolanaConnectionPreferences {
  final SettingsController settings;
  SettingsConnectionPreferences(this.settings);
  @override
  String get address => settings.address.value;
  @override
  String get username => settings.username.value;
  @override
  String get password => settings.password.value;

  @override
  Future<void> save({
    required String address,
    required String username,
    required String password,
  }) async {
    if (_startupChecking()) throw _StartupConnectionInProgress();
    // Retire legacy per-profile streams as well as the console's event stream.
    if (Get.isRegistered<ScriptService>()) {
      await Get.find<ScriptService>().resetDashboardState();
    }
    // A startup check may have begun while old streams were closing.
    if (_startupChecking()) throw _StartupConnectionInProgress();
    settings.updateAddress(address);
    settings.updateUsername(username);
    settings.updatePassword(password);
    await settings.storage.save();
    settings.consumeLoginConfigChanged();
  }

  @override
  Future<void> synchronize() async {
    if (Get.isRegistered<HomeDashboardController>()) {
      // Existing read-only login refresh: checks the address, refreshes profiles
      // and translations. Unlike settings-leave, it cannot auto-deploy/auto-run.
      await Get.find<HomeDashboardController>().retryStartupConnection();
    }
  }
}

Future<bool> showSolanaConnectionSettings(
  BuildContext context,
  SolanaController controller, {
  SolanaConnectionPreferences? preferences,
  bool Function()? isStartupChecking,
}) async {
  return await showDialog<bool>(
        context: context,
        barrierDismissible: false,
        builder: (_) => SolanaConnectionSettingsDialog(
          controller: controller,
          isStartupChecking: isStartupChecking,
          preferences:
              preferences ??
              SettingsConnectionPreferences(Get.find<SettingsController>()),
        ),
      ) ??
      false;
}

class SolanaConnectionSettingsDialog extends StatefulWidget {
  final SolanaController controller;
  final SolanaConnectionPreferences preferences;
  final bool Function()? isStartupChecking;
  const SolanaConnectionSettingsDialog({
    super.key,
    required this.controller,
    required this.preferences,
    this.isStartupChecking,
  });
  @override
  State<SolanaConnectionSettingsDialog> createState() =>
      _SolanaConnectionSettingsDialogState();
}

class _SolanaConnectionSettingsDialogState
    extends State<SolanaConnectionSettingsDialog> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _address;
  late final TextEditingController _username;
  late final TextEditingController _password;
  bool _saving = false;
  bool _saved = false;
  String? _error;
  Worker? _startupWorker;
  bool get _startupInProgress =>
      widget.isStartupChecking?.call() ?? _startupChecking();

  @override
  void initState() {
    super.initState();
    _address = TextEditingController(text: widget.preferences.address);
    _username = TextEditingController(text: widget.preferences.username);
    _password = TextEditingController(text: widget.preferences.password);
    if (Get.isRegistered<HomeDashboardController>()) {
      _startupWorker = ever(
        Get.find<HomeDashboardController>().isStartupChecking,
        (_) {
          if (mounted) setState(() {});
        },
      );
    }
  }

  String? _validateAddress(String? value) {
    final address = (value ?? '').trim();
    if (address.isEmpty) {
      return null; // Preserve the original default-address behavior.
    }
    final uri = Uri.tryParse(
      address.contains('://') ? address : 'http://$address',
    );
    if (uri == null ||
        !['http', 'https'].contains(uri.scheme) ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment) {
      return '请输入有效的 HTTP/HTTPS 后端地址';
    }
    return null;
  }

  Future<void> _save() async {
    if (_startupInProgress) {
      setState(() => _error = _startupBusyMessage);
      return;
    }
    if (_saving || !_form.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await widget.controller.reconnectBackend(
        updateConnection: () async {
          if (_startupInProgress) throw _StartupConnectionInProgress();
          await widget.preferences.save(
            address: _address.text.trim(),
            username: _username.text.trim(),
            password: _password.text,
          );
          _saved = true;
          await widget.preferences.synchronize();
        },
      );
      if (!mounted) return;
      if (widget.controller.connected && widget.controller.supported) {
        Navigator.of(context).pop(true);
      } else {
        setState(() => _error = '连接设置已保存，但后端尚未连接。请检查地址和后端服务后重试。');
      }
    } catch (error) {
      if (mounted) {
        setState(
          () => _error = error is _StartupConnectionInProgress
              ? _startupBusyMessage
              : _saved
              ? '连接设置已保存，重新连接未完成。请检查后端服务后重试。'
              : '连接设置未能完成保存，请重试。',
        );
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !_saving && !_saved,
    child: AlertDialog(
      title: const Text('连接设置'),
      content: SizedBox(
        width: 420,
        child: SingleChildScrollView(
          child: Form(
            key: _form,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                TextFormField(
                  key: const ValueKey('connection-address'),
                  controller: _address,
                  enabled: !_saving,
                  validator: _validateAddress,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(
                    labelText: '后端地址',
                    hintText: 'http://127.0.0.1:22288',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
                TextFormField(
                  key: const ValueKey('connection-username'),
                  controller: _username,
                  enabled: !_saving,
                  autofillHints: const [AutofillHints.username],
                  decoration: const InputDecoration(
                    labelText: '用户名',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
                TextFormField(
                  key: const ValueKey('connection-password'),
                  controller: _password,
                  enabled: !_saving,
                  obscureText: true,
                  enableSuggestions: false,
                  autocorrect: false,
                  decoration: const InputDecoration(
                    labelText: '密码',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                const Text('保存在本机。保存后重新连接后端。', style: TextStyle(fontSize: 12)),
                if (_startupInProgress)
                  const Padding(
                    padding: EdgeInsets.only(top: 12),
                    child: Text(_startupBusyMessage),
                  ),
                if (_error != null && !_startupInProgress)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(
                      _error!,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: _saving ? null : () => Navigator.of(context).pop(_saved),
          child: Text(_saved ? '关闭' : '取消'),
        ),
        FilledButton(
          onPressed: _saving || widget.controller.busy || _startupInProgress
              ? null
              : _save,
          child: Text(_saving ? '正在连接…' : '保存并连接'),
        ),
      ],
    ),
  );

  @override
  void dispose() {
    _startupWorker?.dispose();
    _address.dispose();
    _username.dispose();
    _password.dispose();
    super.dispose();
  }
}
