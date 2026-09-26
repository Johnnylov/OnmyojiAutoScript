import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/args/index.dart';
import 'package:oasx/solana/solana_widgets.dart';

Future<ArgsController> mountLongForm(
  WidgetTester tester, {
  SaveArgumentCallback? save,
}) async {
  Get.testMode = true;
  await tester.binding.setSurfaceSize(const Size(720, 600));
  addTearDown(() async {
    await tester.binding.setSurfaceSize(null);
    Get.reset();
  });
  final controller = Get.put(ArgsController());
  await controller.loadGroups(
    config: 'fixture',
    task: 'Activity',
    stagingMode: true,
    preloadedGroups: {
      '活动设置': [
        for (var index = 0; index < 200; index++)
          {
            'name': '选项$index',
            'type': 'string',
            'value': '值$index',
            'description': '此处是参数说明，可以选中复制。',
          },
      ],
    },
    saveArgumentOverride: save ?? (_, _, _, _, _, _) async => true,
  );
  await tester.pumpWidget(
    GetMaterialApp(
      theme: solanaTheme(Brightness.light),
      home: const Scaffold(
        body: Args(
          scriptName: 'fixture',
          taskName: 'Activity',
          stagingMode: true,
          groupDraggable: false,
          readableLayout: true,
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
  return controller;
}

void main() {
  testWidgets('a 200-field task only builds the visible form window', (
    tester,
  ) async {
    await mountLongForm(tester);
    final fields = find.byType(TextFormField).evaluate().length;
    debugPrint(
      'FORM_PERFORMANCE initial_fields=$fields total_fields=200 '
      'editable_texts=${find.byType(EditableText).evaluate().length}',
    );
    expect(fields, greaterThan(0));
    expect(fields, lessThan(15));
    expect(
      find.byKey(const ValueKey('argument-input-活动设置-选项199')),
      findsNothing,
    );
    // Labels/descriptions share a selection area instead of allocating two
    // more EditableText widgets for every parameter.
    expect(find.byType(EditableText).evaluate().length, fields);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('an edit survives a far scroll and can be saved immediately', (
    tester,
  ) async {
    final saved = <Object?>[];
    final controller = await mountLongForm(
      tester,
      save: (_, _, _, _, _, value) async {
        saved.add(value);
        return true;
      },
    );
    final first = find.byKey(const ValueKey('argument-input-活动设置-选项0'));
    await tester.enterText(first, '尚未提交的中文输入');
    // No debounce delay is required for draft state or clicking Save.
    expect(controller.findArgument('活动设置', '选项0')?.value, '尚未提交的中文输入');
    // Unfocused fields may leave the tree and are restored from the draft.
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pump();
    final scrollable = find.byWidgetPredicate(
      (widget) =>
          widget is Scrollable && widget.axisDirection == AxisDirection.down,
    );
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('argument-input-活动设置-选项70')),
      500,
      scrollable: scrollable,
      maxScrolls: 40,
    );
    await tester.pumpAndSettle();
    expect(find.byType(TextFormField).evaluate().length, lessThan(15));
    final position = tester.state<ScrollableState>(scrollable).position;
    position.jumpTo(0);
    await tester.pumpAndSettle();
    expect(tester.widget<TextFormField>(first).controller!.text, '尚未提交的中文输入');
    expect(await controller.saveDraftChanges(), isTrue);
    expect(saved, ['尚未提交的中文输入']);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('collapsing and reopening a group preserves unsaved edits', (
    tester,
  ) async {
    final controller = await mountLongForm(tester);
    final first = find.byKey(const ValueKey('argument-input-活动设置-选项0'));
    await tester.enterText(first, '折叠前的草稿');
    final toggle = find.byKey(const ValueKey('args-group-toggle-活动设置'));
    await tester.tap(toggle);
    await tester.pumpAndSettle();
    expect(first, findsNothing);
    expect(controller.hasDraftChanges, isTrue);
    await tester.tap(toggle);
    await tester.pumpAndSettle();
    expect(tester.widget<TextFormField>(first).controller!.text, '折叠前的草稿');
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox.shrink());
  });
}
