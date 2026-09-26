import 'dart:async';

import 'package:flutter/material.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/home/widgets/log_center_panel.dart';
import 'package:oasx/modules/log/script_log_browser_controller.dart';

/// Compact presentation of the existing real log browser. Its controller owns
/// the bounded window, deduplication, SSE and cursor recovery.
class SolanaTerminal extends StatefulWidget {
  final String scriptName;
  final bool autoConnect;
  final ScriptLogBrowserController? controller;
  const SolanaTerminal({
    super.key,
    required this.scriptName,
    this.autoConnect = true,
    this.controller,
  });
  @override
  State<SolanaTerminal> createState() => _SolanaTerminalState();
}

class _SolanaTerminalState extends State<SolanaTerminal> {
  ScriptLogBrowserController? _controller;
  final _scroll = ScrollController();
  bool _follow = true;
  @override
  void initState() {
    super.initState();
    _bind();
  }

  void _bind() {
    if (widget.scriptName.isEmpty) return;
    _controller =
        widget.controller ??
        ScriptLogBrowserController(scriptName: widget.scriptName);
    _controller!.scrollToBottom = () {
      if (!mounted || !_follow || !_scroll.hasClients) return;
      _scroll.jumpTo(_scroll.position.maxScrollExtent);
    };
    if (widget.autoConnect) unawaited(_controller!.refreshLatest());
  }

  @override
  void didUpdateWidget(covariant SolanaTerminal oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.scriptName != widget.scriptName ||
        oldWidget.autoConnect != widget.autoConnect) {
      if (_controller != null) {
        _controller!.revision++;
        _controller!.onClose();
      }
      _controller = null;
      _bind();
    }
  }

  @override
  void dispose() {
    if (_controller != null) {
      _controller!.revision++;
      _controller!.onClose();
    }
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final controller = _controller;
    if (controller == null) return _empty('选择配置后显示运行日志');
    return Obx(() {
      if (controller.infoLoading.value && controller.lines.isEmpty) {
        return _empty('正在读取运行日志…');
      }
      if (controller.lines.isEmpty) {
        return _empty(
          controller.infoError.value.isNotEmpty ? '运行日志暂不可用' : '暂无运行日志',
        );
      }
      return Theme(
        data: Theme.of(context).copyWith(
          textTheme: Theme.of(context).textTheme.copyWith(
            bodyMedium: const TextStyle(
              fontFamily: 'Consolas',
              fontFamilyFallback: ['Microsoft YaHei'],
              fontSize: 10.7,
              color: Color(0xFF3C3647),
              height: 1.2,
            ),
          ),
        ),
        child: Stack(
          children: [
            Positioned.fill(
              child: NotificationListener<ScrollNotification>(
                onNotification: (notification) {
                  if (notification is ScrollUpdateNotification &&
                      notification.dragDetails != null) {
                    _follow = notification.metrics.extentAfter < 40;
                  }
                  return false;
                },
                child: SelectionArea(
                  child: ListView.builder(
                    controller: _scroll,
                    padding: EdgeInsets.zero,
                    itemCount: controller.lines.length,
                    itemBuilder: (context, index) => LogCenterLogText(
                      line: controller.lines[index].text,
                      maxLines: 1,
                      overflow: TextOverflow.clip,
                    ),
                  ),
                ),
              ),
            ),
            if (controller.infoError.value.isNotEmpty)
              Positioned(
                right: 0,
                bottom: 0,
                child: Tooltip(
                  message: '日志连接需要恢复',
                  child: IconButton(
                    onPressed: controller.refreshLatest,
                    icon: const Icon(Icons.refresh, size: 14),
                  ),
                ),
              ),
          ],
        ),
      );
    });
  }

  Widget _empty(String message) => Center(
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        const Icon(Icons.terminal, size: 22, color: Color(0xFFA498B3)),
        const SizedBox(height: 7),
        Text(
          message,
          style: const TextStyle(fontSize: 11, color: Color(0xFF958A9F)),
        ),
      ],
    ),
  );
}
