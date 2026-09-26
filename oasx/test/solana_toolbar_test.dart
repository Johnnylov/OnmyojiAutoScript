import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_reference_icons.dart';
import 'package:oasx/solana/solana_shell.dart';

import 'solana_console_test.dart'
    show FixtureApi, fixtureController, fixtureOverview;

class _ToolbarApi extends FixtureApi {
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    if (path == '/config/task/copy-json') {
      calls.add({'path': path, 'query': query});
      return {
        'device': {'serial': 'fixture-unmasked-serial'},
        'account_id': 'fixture-unmasked-account',
      };
    }
    if (path == '/script_menu') {
      return {
        'Script': ['Script', 'Restart', 'GlobalGame', 'Orochi'],
      };
    }
    return super.get(path, query: query);
  }
}

void main() {
  testWidgets(
    'toolbar copies only the current task JSON without redaction or navigation',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(1124, 751));
      String? copied;
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(SystemChannels.platform, (call) async {
            if (call.method == 'Clipboard.setData') {
              copied = (call.arguments as Map)['text'] as String;
            }
            return null;
          });
      final api = _ToolbarApi();
      final c = fixtureController(api: api);
      await tester.pumpWidget(
        MaterialApp(
          home: SolanaShell(
            controller: c,
            autoStart: false,
            showCaption: false,
          ),
        ),
      );
      await tester.pumpAndSettle();
      for (final entry in const {
        'Script': 'tree-script',
        'Restart': 'tree-restart',
        'GlobalGame': 'tree-global',
        'Orochi': 'tree-plan-document',
      }.entries) {
        expect(
          tester
              .widget<SolanaReferenceIcon>(
                find.byKey(ValueKey('task-icon-${entry.key}')),
              )
              .name,
          entry.value,
        );
      }
      await tester.tap(find.byTooltip('复制非脱敏信息'));
      await tester.pumpAndSettle();
      final request = api.calls.singleWhere(
        (call) => call['path'] == '/config/task/copy-json',
      );
      expect(request['query'], {'config_name': '测试配置', 'task_name': 'Orochi'});
      expect(jsonDecode(copied!), {
        'device': {'serial': 'fixture-unmasked-serial'},
        'account_id': 'fixture-unmasked-account',
      });
      expect(find.byType(SolanaShell), findsOneWidget);
      c.overview.data = {...fixtureOverview, 'current_runs': <JsonObject>[]};
      c.dismissMessage();
      await tester.pump();
      expect(find.byTooltip('先选择任务以复制非脱敏信息'), findsOneWidget);
      final unavailable = tester.widget<IconButton>(
        find.widgetWithIcon(IconButton, Icons.copy_outlined),
      );
      expect(unavailable.onPressed, isNull);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(SystemChannels.platform, null);
      await tester.binding.setSurfaceSize(null);
    },
  );

  testWidgets(
    'connector applies only selected real profiles and can be disabled',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(1124, 751));
      final api = _ToolbarApi();
      final c = fixtureController(api: api);
      c.overview.data = {
        ...fixtureOverview,
        'profiles': [
          ...objects(fixtureOverview['profiles']),
          {
            'id': 'profile-2',
            'name': '第二配置',
            'state': 'stopped',
            'state_version': 0,
          },
        ],
      };
      await tester.pumpWidget(
        MaterialApp(
          home: SolanaShell(
            controller: c,
            autoStart: false,
            showCaption: false,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('开启连接器'));
      await tester.pumpAndSettle();
      expect(find.text('已选 0 个配置'), findsOneWidget);
      await tester.tap(find.widgetWithText(CheckboxListTile, '测试配置'));
      await tester.tap(find.widgetWithText(CheckboxListTile, '第二配置'));
      await tester.pump();
      expect(find.text('已选 2 个配置'), findsOneWidget);
      await tester.tap(find.text('应用联动范围'));
      await tester.pumpAndSettle();
      expect(c.linker.enabled, isTrue);
      expect(c.linker.scopeFor('测试配置'), containsAll(['测试配置', '第二配置']));
      expect(api.calls.where((call) => call['method'] != null), isEmpty);
      await tester.tap(find.byTooltip('关闭连接器'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(SwitchListTile));
      await tester.pump();
      await tester.tap(find.text('应用联动范围'));
      await tester.pumpAndSettle();
      expect(c.linker.enabled, isFalse);
      expect(c.linker.scopeFor('测试配置'), ['测试配置']);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
      await tester.binding.setSurfaceSize(null);
    },
  );
}
