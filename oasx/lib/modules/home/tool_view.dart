import 'package:flutter/material.dart';
import 'package:oasx/api/api_client.dart';

/// A single scrollable dialog, including its title and actions.
class NotifyTest extends StatefulWidget {
  const NotifyTest({super.key, this.onSend});

  final Future<bool> Function(String setting, String title, String content)?
  onSend;

  @override
  State<NotifyTest> createState() => _NotifyTestState();
}

class _NotifyTestState extends State<NotifyTest> {
  final _form = GlobalKey<FormState>();
  final _config = TextEditingController(text: 'provider:');
  final _title = TextEditingController(text: '推送测试');
  final _content = TextEditingController(text: '这是一条 OASX 测试消息。');
  bool _sending = false;
  String? _result;

  @override
  void dispose() {
    _config.dispose();
    _title.dispose();
    _content.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    if (_sending || !_form.currentState!.validate()) return;
    setState(() {
      _sending = true;
      _result = null;
    });
    var success = false;
    try {
      success =
          await (widget.onSend ??
              (setting, title, content) => ApiClient().notifyTest(
                setting,
                title,
                content,
                showFeedback: false,
              ))(_config.text, _title.text, _content.text);
    } catch (_) {
      // Configuration may contain tokens; do not display request details.
      success = false;
    }
    if (!mounted) return;
    setState(() {
      _sending = false;
      _result = success ? '测试消息已发送' : '发送失败，请检查推送配置和后端连接。';
    });
  }

  Widget _field(
    String label,
    TextEditingController controller, {
    int minLines = 1,
    int maxLines = 1,
    String? help,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(label, style: Theme.of(context).textTheme.labelLarge),
        const SizedBox(height: 8),
        TextFormField(
          key: ValueKey('notify-$label'),
          controller: controller,
          enabled: !_sending,
          minLines: minLines,
          maxLines: maxLines,
          keyboardType: maxLines > 1
              ? TextInputType.multiline
              : TextInputType.text,
          textInputAction: maxLines > 1
              ? TextInputAction.newline
              : TextInputAction.next,
          decoration: const InputDecoration(
            filled: true,
            contentPadding: EdgeInsets.all(12),
            errorMaxLines: 3,
          ),
          validator: (value) {
            if (value == null || value.trim().isEmpty) return '请填写$label';
            if (controller == _config && value.trim() == 'provider:') {
              return '请填写完整的推送配置';
            }
            return null;
          },
        ),
        if (help != null) ...[
          const SizedBox(height: 8),
          Text(help, style: Theme.of(context).textTheme.bodySmall),
        ],
      ],
    );
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('推送测试'),
    scrollable: true,
    insetPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
    content: SizedBox(
      width: 480,
      child: Form(
        key: _form,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _field(
              '推送配置',
              _config,
              minLines: 4,
              maxLines: 8,
              help: '按「消息推送」中的说明填写完整配置，保留原有换行和缩进。',
            ),
            const SizedBox(height: 20),
            _field('发送主题', _title),
            const SizedBox(height: 20),
            _field('发送内容', _content, minLines: 3, maxLines: 6),
            if (_result != null) ...[
              const SizedBox(height: 16),
              Semantics(liveRegion: true, child: Text(_result!)),
            ],
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('关闭'),
      ),
      FilledButton.icon(
        onPressed: _sending ? null : _send,
        icon: _sending
            ? const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : const Icon(Icons.send_outlined, size: 18),
        label: Text(_sending ? '发送中…' : '发送测试'),
      ),
    ],
  );
}
