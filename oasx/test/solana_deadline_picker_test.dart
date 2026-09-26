import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/args/index.dart';
import 'package:oasx/solana/solana_widgets.dart';
import 'package:oasx/translation/i18n.dart';

void main() {
  testWidgets('deadline toggles persist empty/unlimited and retain original instant', (
    tester,
  ) async {
    Get.testMode = true;
    addTearDown(Get.reset);
    var value = '2026-09-26T23:59:00+08:00';
    await tester.pumpWidget(
      GetMaterialApp(
        translations: Messages(),
        locale: const Locale('zh', 'CN'),
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) => DeadlinePicker(
              value: value,
              onChanged: (next) => setState(() => value = next),
            ),
          ),
        ),
      ),
    );
    expect(tester.widget<Checkbox>(find.byType(Checkbox)).value, isTrue);
    expect(find.byType(DateTimePicker), findsOneWidget);
    await tester.tap(find.byType(Checkbox));
    await tester.pumpAndSettle();
    expect(value, '');
    expect(find.byType(DateTimePicker), findsNothing);
    expect(find.text('未启用，不限制活动截止时间。'), findsOneWidget);
    await tester.tap(find.byType(Checkbox));
    await tester.pumpAndSettle();
    expect(value, '2026-09-26T23:59:00+08:00');
    expect(find.byType(DateTimePicker), findsOneWidget);
  });

  testWidgets('calendar edits are stored with explicit UTC offset, not a text placeholder', (
    tester,
  ) async {
    var value = '';
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) => DeadlinePicker(
              value: value,
              onChanged: (next) => setState(() => value = next),
            ),
          ),
        ),
      ),
    );
    expect(find.byType(TextFormField), findsNothing);
    await tester.tap(find.byType(Checkbox));
    await tester.pumpAndSettle();
    expect(DateTime.parse(value).isUtc, isTrue);
    final chosen = DateTime(2027, 3, 5, 13, 14, 15);
    tester.widget<DateTimePicker>(find.byType(DateTimePicker)).onChange('2027-03-05 13:14:15');
    await tester.pump();
    expect(DateTime.parse(value), chosen.toUtc());
    expect(find.text('2027-03-05 13:14:15'), findsOneWidget);
  });

  testWidgets('task form stages and saves deadline as its original string field', (
    tester,
  ) async {
    Get.testMode = true;
    addTearDown(Get.reset);
    final args = Get.put(ArgsController());
    final saved = <List<Object?>>[];
    await args.loadGroups(
      config: 'trial',
      task: 'ActivityShikigami',
      stagingMode: true,
      preloadedGroups: {
        'scheduler': [
          {'name': 'real_deadline', 'type': 'string', 'value': '', 'description': 'real_deadline_help'},
        ],
      },
      saveArgumentOverride: (config, task, group, field, type, value) async {
        saved.add([group, field, type, value]);
        return true;
      },
    );
    await tester.pumpWidget(
      GetMaterialApp(
        translations: Messages(),
        locale: const Locale('zh', 'CN'),
        theme: solanaTheme(Brightness.light),
        home: const Scaffold(
          body: Args(
            scriptName: 'trial',
            taskName: 'ActivityShikigami',
            readableLayout: true,
            stagingMode: true,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byType(Checkbox));
    await tester.pumpAndSettle();
    expect(args.hasDraftChanges, isTrue);
    expect(await args.saveDraftChanges(), isTrue);
    expect(saved.single.take(3), ['scheduler', 'real_deadline', 'string']);
    expect(DateTime.tryParse(saved.single.last as String), isNotNull);
    await tester.tap(find.byType(Checkbox));
    await tester.pumpAndSettle();
    expect(await args.saveDraftChanges(), isTrue);
    expect(saved.last.last, '');
    expect(tester.takeException(), isNull);
  });
}
