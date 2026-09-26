import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:oasx/solana/solana_terminal.dart';
import 'package:oasx/modules/log/script_log_browser_controller.dart';
import 'package:oasx/modules/log/log_browser_models.dart';
import 'package:oasx/translation/i18n.dart';

import 'solana_console_test.dart' show fixtureController;

/// Opt-in visual artifacts use test fixtures, never production sample records.
/// Run: flutter test --no-pub --dart-define=SOLANA_CAPTURE=true test/solana_visual_test.dart
void main() {
  const capture = bool.fromEnvironment('SOLANA_CAPTURE');
  testWidgets('reference overview renders at 1124 by 751', (tester) async {
    Get.testMode = true;
    await tester.binding.setSurfaceSize(const Size(1124, 751));
    final font = File(r'C:\Windows\Fonts\msyh.ttc');
    if (font.existsSync()) {
      final loader = FontLoader('Microsoft YaHei')
        ..addFont(Future.value(ByteData.sublistView(font.readAsBytesSync())));
      await loader.load();
    }
    final icons = FontLoader('MaterialIcons')
      ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'));
    await icons.load();
    final emoji = File(r'C:\Windows\Fonts\seguiemj.ttf');
    if (emoji.existsSync()) {
      await (FontLoader('Segoe UI Emoji')..addFont(
            Future.value(ByteData.sublistView(emoji.readAsBytesSync())),
          ))
          .load();
    }
    final monospace = File(r'C:\Windows\Fonts\consola.ttf');
    if (monospace.existsSync()) {
      await (FontLoader('Consolas')..addFont(
            Future.value(ByteData.sublistView(monospace.readAsBytesSync())),
          ))
          .load();
    }
    final c = fixtureController();
    c.overview.data = {
      ...c.overview.data!,
      'profiles': [
        {
          'id': 'profile-1',
          'name': 'OAS1',
          'state': 'running',
          'state_version': 7,
          'device': {'name': 'MuMu 模拟器 12', 'serial': '127.0.0.1:16384'},
        },
      ],
    };
    c.scheduler.data = {
      ...c.scheduler.data!,
      'ready': [
        {
          'task_id': 'RichMan',
          'task': 'RichMan',
          'next_run': '2026-09-24T04:00:00+08:00',
        },
        {
          'task_id': 'FloatParade',
          'task': 'FloatParade',
          'next_run': '2026-09-24T04:30:00+08:00',
        },
      ],
      'waiting': [
        {
          'task_id': 'BuyCards',
          'task_name': '结界赠卡',
          'next_run': '2026-09-24T15:05:09+08:00',
        },
        {
          'task_id': 'Flower',
          'task_name': '花合战',
          'next_run': '2026-09-24T15:19:20+08:00',
        },
        {
          'task_id': 'Meet',
          'task_name': '逢魔之时',
          'next_run': '2026-09-24T17:30:00+08:00',
        },
        {
          'task_id': 'WantedQuests',
          'task_name': '悬赏封印',
          'next_run': '2026-09-24T19:00:00+08:00',
        },
      ],
    };
    final logs = ScriptLogBrowserController(scriptName: 'OAS1');
    logs.lines.addAll(
      List.generate(
        18,
        (index) => ScriptLogLine.fromJson({
          'file_name': 'fixture.log',
          'line_no': index + 1,
          'offset': index * 80,
          'byte_length': 80,
          'text':
              'INFO   | 21:20:${(index + 35).toString()}.421 | ${['[PackageName] com.netease.onmyoji', '<<< HANDLE >>>', 'Screenshot interval set to 0.3s', 'Scheduler: Start task Orochi', 'OAS Solana visual test fixture', 'Switch to the battle theme', '<<< START THOUSAND THINGS >>>'][index % 7]}',
          'line_truncated': false,
        }),
      ),
    );
    c.audit.data = {
      'items': [
        {
          'type': 'control.completed',
          'occurred_at': '2026-09-24T03:20:00Z',
          'profile_id': 'profile-1',
          'payload': {'message': '运行计划已启动', 'executed': true},
        },
        {
          'type': 'scheduler.selected',
          'occurred_at': '2026-09-24T03:20:01Z',
          'profile_id': 'profile-1',
          'task_id': 'Orochi',
          'payload': {'reason': '已满足运行条件，按公平份额安排执行'},
        },
        {
          'type': 'config.changed',
          'occurred_at': '2026-09-24T03:10:00Z',
          'profile_id': 'profile-1',
          'payload': {'message': '目标场次已更新，下次运行生效'},
        },
      ],
    };
    final key = GlobalKey();
    await tester.pumpWidget(
      GetMaterialApp(
        translations: Messages(),
        locale: const Locale('zh', 'CN'),
        home: RepaintBoundary(
          key: key,
          child: Stack(
            children: [
              SolanaShell(
                controller: c,
                autoStart: false,
                showCaption: false,
                terminalBuilder: (name) => SolanaTerminal(
                  scriptName: name,
                  autoConnect: false,
                  controller: logs,
                ),
              ),
              const Positioned(
                bottom: 1,
                right: 12,
                child: Text(
                  'TEST DATA - NOT ACTUAL RUN HISTORY',
                  style: TextStyle(
                    fontFamily: 'Microsoft YaHei',
                    fontSize: 8,
                    color: Colors.grey,
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.runAsync(() async {
      for (final path in [
        'assets/images/Icon-app.png',
        for (final name in [
          'stamina',
          'jade',
          'coins',
          'summon_tickets',
          'gold_scales',
          'spirit_tickets',
          'raid_tickets',
          'purple_scales',
        ])
          'assets/solana/$name.png',
      ]) {
        await precacheImage(
          AssetImage(path),
          tester.element(find.byType(SolanaShell)),
        );
      }
    });
    await tester.pump();
    final output = Directory('build/solana-visual-fixtures');
    if (capture) output.createSync(recursive: true);
    for (final entry in [
      ('reference-overview', '总览'),
      ('reference-tasks', '任务中心'),
      ('reference-scheduler', '调度中心'),
      ('reference-statistics', '运行统计'),
      ('reference-audit', '操作审计'),
      ('reference-settings', '应用设置'),
    ]) {
      if (entry.$1 != 'reference-overview') {
        await tester.tap(find.byTooltip(entry.$2));
        await tester.pumpAndSettle();
      }
      if (entry.$1 == 'reference-tasks') {
        await tester.tap(find.text('资源收集').first);
        await tester.pumpAndSettle();
        await tester.tap(find.text('Orochi'.tr).first);
        await tester.pumpAndSettle();
      }
      expect(tester.takeException(), isNull, reason: entry.$1);
      if (capture) {
        await tester.runAsync(() async {
          final boundary =
              key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
          final image = await boundary.toImage(pixelRatio: 1);
          final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
          await File(
            '${output.path}/${entry.$1}.png',
          ).writeAsBytes(bytes!.buffer.asUint8List());
          image.dispose();
        });
      }
    }
    await tester.pumpWidget(const SizedBox());
    c.dispose();
    Get.reset();
    await tester.binding.setSurfaceSize(null);
  });
}
