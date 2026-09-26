import 'dart:math' as math;

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/log/log_widget.dart';
import 'package:oasx/modules/server/controllers/server_controller.dart';
import 'package:oasx/modules/server/widgets/deploy_section_panel.dart';

import 'solana_widgets.dart';

/// Deployment content embedded in the console, without another page scaffold.
/// Registering the controller reads its configuration; only the explicit run
/// button invokes deployment. The controller survives closing this content so
/// an in-progress deployment and its logs remain available on return.
class SolanaDeployment extends StatefulWidget {
  const SolanaDeployment({super.key, required this.onBack, this.controller});

  final VoidCallback onBack;
  final ServerController? controller;

  @override
  State<SolanaDeployment> createState() => _SolanaDeploymentState();
}

class _SolanaDeploymentState extends State<SolanaDeployment> {
  late final ServerController _controller;
  String? _error;

  @override
  void initState() {
    super.initState();
    if (Get.isRegistered<ServerController>()) {
      _controller = Get.find<ServerController>();
      assert(
        widget.controller == null || identical(widget.controller, _controller),
      );
    } else {
      _controller = Get.put<ServerController>(
        widget.controller ?? ServerController(),
        permanent: true,
      );
    }
  }

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, bounds) {
      final height = bounds.hasBoundedHeight
          ? bounds.maxHeight
          : MediaQuery.sizeOf(context).height * .8;
      final panelHeight = (height * .48).clamp(260.0, 440.0);
      return SizedBox(
        height: height,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Wrap(
              spacing: 12,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                TextButton.icon(
                  key: const ValueKey('deployment-back'),
                  onPressed: widget.onBack,
                  icon: const Icon(Icons.arrow_back_rounded, size: 17),
                  label: const Text('返回应用设置'),
                ),
                Text('后端部署', style: Theme.of(context).textTheme.titleLarge),
                Obx(
                  () => StateBadge(
                    _controller.isDeployLoading.value ? '部署进行中' : '等待操作',
                    color: _controller.isDeployLoading.value
                        ? solanaAmber
                        : solanaBlue,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            Expanded(
              child: SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _pathCard(),
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      Notice(_error!, error: true),
                    ],
                    const SizedBox(height: 14),
                    // Reuse the structured editor and its save/copy/import/
                    // export controls, without the old ServerView navigation.
                    Obx(
                      () => !_controller.rootPathAuthenticated.value
                          ? const Surface(
                              child: Text('选择有效的后端目录后，可编辑、导入和导出部署配置。'),
                            )
                          : AbsorbPointer(
                              absorbing: _controller.isDeployLoading.value,
                              child: Opacity(
                                opacity: _controller.isDeployLoading.value
                                    ? .6
                                    : 1,
                                child: LayoutBuilder(
                                  builder: (context, constraints) {
                                    // The existing toolbar needs room for four actions.
                                    // Keep them reachable in a narrow embedded dialog.
                                    return SingleChildScrollView(
                                      scrollDirection: Axis.horizontal,
                                      child: SizedBox(
                                        width: math.max(
                                          440,
                                          constraints.maxWidth,
                                        ),
                                        child: DeploySectionPanel(
                                          maxHeight: panelHeight,
                                        ),
                                      ),
                                    );
                                  },
                                ),
                              ),
                            ),
                    ),
                    const SizedBox(height: 4),
                    SizedBox(
                      height: panelHeight,
                      child: LogWidget(
                        controller: _controller,
                        title: '部署日志',
                        enableCopy: true,
                        enableAutoScroll: true,
                        enableClear: true,
                        enableCollapse: false,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      );
    },
  );

  Widget _pathCard() => Obx(() {
    final authenticated = _controller.rootPathAuthenticated.value;
    final deploying = _controller.isDeployLoading.value;
    return Surface(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SectionHeading(
            '部署目录',
            subtitle: '选择包含部署配置、Python 和 Git 工具的 OAS 后端目录',
            trailing: StateBadge(
              authenticated ? '目录已验证' : '需要选择有效目录',
              color: authenticated ? solanaGreen : solanaAmber,
            ),
          ),
          const SizedBox(height: 14),
          SelectableText(
            _controller.rootPathServer.value.isEmpty
                ? '尚未选择目录'
                : _controller.rootPathServer.value,
            key: const ValueKey('deployment-root-path'),
            style: solanaFormTextStyle,
          ),
          const SizedBox(height: 14),
          Wrap(
            spacing: 12,
            runSpacing: 8,
            children: [
              OutlinedButton.icon(
                key: const ValueKey('deployment-select-directory'),
                onPressed: deploying ? null : _selectDirectory,
                icon: const Icon(Icons.folder_open_rounded, size: 17),
                label: const Text('选择后端目录'),
              ),
              FilledButton.icon(
                key: const ValueKey('deployment-run'),
                onPressed: authenticated && !deploying ? _run : null,
                icon: deploying
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.play_arrow_rounded, size: 18),
                label: Text(deploying ? '正在部署…' : '部署并启动后端'),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(
            '编辑部署配置后先保存，再执行部署。日志保留在本页，返回后可继续查看。',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  });

  Future<void> _selectDirectory() async {
    try {
      final path = await FilePicker.platform.getDirectoryPath();
      if (path == null || !mounted) return;
      _controller.updateRootPathServer(path);
      setState(() => _error = null);
    } catch (_) {
      if (mounted) setState(() => _error = '无法选择或读取后端目录，请检查目录权限。');
    }
  }

  Future<void> _run() async {
    if (!_controller.rootPathAuthenticated.value ||
        _controller.isDeployLoading.value) {
      return;
    }
    setState(() => _error = null);
    try {
      await _controller.run();
    } catch (_) {
      if (mounted) setState(() => _error = '部署未完成，请查看部署日志中的具体原因。');
    }
  }
}
