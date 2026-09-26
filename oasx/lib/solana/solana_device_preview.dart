import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';

import 'solana_controller.dart';
import 'solana_widgets.dart';

/// Only a changed frame allocates new image bytes. Status updates and scrolling
/// can reuse the same image-cache entry without decoding a full screenshot.
class SolanaDevicePreview extends StatefulWidget {
  const SolanaDevicePreview({super.key, required this.controller});
  final SolanaController controller;

  @override
  State<SolanaDevicePreview> createState() => _SolanaDevicePreviewState();
}

class _SolanaDevicePreviewState extends State<SolanaDevicePreview> {
  String? _encoded;
  Uint8List? _bytes;

  @override
  Widget build(BuildContext context) => RepaintBoundary(
    child: ListenableBuilder(
      listenable: widget.controller.previewChanges,
      builder: (context, _) {
        final c = widget.controller;
        final data = c.preview.data;
        final encoded = data?['available'] == true
            ? (data?['image_base64'] as String?)
            : null;
        if (encoded != _encoded) {
          _encoded = encoded;
          try {
            _bytes = encoded == null ? null : base64Decode(encoded);
          } catch (_) {
            _bytes = null;
          }
        }
        return ClipRRect(
          borderRadius: BorderRadius.circular(12),
          child: DecoratedBox(
            decoration: const BoxDecoration(color: Color(0xFF273141)),
            child: Stack(
              fit: StackFit.expand,
              children: [
                if (_bytes != null)
                  Image.memory(
                    _bytes!,
                    fit: BoxFit.cover,
                    cacheWidth: 528,
                    gaplessPlayback: true,
                    errorBuilder: (_, __, ___) => _empty('画面无法显示'),
                  )
                else
                  _empty(c.preview.error == null ? '暂无设备画面' : '设备画面暂不可用'),
                if (_bytes != null)
                  Positioned(
                    right: 5,
                    bottom: 5,
                    child: Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 5,
                        vertical: 2,
                      ),
                      color: Colors.black45,
                      child: Text(
                        '${displayTime(data?['occurred_at'])}${data?['stale'] == true || !c.connected ? ' · 最近画面' : ''}',
                        style: const TextStyle(
                          fontSize: 9,
                          color: Colors.white70,
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
        );
      },
    ),
  );

  Widget _empty(String label) => Column(
    mainAxisAlignment: MainAxisAlignment.center,
    children: [
      const Icon(
        Icons.desktop_windows_outlined,
        size: 29,
        color: Color(0xFF8793A5),
      ),
      const SizedBox(height: 8),
      Text(
        label,
        style: const TextStyle(fontSize: 11, color: Color(0xFFB2BDCE)),
      ),
      const SizedBox(height: 3),
      const Text(
        '等待执行器提供最近画面',
        style: TextStyle(fontSize: 9, color: Color(0xFF8290A6)),
      ),
    ],
  );
}
