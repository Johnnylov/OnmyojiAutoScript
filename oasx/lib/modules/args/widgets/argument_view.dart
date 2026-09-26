part of args;

class ArgumentView extends StatefulWidget {
  const ArgumentView({
    required this.setArgument,
    required this.getGroupName,
    required this.index,
    this.lockImmediateScheduling = false,
    this.readableLayout = false,
    Key? key,
    this.scriptName,
    this.taskName,
  }) : super(key: key);

  final SetArgumentCallback setArgument;
  final String Function() getGroupName;
  final int index;
  final bool lockImmediateScheduling;
  final bool readableLayout;
  final String? scriptName;
  final String? taskName;

  @override
  State<ArgumentView> createState() => _ArgumentViewState();
}

class _ArgumentViewState extends State<ArgumentView> {
  Timer? timer;
  bool landscape = true;
  bool _descriptionExpanded = false;
  late final TextEditingController _textController;
  late final FocusNode _focusNode;

  ArgumentModel get model {
    final controller = Get.find<ArgsController>();
    final groupsModel = controller.groupsData.value[widget.getGroupName()];
    return groupsModel!.members[widget.index];
  }

  ArgsController get _argsController => Get.find<ArgsController>();

  bool get _isProtectedImmediateScheduleField {
    return widget.lockImmediateScheduling &&
        ArgsController.isImmediateSchedulingField(
          widget.getGroupName(),
          model.title,
        );
  }

  @override
  void initState() {
    super.initState();
    _textController = TextEditingController(text: model.value.toString());
    _focusNode = FocusNode();
  }

  @override
  void dispose() {
    timer?.cancel();
    _textController.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Obx(() {
      landscape = MediaQuery.of(context).orientation == Orientation.landscape;
      _syncTextController();
      if (widget.readableLayout) return _readableField();
      final title = _title();
      final form = _buildFormSection();
      if (landscape) {
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: title),
            const SizedBox(width: 12),
            Expanded(child: form),
          ],
        ).padding(bottom: 8);
      }
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [title, form],
      ).padding(bottom: 8);
    });
  }

  Widget _readableField() {
    final theme = Theme.of(context);
    final description = model.description?.tr ?? '';
    final lengthy = description.length > 100 || description.contains('\n');
    return Padding(
      padding: const EdgeInsets.only(bottom: 17),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SelectableText(
            model.title.tr,
            style: theme.textTheme.bodyLarge?.copyWith(
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 6),
          _buildFormSection(),
          if (description.isNotEmpty) ...[
            const SizedBox(height: 5),
            SelectableText(
              description,
              maxLines: lengthy && !_descriptionExpanded ? 2 : null,
              style: theme.textTheme.bodySmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
                height: 1.45,
              ),
            ),
            if (lengthy)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: () => setState(
                    () => _descriptionExpanded = !_descriptionExpanded,
                  ),
                  style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 3),
                    minimumSize: const Size(0, 26),
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    textStyle: theme.textTheme.labelSmall,
                  ),
                  icon: Icon(
                    _descriptionExpanded
                        ? Icons.expand_less
                        : Icons.expand_more,
                    size: 15,
                  ),
                  label: Text(_descriptionExpanded ? '收起说明' : '展开完整说明'),
                ),
              ),
          ],
        ],
      ),
    );
  }

  InputDecoration _decoration(String? errorText, {bool textInput = false}) {
    if (!widget.readableLayout) return InputDecoration(errorText: errorText);
    final colors = Theme.of(context).colorScheme;
    final border = OutlineInputBorder(
      borderRadius: BorderRadius.circular(8),
      borderSide: BorderSide(
        color: colors.onSurfaceVariant.withValues(alpha: .38),
      ),
    );
    return InputDecoration(
      errorText: errorText,
      filled: true,
      fillColor: Theme.of(context).brightness == Brightness.light
          ? Colors.white
          : colors.surfaceContainerHigh,
      hintText: textInput ? '输入${model.title.tr}' : null,
      hintStyle: Theme.of(context).textTheme.bodyLarge?.copyWith(
        color: colors.onSurfaceVariant.withValues(alpha: .7),
      ),
      border: border,
      enabledBorder: border,
      focusedBorder: border.copyWith(
        borderSide: BorderSide(color: colors.primary, width: 1.3),
      ),
      contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
    );
  }

  Widget _title() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SelectableText(
          model.title.tr,
          style: Theme.of(context).textTheme.bodyMedium,
        ),
        if (model.description != null && model.description!.isNotEmpty)
          SelectableText(
            model.description!.tr,
            style: Theme.of(context).textTheme.bodySmall,
          ).padding(top: 4),
      ],
    );
  }

  Widget _buildFormSection() {
    final errorText = _argsController.fieldError(
      widget.getGroupName(),
      model.title,
    );
    final isLocked = _isProtectedImmediateScheduleField;
    final child = switch (model.type) {
      'boolean' => Checkbox(
        value: model.value,
        onChanged: isLocked ? null : onCheckboxChanged,
      ).alignment(Alignment.centerLeft),
      'string' => _buildTextField(errorText: errorText, enabled: !isLocked),
      'multi_line' => _buildTextField(
        errorText: errorText,
        maxLines: null,
        enabled: !isLocked,
      ),
      'number' => _buildTextField(
        errorText: errorText,
        keyboardType: TextInputType.number,
        inputFormatters: [FilteringTextInputFormatter.allow(RegExp('[-0-9.]'))],
        onChanged: _scheduleNumberChange,
        enabled: !isLocked,
      ),
      'integer' => _buildTextField(
        errorText: errorText,
        keyboardType: TextInputType.number,
        inputFormatters: [FilteringTextInputFormatter.allow(RegExp('[-0-9]'))],
        onChanged: _scheduleIntegerChange,
        enabled: !isLocked,
      ),
      'enum' => DropdownButtonFormField<String>(
        initialValue: model.value.toString(),
        isExpanded: true,
        menuMaxHeight: Get.height * 0.5,
        decoration: _decoration(errorText),
        dropdownColor: widget.readableLayout
            ? Theme.of(context).canvasColor
            : null,
        items: model.enumEnum!
            .map<DropdownMenuItem<String>>(
              (e) => DropdownMenuItem(
                value: e.toString(),
                child: Text(
                  e.toString().tr,
                  style: Theme.of(context).textTheme.bodyLarge,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            )
            .toList(),
        selectedItemBuilder: (context) => model.enumEnum!
            .map<Widget>(
              (e) => Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  e.toString().tr,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodyLarge,
                ),
              ),
            )
            .toList(),
        onChanged: isLocked ? null : onEnumChanged,
      ),
      'date_time' => _buildPicker(
        DateTimePicker(
          value: model.value,
          onChange: onDateTimeChanged,
          readableField: widget.readableLayout,
        ),
        errorText,
        enabled: !isLocked,
      ),
      'time_delta' => _buildPicker(
        TimeDeltaPicker(
          value: ensureTimeDeltaString(model.value),
          onChange: onTimeDeltaChanged,
          readableField: widget.readableLayout,
        ),
        errorText,
        enabled: !isLocked,
      ),
      'time' => _buildPicker(
        TimePicker(
          value: model.value,
          onChange: onTimeChanged,
          readableField: widget.readableLayout,
        ),
        errorText,
        enabled: !isLocked,
      ),
      _ => Text(model.value.toString()),
    };
    return child;
  }

  Widget _buildTextField({
    required String? errorText,
    bool enabled = true,
    int? maxLines = 1,
    TextInputType? keyboardType,
    List<TextInputFormatter>? inputFormatters,
    ValueChanged<String>? onChanged,
  }) {
    final keyboardInset = MediaQuery.viewInsetsOf(context).bottom;
    final isSingleLine = maxLines == 1;
    return TextFormField(
      key: ValueKey('argument-input-${widget.getGroupName()}-${model.title}'),
      controller: _textController,
      focusNode: _focusNode,
      enabled: enabled,
      maxLines: maxLines,
      keyboardType: keyboardType,
      inputFormatters: inputFormatters,
      style: widget.readableLayout
          ? Theme.of(context).textTheme.bodyLarge
          : null,
      scrollPadding: EdgeInsets.only(
        left: 12,
        top: 12,
        right: 12,
        bottom: keyboardInset > 0 ? keyboardInset + 24 : 24,
      ),
      textInputAction: isSingleLine
          ? TextInputAction.done
          : TextInputAction.newline,
      decoration: _decoration(errorText, textInput: true),
      onTapOutside: PlatformUtils.isWeb ? null : (_) => _focusNode.unfocus(),
      onEditingComplete: PlatformUtils.isWeb
          ? null
          : (isSingleLine ? _focusNode.unfocus : null),
      onChanged: onChanged ?? _scheduleStringChange,
    );
  }

  Widget _buildPicker(Widget picker, String? errorText, {bool enabled = true}) {
    return IgnorePointer(
      ignoring: !enabled,
      child: Opacity(
        opacity: enabled ? 1 : 0.6,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            picker,
            if (errorText != null && errorText.isNotEmpty)
              Text(
                errorText,
                style: TextStyle(
                  color: Theme.of(context).colorScheme.error,
                  fontSize: 12,
                ),
              ).padding(top: 4),
          ],
        ),
      ),
    );
  }

  void _syncTextController() {
    if (_focusNode.hasFocus) {
      return;
    }
    final nextValue = model.value.toString();
    if (_textController.text == nextValue) {
      return;
    }
    _textController.value = TextEditingValue(
      text: nextValue,
      selection: TextSelection.collapsed(offset: nextValue.length),
    );
  }

  void _scheduleStringChange(String value) {
    timer?.cancel();
    timer = Timer(
      const Duration(milliseconds: 150),
      () => onStringChanged(value),
    );
  }

  void _scheduleNumberChange(String value) {
    timer?.cancel();
    timer = Timer(
      const Duration(milliseconds: 150),
      () => onNumberChanged(value),
    );
  }

  void _scheduleIntegerChange(String value) {
    timer?.cancel();
    timer = Timer(
      const Duration(milliseconds: 150),
      () => onIntegerChanged(value),
    );
  }
}
