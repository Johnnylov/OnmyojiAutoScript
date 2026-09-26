import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_recovery.dart';
import 'package:oasx/solana/solana_settings.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:oasx/solana/solana_widgets.dart';
import 'package:oasx/translation/i18n.dart';

import 'solana_console_test.dart' show FixtureApi, fixtureController;

const chinesePanelTaskNames = {
  'restart': '重启',
  'area_boss': '地域鬼王',
  'realm_raid': '个人突破',
  'kekkai_utilize': '结界蹭卡',
  'kekkai_activation': '结界挂卡',
  'demon_encounter': '逢魔之时',
};

class _ChineseMenuApi extends FixtureApi {
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    if (path == '/script_menu') {
      return {
        'DailyTask': ['DailyTrifles', 'AreaBoss', 'DemonEncounter'],
        'SoulZones': ['Orochi', 'FallenSun'],
      };
    }
    return super.get(path, query: query);
  }
}

SolanaController chinesePanelFixtureController() {
  final c = fixtureController(api: _ChineseMenuApi());
  c.overview.data = {
    'profiles': [
      {
        'id': 'profile-1',
        'name': '自定义配置A',
        'state': 'running',
        'device': {'name': 'MuMu 模拟器 12'},
      },
    ],
    'current_runs': <JsonObject>[],
  };
  c.scheduler.data = {
    'policy': {'mode': 'legacy', 'batch_seconds': 120, 'weights': {}},
    'running': [
      {
        'profile_id': 'profile-1',
        'task_id': 'orochi',
        'state': 'running',
        'device_id': 'opaque-device-id',
        'execution_seconds': 60,
      },
    ],
    'ready': <JsonObject>[],
    'waiting': [
      for (final task in chinesePanelTaskNames.keys)
        {'task_id': task, 'reason': 'scheduled', 'cooperative': false},
    ],
    'decisions': <JsonObject>[],
  };
  return c;
}

Future<void> _mount(
  WidgetTester tester,
  SolanaController controller, {
  Widget? body,
}) async {
  Get.testMode = true;
  await tester.binding.setSurfaceSize(const Size(1400, 1000));
  await tester.pumpWidget(
    GetMaterialApp(
      translations: Messages(),
      locale: const Locale('zh', 'CN'),
      theme: solanaTheme(Brightness.light),
      home:
          body ??
          SolanaShell(
            controller: controller,
            autoStart: false,
            showCaption: false,
            terminalBuilder: (_) => const SizedBox(),
          ),
    ),
  );
  await tester.pumpAndSettle();
  addTearDown(() async {
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
    await tester.binding.setSurfaceSize(null);
    Get.reset();
  });
}

class _RecoveryApi extends FixtureApi {
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    if (path == '/api/v2/recovery/operations') {
      return {
        'items': [
          {
            'action': 'config.change',
            'path': 'realm_raid.scheduler.enable',
            'key': 'raw-operation-id',
            'name': '保留自定义配置名',
            'resolvable': false,
            'explanation':
                'Stop all executors before reviewing this control receipt.',
          },
        ],
      };
    }
    if (path == '/api/v2/recovery') {
      return {
        'items': [
          {
            'task_id': 'realm_raid',
            'run_id': 'raw-run-id',
            'state': 'needs_reconciliation',
          },
        ],
      };
    }
    return super.get(path, query: query);
  }
}

void main() {
  testWidgets('scheduler waiting rows use Chinese names and waiting badges', (
    tester,
  ) async {
    final c = chinesePanelFixtureController();
    await _mount(tester, c);
    await tester.tap(find.byTooltip('调度中心'));
    await tester.pumpAndSettle();
    for (final entry in chinesePanelTaskNames.entries) {
      expect(find.text(entry.key), findsNothing);
      expect(find.text(entry.value), findsWidgets);
    }
    expect(find.text('排队中'), findsNothing);
    expect(find.text('等待中'), findsWidgets);
    expect(find.text('此任务会完整执行后再切换'), findsNWidgets(6));
    expect(find.textContaining('opaque-device-id'), findsNothing);
    expect(find.textContaining('MuMu 模拟器 12 ·'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'scheduler shares display Chinese task names but retain API keys',
    (tester) async {
      final api = FixtureApi();
      final c = fixtureController(api: api);
      final policy = <String, dynamic>{
        'mode': 'eevdf',
        'batch_seconds': 120,
        'weights': {'area_boss': 2},
      };
      await _mount(
        tester,
        c,
        body: Scaffold(
          body: SchedulerPolicyEditor(
            controller: c,
            policy: policy,
            tasks: const [],
          ),
        ),
      );
      await tester.tap(find.text('高级设置'));
      await tester.pumpAndSettle();
      expect(find.text('地域鬼王 · 执行份额'), findsOneWidget);
      expect(find.text('area_boss · 执行份额'), findsNothing);
      await tester.tap(find.text('应用调度策略'));
      await tester.pumpAndSettle();
      final call = api.calls.singleWhere(
        (item) => item['path'] == '/api/v2/scheduler/policy',
      );
      expect(object(object(call['body'])['weights'])['area_boss'], 2);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'history and audit show Chinese summaries while copy preserves raw record',
    (tester) async {
      final c = fixtureController();
      final record = <String, dynamic>{
        'type': 'scheduler.selected',
        'task_id': 'realm_raid',
        'profile_id': 'profile-1',
        'request_id': 'diagnostic-request-id',
        'occurred_at': '2026-09-26T01:00:00Z',
        'payload': {'reason': 'fair_share', 'result': 'succeeded'},
      };
      c.audit.data = {
        'items': [record],
      };
      c.runs.data = {
        'items': [
          {
            'task_id': 'area_boss',
            'result': 'succeeded',
            'started_at': '2026-09-26T01:00:00Z',
          },
        ],
      };
      await _mount(tester, c);
      await tester.tap(find.byTooltip('运行统计'));
      await tester.pumpAndSettle();
      expect(find.text('地域鬼王'), findsWidgets);
      expect(find.text('area_boss'), findsNothing);
      await tester.tap(find.byTooltip('操作审计'));
      await tester.pumpAndSettle();
      expect(find.text('测试配置 · 个人突破 · 按执行份额安排'), findsOneWidget);
      await tester.tap(find.text('调度选中任务'));
      await tester.pumpAndSettle();
      expect(find.text('任务：个人突破'), findsOneWidget);
      expect(find.text('配置：测试配置'), findsOneWidget);
      expect(find.text('状态：成功'), findsOneWidget);
      expect(find.text('原因：按执行份额安排'), findsOneWidget);
      expect(find.byType(SelectableText), findsNothing);
      String? copied;
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        (call) async {
          if (call.method == 'Clipboard.setData') {
            copied = (call.arguments as Map)['text'] as String;
          }
          return null;
        },
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          SystemChannels.platform,
          null,
        ),
      );
      await tester.tap(find.text('复制记录'));
      await tester.pump();
      expect(copied, const JsonEncoder.withIndent('  ').convert(record));
      await tester.tap(find.text('原始记录'));
      await tester.pumpAndSettle();
      expect(find.byType(SelectableText), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'recovery task title uses friendly name rather than raw task or run ID',
    (tester) async {
      final c = fixtureController(api: _RecoveryApi());
      await _mount(
        tester,
        c,
        body: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => showSolanaRecovery(context, c),
              child: const Text('查看恢复'),
            ),
          ),
        ),
      );
      await tester.tap(find.text('查看恢复'));
      await tester.pumpAndSettle();
      expect(find.text('个人突破'), findsOneWidget);
      expect(find.text('realm_raid'), findsNothing);
      expect(find.text('raw-run-id'), findsNothing);
      expect(find.text('修改配置 · 保留自定义配置名'), findsOneWidget);
      expect(find.text('请先停止所有正在运行的任务，再核验这次操作。'), findsOneWidget);
      expect(find.textContaining('Stop all executors'), findsNothing);
      expect(find.textContaining('realm_raid.scheduler.enable'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('关闭'));
      await tester.pumpAndSettle();
    },
  );
}
