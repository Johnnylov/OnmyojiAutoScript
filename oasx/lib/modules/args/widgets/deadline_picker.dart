part of args;

/// Empty strings retain the original unlimited setting on disk.
class DeadlinePicker extends StatefulWidget {
  const DeadlinePicker({
    super.key,
    required this.value,
    required this.onChanged,
    this.enabled = true,
    this.errorText,
  });

  final String value;
  final ValueChanged<String> onChanged;
  final bool enabled;
  final String? errorText;

  @override
  State<DeadlinePicker> createState() => _DeadlinePickerState();
}

class _DeadlinePickerState extends State<DeadlinePicker> {
  String? _lastDeadline;

  bool get _checked => widget.value.trim().isNotEmpty;

  String _defaultDeadline() {
    final now = DateTime.now();
    return DateTime(now.year, now.month, now.day, 23, 59, 59)
        .toUtc()
        .toIso8601String();
  }

  @override
  void initState() {
    super.initState();
    if (_checked) _lastDeadline = widget.value;
  }

  @override
  void didUpdateWidget(covariant DeadlinePicker oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_checked) _lastDeadline = widget.value;
  }

  @override
  Widget build(BuildContext context) {
    final parsed = DateTime.tryParse(widget.value)?.toLocal();
    final text = parsed == null
        ? widget.value
        : parsed.toIso8601String().split('.').first.replaceFirst('T', ' ');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        CheckboxListTile(
          key: const ValueKey('deadline-enabled'),
          value: _checked,
          controlAffinity: ListTileControlAffinity.leading,
          contentPadding: EdgeInsets.zero,
          dense: true,
          title: const Text('启用活动截止时间'),
          onChanged: !widget.enabled
              ? null
              : (checked) => widget.onChanged(
                  checked == true ? _lastDeadline ?? _defaultDeadline() : '',
                ),
        ),
        if (_checked)
          IgnorePointer(
            ignoring: !widget.enabled,
            child: DateTimePicker(
              key: const ValueKey('deadline-date-time'),
              value: text,
              readableField: true,
              onChange: (value) {
                final selected = DateTime.tryParse(value);
                if (selected != null) {
                  widget.onChanged(selected.toUtc().toIso8601String());
                }
              },
            ),
          ),
        Padding(
          padding: const EdgeInsets.only(top: 4),
          child: Text(
            _checked ? '按当前电脑的本地时间选择。' : '未启用，不限制活动截止时间。',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ),
        if (widget.errorText != null)
          Text(
            widget.errorText!,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
      ],
    );
  }
}
