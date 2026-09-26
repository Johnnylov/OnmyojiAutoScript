import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:oasx/solana/solana_widgets.dart';
import 'package:oasx/translation/i18n.dart';

import 'solana_chinese_panels_test.dart'
    show chinesePanelFixtureController, chinesePanelTaskNames;

/// Images are opt-in and contain fixture data, never actual game history.
void main() {
  const capture = bool.fromEnvironment('SOLANA_CAPTURE');
  for (final scenario in [
    ('1124-100', const Size(1124, 751), 1.0),
    ('960-150', const Size(960, 900), 1.5),
  ]) {
    testWidgets(
      'Chinese scheduler renders without overflow at ${scenario.$1}',
      (tester) async {
        Get.testMode = true;
        await tester.binding.setSurfaceSize(scenario.$2);
        if (capture) {
          final font = File(r'C:\Windows\Fonts\msyh.ttc');
          if (font.existsSync()) {
            await (FontLoader('Microsoft YaHei')..addFont(
                  Future.value(ByteData.sublistView(font.readAsBytesSync())),
                ))
                .load();
          }
          await (FontLoader('MaterialIcons')
                ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf')))
              .load();
        }
        final c = chinesePanelFixtureController();
        final key = GlobalKey();
        await tester.pumpWidget(
          GetMaterialApp(
            translations: Messages(),
            locale: const Locale('zh', 'CN'),
            theme: solanaTheme(Brightness.light),
            builder: (context, child) => MediaQuery(
              data: MediaQuery.of(
                context,
              ).copyWith(textScaler: TextScaler.linear(scenario.$3)),
              child: child!,
            ),
            home: RepaintBoundary(
              key: key,
              child: Stack(
                children: [
                  SolanaShell(
                    controller: c,
                    autoStart: false,
                    showCaption: false,
                    terminalBuilder: (_) => const SizedBox(),
                  ),
                  const Positioned(
                    bottom: 0,
                    right: 8,
                    child: Text(
                      '中文界面验证 · 测试数据',
                      style: TextStyle(
                        fontFamily: 'Microsoft YaHei',
                        fontSize: 9,
                        color: Colors.grey,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        );
        addTearDown(() async {
          await tester.pumpWidget(const SizedBox());
          c.dispose();
          await tester.binding.setSurfaceSize(null);
          Get.reset();
        });
        await tester.pumpAndSettle();
        await tester.tap(find.byTooltip('调度中心'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        final output = Directory('build/solana-localization');
        if (capture) output.createSync(recursive: true);
        Future<void> snapshot(String section) async {
          if (!capture) return;
          await tester.runAsync(() async {
            final boundary =
                key.currentContext!.findRenderObject()!
                    as RenderRepaintBoundary;
            final image = await boundary.toImage(pixelRatio: 1);
            final bytes = await image.toByteData(
              format: ui.ImageByteFormat.png,
            );
            await File(
              '${output.path}/scheduler-$section-${scenario.$1}.png',
            ).writeAsBytes(bytes!.buffer.asUint8List());
            image.dispose();
          });
        }

        await snapshot('top');
        await Scrollable.ensureVisible(
          tester.element(find.text('等待条件')),
          alignment: 0,
        );
        await tester.pumpAndSettle();
        for (final entry in chinesePanelTaskNames.entries) {
          expect(find.text(entry.key), findsNothing);
          expect(find.text(entry.value), findsWidgets);
        }
        expect(find.text('排队中'), findsNothing);
        expect(find.text('等待中'), findsWidgets);
        expect(tester.takeException(), isNull);
        await snapshot('waiting');
      },
    );
  }
}
