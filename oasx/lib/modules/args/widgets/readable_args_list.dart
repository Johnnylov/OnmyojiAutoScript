part of args;

/// Keep each field in the scrollable's lazy sliver, instead of laying out an
/// entire expanded task form for every frame. Models still live in the draft
/// controller. The sliver retains focused editors through TextField's built-in
/// keep-alive; unfocused off-screen fields can be recreated from their drafts.
class _ReadableArgsList extends StatefulWidget {
  const _ReadableArgsList({
    required this.editor,
    required this.groupNames,
    required this.selectedScript,
    required this.selectedTask,
  });

  final Args editor;
  final List<String> groupNames;
  final String selectedScript;
  final String selectedTask;

  @override
  State<_ReadableArgsList> createState() => _ReadableArgsListState();
}

class _ReadableArgsListState extends State<_ReadableArgsList> {
  final _collapsed = <String>{};

  @override
  void didUpdateWidget(covariant _ReadableArgsList oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.selectedScript != widget.selectedScript ||
        oldWidget.selectedTask != widget.selectedTask) {
      _collapsed.clear();
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = Get.find<ArgsController>();
    final rows = <({String group, int? member, bool last})>[];
    for (final name in widget.groupNames) {
      final members = controller.groupsData.value[name]?.members ?? const [];
      final expanded = !_collapsed.contains(name);
      rows.add((group: name, member: null, last: !expanded || members.isEmpty));
      if (expanded) {
        for (var index = 0; index < members.length; index++) {
          rows.add((
            group: name,
            member: index,
            last: index == members.length - 1,
          ));
        }
      }
    }
    String rowKey(({String group, int? member, bool last}) row) {
      final suffix = row.member == null
          ? 'header'
          : (controller.groupsData.value[row.group]!.members[row.member!]
                    as ArgumentModel)
                .title;
      return '${widget.selectedScript}/${widget.selectedTask}/${row.group}/$suffix';
    }

    final indexes = <String, int>{
      for (var index = 0; index < rows.length; index++)
        rowKey(rows[index]): index,
    };
    return SelectionArea(
      child: ListView.builder(
        key: PageStorageKey(
          'args-scroll-${widget.selectedScript}-${widget.selectedTask}',
        ),
        padding: const EdgeInsets.fromLTRB(10, 0, 10, 10),
        itemCount: rows.length,
        findChildIndexCallback: (key) =>
            key is ValueKey<String> ? indexes[key.value] : null,
        itemBuilder: (context, index) {
          final row = rows[index];
          final group = controller.groupsData.value[row.group]!;
          final memberIndex = row.member;
          final expanded = !_collapsed.contains(row.group);
          final color = widget.editor._groupBackgroundColor(
            context,
            widget.selectedScript,
            widget.selectedTask,
            row.group,
          );
          return KeyedSubtree(
            key: ValueKey(rowKey(row)),
            child: Padding(
              padding: EdgeInsets.only(bottom: row.last ? 10 : 0),
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: color,
                  borderRadius: BorderRadius.vertical(
                    top: memberIndex == null
                        ? const Radius.circular(10)
                        : Radius.zero,
                    bottom: row.last ? const Radius.circular(10) : Radius.zero,
                  ),
                ),
                child: memberIndex == null
                    ? Column(
                        children: [
                          ListTile(
                            key: ValueKey('args-group-toggle-${row.group}'),
                            contentPadding: const EdgeInsets.symmetric(
                              horizontal: 16,
                            ),
                            leading: widget.editor.groupDraggable
                                ? widget.editor._buildGroupDragHandle(
                                    context,
                                    widget.selectedScript,
                                    widget.selectedTask,
                                    row.group,
                                  )
                                : null,
                            title: Text(configGroupLabel(row.group)),
                            trailing: Icon(
                              expanded ? Icons.expand_less : Icons.expand_more,
                            ),
                            onTap: () => setState(() {
                              if (expanded) {
                                _collapsed.add(row.group);
                              } else {
                                _collapsed.remove(row.group);
                              }
                            }),
                          ),
                          if (expanded && group.members.isNotEmpty)
                            const Padding(
                              padding: EdgeInsets.symmetric(horizontal: 16),
                              child: Divider(height: 12),
                            ),
                        ],
                      )
                    : Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        child: ArgumentView(
                          key: ValueKey(
                            'args-${widget.selectedScript}-${widget.selectedTask}-${row.group}-${(group.members[memberIndex] as ArgumentModel).title}',
                          ),
                          scriptName: widget.editor.scriptName,
                          taskName: widget.editor.taskName,
                          setArgument:
                              widget.editor.setArgumentOverride ??
                              controller.setArgument,
                          getGroupName: group.getGroupName,
                          lockImmediateScheduling:
                              widget.editor.lockImmediateScheduling,
                          readableLayout: true,
                          index: memberIndex,
                        ),
                      ),
              ),
            ),
          );
        },
      ),
    );
  }
}
