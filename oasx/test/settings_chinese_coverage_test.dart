import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/args/index.dart';
import 'package:oasx/translation/config_labels.dart';
import 'package:oasx/translation/i18n.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    Get.testMode = true;
    Get.locale = const Locale('zh', 'CN');
    Get.addTranslations(Messages().keys);
  });

  tearDown(() {
    Get.reset();
    Get.locale = null;
  });

  test(
    'all registered settings have bundled Chinese labels, options and help',
    () {
      final catalog =
          jsonDecode(
                File(
                  'test/fixtures/settings_translation_keys.json',
                ).readAsStringSync(),
              )
              as Map<String, dynamic>;
      expect((catalog['tasks'] as List).length, 57);
      final untranslated = <String>[];
      for (final entry in catalog['keys'] as List) {
        final key = entry['key'] as String;
        final text = entry['kind'] == 'group' ? configGroupLabel(key) : key.tr;
        // An intentionally empty help string has no untranslated UI text.
        if (entry['kind'] == 'help' && text.isEmpty) continue;
        if (!RegExp(r'[\u3400-\u9fff]').hasMatch(text)) {
          untranslated.add('${entry['kind']}: $key (${entry['contexts'][0]})');
        }
      }
      expect(untranslated, isEmpty, reason: untranslated.join('\n'));
    },
  );

  test(
    'numbered groups preserve their protocol name and display the index',
    () {
      expect(
        configGroupLabel('switch_soul_config_12'),
        '${'switch_soul_config'.tr} 12',
      );
      expect(configGroupLabel('invite_info_list_1'), '大号-1');
      expect(configGroupLabel('sup_account_list_2'), '小号-2');
      expect(configGroupLabel('invite_info_list_5'), '被邀请账号 5');
      expect(configGroupLabel('sup_account_list_51'), '小号账号 51');
      expect(configGroupLabel('自定义队伍_3'), '自定义队伍_3');
      expect(configGroupLabel('unknown_group_1'), 'unknown_group_1');
    },
  );

  testWidgets(
    'activity labels are Chinese while editable values and saved keys stay raw',
    (tester) async {
      final args = Get.put(ArgsController());
      final writes = <List<dynamic>>[];
      await args.loadGroups(
        config: 'my_profile',
        task: 'ActivityShikigami',
        stagingMode: true,
        preloadedGroups: {
          'general_climb': [
            {'name': 'pass_limit', 'value': 50, 'type': 'integer'},
            {'name': 'run_sequence', 'value': 'pass,ap', 'type': 'string'},
            {'name': 'auto_select_souls', 'value': false, 'type': 'boolean'},
          ],
        },
        saveArgumentOverride: (config, task, group, name, type, value) async {
          writes.add([config, task, group, name, type, value]);
          return true;
        },
      );
      await tester.pumpWidget(
        GetMaterialApp(
          translations: Messages(),
          locale: const Locale('zh', 'CN'),
          home: Scaffold(
            body: ListView(
              children: [
                for (var index = 0; index < 3; index++)
                  ArgumentView(
                    index: index,
                    getGroupName: () => 'general_climb',
                    setArgument: (_, _, _, _, _, _) {},
                    readableLayout: true,
                    scriptName: 'my_profile',
                    taskName: 'ActivityShikigami',
                  ),
              ],
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('门票爬塔次数限制'), findsOneWidget);
      expect(find.text('运行顺序'), findsOneWidget);
      expect(find.text('按日期自选御魂'), findsOneWidget);
      expect(find.text('pass_limit'), findsNothing);
      expect(find.text('pass,ap'), findsOneWidget);
      await tester.enterText(
        find.byKey(const ValueKey('argument-input-general_climb-run_sequence')),
        'ap100,boss',
      );
      await tester.pump(const Duration(milliseconds: 200));
      await tester.tap(find.byType(Checkbox));
      await tester.pump();
      expect(await args.saveDraftChanges(), isTrue);
      expect(
        writes,
        contains(
          equals([
            'my_profile',
            'ActivityShikigami',
            'general_climb',
            'run_sequence',
            'string',
            'ap100,boss',
          ]),
        ),
      );
      expect(
        writes,
        contains(
          equals([
            'my_profile',
            'ActivityShikigami',
            'general_climb',
            'auto_select_souls',
            'boolean',
            true,
          ]),
        ),
      );
    },
  );

  testWidgets('Chinese enum choices still save the original protocol value', (
    tester,
  ) async {
    final args = Get.put(ArgsController());
    await args.loadGroups(
      config: 'my_profile',
      task: 'BondlingFairyland',
      stagingMode: true,
      preloadedGroups: {
        'bondling_config': [
          {
            'name': 'user_status',
            'value': 'leader',
            'type': 'enum',
            'enumEnum': ['leader', 'handoff1'],
          },
        ],
      },
    );
    await tester.pumpWidget(
      GetMaterialApp(
        translations: Messages(),
        locale: const Locale('zh', 'CN'),
        home: Scaffold(
          body: ArgumentView(
            index: 0,
            getGroupName: () => 'bondling_config',
            setArgument: (_, _, _, _, _, _) {},
            readableLayout: true,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('轮换队长：先带队后跟队').last);
    await tester.pumpAndSettle();
    expect(
      args.findArgument('bondling_config', 'user_status')!.value,
      'handoff1',
    );
  });
}
