import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_profile_status.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:oasx/solana/solana_widgets.dart';

import 'solana_console_test.dart' show fixtureController;

void main() {
  test(
    'waiting dates sort chronologically with stable ties and missing dates last',
    () {
      final tasks = <JsonObject>[
        {'task': 'tomorrow', 'next_run': '2026-09-27 06:15:00'},
        {'task': 'later', 'next_run': '2026-09-26T18:00:00+08:00'},
        {'task': 'early', 'next_run': '2026-09-26T08:00:00+08:00'},
        {'task': 'same', 'next_run': '2026-09-26T00:00:00Z'},
        {'task': 'invalid', 'next_run': 'unknown'},
        {'task': 'missing'},
      ];
      final result = waitingByNextRun(tasks);
      expect(result.map((t) => t['task']), [
        'early',
        'same',
        'later',
        'tomorrow',
        'invalid',
        'missing',
      ]);
      expect(tasks.first['task'], 'tomorrow');
    },
  );

  testWidgets(
    'scheduler and every sidebar profile share running, paused and error states',
    (tester) async {
      final c = fixtureController();
      final capture = GlobalKey();
      await tester.binding.setSurfaceSize(const Size(1124, 751));
      if (const bool.fromEnvironment('SOLANA_CAPTURE')) {
        final font = File(r'C:\Windows\Fonts\msyh.ttc');
        if (font.existsSync()) {
          await (FontLoader('Microsoft YaHei')..addFont(
                Future.value(ByteData.sublistView(font.readAsBytesSync())),
              ))
              .load();
        }
        await (FontLoader(
          'MaterialIcons',
        )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
      }
      void profile(String state, bool alive) {
        c.overview.data = {
          'profiles': [
            {
              'id': 'profile-1',
              'name': 'oas2',
              'state': state,
              'executor_alive': alive,
            },
            {
              'id': 'profile-2',
              'name': 'oas1',
              'state': 'waiting',
              'executor_alive': true,
            },
          ],
          'current_runs': <JsonObject>[],
        };
        c.dismissMessage();
      }

      profile('running', true);
      c.scheduler.data = {
        ...c.scheduler.data!,
        'ready': <JsonObject>[],
        'waiting': [
          {'task': '重启', 'next_run': '2026-09-27 13:57:39'},
          {'task': '个人突破', 'next_run': '2026-09-26 20:17:17'},
          {'task': '结界蹭卡', 'next_run': '2026-09-26 17:00:47'},
          {'task': '逢魔之时', 'next_run': '2026-09-26 18:00:00'},
        ],
      };
      await tester.pumpWidget(
        MaterialApp(
          home: RepaintBoundary(
            key: capture,
            child: SolanaShell(
              controller: c,
              autoStart: false,
              showCaption: false,
              terminalBuilder: (_) => const SizedBox(),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      for (final state in [
        'running',
        'waiting',
        'paused',
        'pausing',
        'warning',
        'inactive',
      ]) {
        profile(state, !{'warning', 'inactive'}.contains(state));
        await tester.pump();
        final expected = state == 'warning'
            ? solanaRed
            : {'paused', 'pausing'}.contains(state)
            ? solanaAmber
            : state == 'inactive'
            ? solanaBlue
            : solanaGreen;
        final selected = tester.widget<Icon>(
          find.byKey(const ValueKey('profile-status-profile-1')),
        );
        final other = tester.widget<Icon>(
          find.byKey(const ValueKey('profile-status-profile-2')),
        );
        final dot = tester.widget<Container>(
          find.byKey(const ValueKey('scheduler-status-dot')),
        );
        expect(selected.color, expected, reason: state);
        expect(
          (dot.decoration as BoxDecoration).color,
          expected,
          reason: state,
        );
        expect(other.color, solanaGreen);
        expect(other.icon, Icons.pause_circle_filled);
        if (state == 'running' || state == 'waiting') {
          expect(selected.icon, Icons.pause_circle_filled);
        }
        if (state == 'warning') expect(selected.icon, Icons.error);
      }
      // The user-facing waiting list uses dates, not configuration-file order.
      final names = ['结界蹭卡', '逢魔之时', '个人突破', '重启'];
      final queue = find.ancestor(
        of: find.text('等待中'),
        matching: find.byType(ListView),
      );
      final positions = names
          .map(
            (name) => tester
                .getTopLeft(
                  find.descendant(of: queue, matching: find.text(name)),
                )
                .dy,
          )
          .toList();
      expect(positions, orderedEquals([...positions]..sort()));
      profile('warning', false);
      await tester.pumpAndSettle();
      if (const bool.fromEnvironment('SOLANA_CAPTURE')) {
        await tester.runAsync(() async {
          final boundary =
              capture.currentContext!.findRenderObject()!
                  as RenderRepaintBoundary;
          final output = await boundary.toImage(pixelRatio: 1);
          final bytes = await output.toByteData(format: ui.ImageByteFormat.png);
          final path =
              '${Directory.current.parent.path}/validation/v047-status-preview.png';
          await File(path).writeAsBytes(bytes!.buffer.asUint8List());
          output.dispose();
        });
      }
      c.connected = false;
      c.dismissMessage();
      await tester.pump();
      expect(
        tester
            .widget<Icon>(
              find.byKey(const ValueKey('profile-status-profile-1')),
            )
            .color,
        Colors.grey,
      );
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      c.dispose();
      await tester.binding.setSurfaceSize(null);
    },
  );
}
