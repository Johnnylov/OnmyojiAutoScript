import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/common/widgets/add_config_dialog.dart';
import 'package:oasx/modules/home/config_actions.dart';
import 'package:oasx/service/script_service.dart';

import 'solana_api.dart';
import 'solana_controller.dart';
import 'solana_widgets.dart';

Future<void> showSolanaProfileManager(
  BuildContext context,
  SolanaController controller,
) async {
  await showDialog<void>(
    context: context,
    builder: (_) => _Profiles(controller: controller),
  );
}

class _Profiles extends StatefulWidget {
  final SolanaController controller;
  const _Profiles({required this.controller});
  @override
  State<_Profiles> createState() => _ProfilesState();
}

class _ProfilesState extends State<_Profiles> {
  bool _busy = false;
  String? _error;
  SolanaController get c => widget.controller;
  bool get _available => c.connected && Get.isRegistered<ScriptService>();

  @override
  void initState() {
    super.initState();
    c.addListener(_updated);
  }

  void _updated() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    c.removeListener(_updated);
    super.dispose();
  }

  Future<void> _run(
    Future<void> Function() operation, {
    bool requiresConnection = true,
  }) async {
    if (_busy) return;
    if (requiresConnection && !_available) {
      setState(() => _error = '配置服务尚未连接，请重新连接后再操作。');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await operation();
      await c.refreshAll();
    } catch (_) {
      if (mounted) setState(() => _error = '操作未完成，请刷新配置列表后检查结果。');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final available = _available;
    return AlertDialog(
      title: const Text('配置管理'),
      content: SizedBox(
        width: 540,
        height: 420,
        child: Column(
          children: [
            if (_error != null) Notice(_error!, error: true),
            if (!available) const Notice('配置服务尚未连接。'),
            Expanded(
              child: AnimatedBuilder(
                animation: c,
                builder: (context, _) => ListView(
                  children: objects(c.overview.data?['profiles']).map((
                    profile,
                  ) {
                    final name = textValue(profile['name']);
                    final editable = [
                      'inactive',
                      'stopped',
                    ].contains(profile['state']);
                    return ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: const Icon(Icons.folder_outlined, size: 20),
                      title: Text(name),
                      subtitle: Text(editable ? '已停止' : '停止后可重命名或删除'),
                      trailing: Wrap(
                        spacing: 2,
                        children: [
                          IconButton(
                            tooltip: '导出脱敏配置',
                            icon: const Icon(
                              Icons.file_upload_outlined,
                              size: 18,
                            ),
                            onPressed: _busy || !available
                                ? null
                                : () => _run(
                                    () =>
                                        ConfigActions.exportScript(name: name),
                                  ),
                          ),
                          IconButton(
                            tooltip: '重命名',
                            icon: const Icon(Icons.edit_outlined, size: 18),
                            onPressed: _busy || !available || !editable
                                ? null
                                : () => _run(() async {
                                    await ConfigActions.showRenameDialog(
                                      scriptService: Get.find<ScriptService>(),
                                      oldName: name,
                                    );
                                  }),
                          ),
                          IconButton(
                            tooltip: '删除配置',
                            icon: const Icon(Icons.delete_outline, size: 18),
                            onPressed: _busy || !available || !editable
                                ? null
                                : () => _run(
                                    () => ConfigActions.showDeleteDialog(
                                      scriptService: Get.find<ScriptService>(),
                                      name: name,
                                    ),
                                  ),
                          ),
                        ],
                      ),
                    );
                  }).toList(),
                ),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _busy ? null : () => Navigator.pop(context),
          child: const Text('关闭'),
        ),
        OutlinedButton.icon(
          onPressed: _busy
              ? null
              : () => _run(c.refreshAll, requiresConnection: false),
          icon: const Icon(Icons.refresh, size: 17),
          label: const Text('刷新'),
        ),
        FilledButton.icon(
          onPressed: _busy || !available
              ? null
              : () => _run(() async {
                  await showAddConfigDialog(context);
                }),
          icon: const Icon(Icons.add, size: 17),
          label: const Text('新增 / 导入配置'),
        ),
      ],
    );
  }
}
