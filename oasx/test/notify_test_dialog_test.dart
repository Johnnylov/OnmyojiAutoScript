import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/home/tool_view.dart';
import 'package:oasx/modules/settings/oas_card.dart' show notifyTest;
import 'package:oasx/solana/solana_widgets.dart';

void main() {
  const capture = bool.fromEnvironment('SOLANA_CAPTURE');
  for (final scenario in [
    ('desktop', const Size(1124, 751), 1.0),
    ('scaled', const Size(800, 700), 1.5),
    ('narrow', const Size(390, 640), 1.5),
  ]) {
    testWidgets('notification labels do not overlap at ${scenario.$1}', (
      tester,
    ) async {
      Get.testMode = true;
      await tester.binding.setSurfaceSize(scenario.$2);
      addTearDown(() async {
        await tester.pumpWidget(const SizedBox());
        await tester.binding.setSurfaceSize(null);
        Get.reset();
      });
      if (capture) {
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
      final boundary = GlobalKey();
      await tester.pumpWidget(
        RepaintBoundary(
          key: boundary,
          child: GetMaterialApp(
            theme: solanaTheme(Brightness.light),
            builder: (context, child) => MediaQuery(
              data: MediaQuery.of(
                context,
              ).copyWith(textScaler: TextScaler.linear(scenario.$3)),
              child: child!,
            ),
            home: const Scaffold(
              body: Center(
                child: FilledButton(
                  onPressed: notifyTest,
                  child: Text('打开测试窗口'),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('打开测试窗口'));
      await tester.pumpAndSettle();
      expect(find.byType(AlertDialog), findsOneWidget);
      // The editable subject has the same text; check the actual title only.
      expect(
        find.descendant(
          of: find.byType(AlertDialog),
          matching: find.byType(Card),
        ),
        findsNothing,
      );
      for (final label in ['推送配置', '发送主题', '发送内容']) {
        final caption = tester.getRect(find.text(label));
        final input = tester.getRect(find.byKey(ValueKey('notify-$label')));
        expect(input.top, greaterThanOrEqualTo(caption.bottom + 7));
        expect(input.width, greaterThan(220));
      }
      expect(tester.takeException(), isNull);
      await tester.ensureVisible(find.byKey(const ValueKey('notify-发送内容')));
      await tester.pumpAndSettle();
      expect(find.text('发送测试').hitTestable(), findsOneWidget);
      if (capture && scenario.$1 == 'desktop') {
        await tester.runAsync(() async {
          final render =
              boundary.currentContext!.findRenderObject()!
                  as RenderRepaintBoundary;
          final shot = await render.toImage(pixelRatio: 1);
          final bytes = await shot.toByteData(format: ui.ImageByteFormat.png);
          final output = File('build/notification-dialog-desktop.png');
          output.parent.createSync(recursive: true);
          await output.writeAsBytes(bytes!.buffer.asUint8List());
          shot.dispose();
        });
      }
    });
  }

  testWidgets('validation, multiline payload and duplicate clicks', (
    tester,
  ) async {
    final pending = Completer<bool>();
    final calls = <List<String>>[];
    await tester.pumpWidget(
      MaterialApp(
        theme: solanaTheme(Brightness.light),
        home: Scaffold(
          body: NotifyTest(
            onSend: (setting, title, content) {
              calls.add([setting, title, content]);
              return pending.future;
            },
          ),
        ),
      ),
    );
    await tester.tap(find.text('发送测试'));
    await tester.pump();
    expect(find.text('请填写完整的推送配置'), findsOneWidget);
    expect(calls, isEmpty);
    const config = 'provider: test\nkey: fixture-only';
    await tester.enterText(find.byKey(const ValueKey('notify-推送配置')), config);
    await tester.enterText(find.byKey(const ValueKey('notify-发送主题')), '测试主题');
    await tester.enterText(
      find.byKey(const ValueKey('notify-发送内容')),
      '第一行\n第二行',
    );
    await tester.tap(find.text('发送测试'));
    await tester.pump();
    expect(find.text('发送中…'), findsOneWidget);
    await tester.tap(find.text('发送中…'));
    expect(calls, [
      [config, '测试主题', '第一行\n第二行'],
    ]);
    pending.complete(true);
    await tester.pumpAndSettle();
    expect(find.text('测试消息已发送'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('request errors are readable without exposing credentials', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: solanaTheme(Brightness.light),
        home: Scaffold(
          body: NotifyTest(
            onSend: (_, _, _) async {
              throw StateError('private-token');
            },
          ),
        ),
      ),
    );
    await tester.enterText(
      find.byKey(const ValueKey('notify-推送配置')),
      'provider: test',
    );
    await tester.tap(find.text('发送测试'));
    await tester.pumpAndSettle();
    expect(find.text('发送失败，请检查推送配置和后端连接。'), findsOneWidget);
    expect(find.textContaining('private-token'), findsNothing);
    expect(find.text('发送测试'), findsOneWidget);
  });
}
