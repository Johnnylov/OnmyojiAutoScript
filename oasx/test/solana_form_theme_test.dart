import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_settings.dart';
import 'package:oasx/solana/solana_widgets.dart';

const capture = bool.fromEnvironment('SOLANA_CAPTURE');

Future<void> loadReadableFonts() async {
  debugDisableShadows = false;
  final file = File(r'C:\Windows\Fonts\msyh.ttc');
  if (file.existsSync()) {
    await (FontLoader('Microsoft YaHei')
          ..addFont(Future.value(ByteData.sublistView(file.readAsBytesSync()))))
        .load();
  }
  await (FontLoader(
    'MaterialIcons',
  )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
}

Widget formHost(GlobalKey key, Widget child) => RepaintBoundary(
  key: key,
  child: MaterialApp(
    debugShowCheckedModeBanner: false,
    theme: solanaTheme(Brightness.light),
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(
        context,
      ).copyWith(textScaler: const TextScaler.linear(1.25)),
      child: child!,
    ),
    home: Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            colors: [Color(0xFFDAC3D2), Color(0xFFE5F4F5)],
          ),
        ),
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Align(alignment: Alignment.topCenter, child: child),
        ),
      ),
    ),
  ),
);

Future<ui.Image> frame(WidgetTester tester, GlobalKey key, String name) async {
  final boundary =
      key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
  final image = await tester.runAsync(() => boundary.toImage(pixelRatio: 1));
  if (capture) {
    await tester.runAsync(() async {
      final data = await image!.toByteData(format: ui.ImageByteFormat.png);
      final directory = Directory('build/solana-form-fixtures')
        ..createSync(recursive: true);
      await File(
        '${directory.path}/$name.png',
      ).writeAsBytes(data!.buffer.asUint8List());
    });
  }
  return image!;
}

void main() {
  test(
    'form and menu typography stays compact without changing glass cards',
    () {
      for (final brightness in Brightness.values) {
        final theme = solanaTheme(brightness);
        expect(theme.colorScheme.surface.a, lessThan(1));
        expect(theme.canvasColor.a, 1);
        expect(
          theme.dropdownMenuTheme.menuStyle!.backgroundColor!.resolve({})!.a,
          1,
        );
        expect(theme.popupMenuTheme.color!.a, 1);
        expect(theme.textTheme.bodyLarge!.fontSize, 13);
        expect(theme.textTheme.bodyLarge!.letterSpacing, 0);
        expect(
          theme.dropdownMenuTheme.textStyle!.fontFamily,
          'Microsoft YaHei',
        );
        expect(theme.textTheme.titleSmall!.letterSpacing, 0);
        expect(theme.textTheme.labelSmall!.letterSpacing, 0);
        expect(theme.hoverColor, isNot(theme.focusColor));
      }
    },
  );

  testWidgets('storage form closed and open is readable at 125 percent', (
    tester,
  ) async {
    await loadReadableFonts();
    await tester.binding.setSurfaceSize(const Size(720, 570));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final controller = SolanaController(
      api: SolanaApi(address: () => 'http://127.0.0.1:1'),
    )..connected = true;
    addTearDown(controller.dispose);
    final key = GlobalKey();
    await tester.pumpWidget(
      formHost(
        key,
        StoragePolicyEditor(
          controller: controller,
          policy: const {
            'mode': 'standard',
            'event_retention_days': 14,
            'summary_retention_days': 180,
            'normal_budget_bytes': 67108864,
            'temporary_reserve_bytes': 16777216,
            'timezone': 'Asia/Shanghai',
          },
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    for (final input in tester.widgetList<EditableText>(
      find.byType(EditableText),
    )) {
      expect(input.style.fontSize, 13);
      expect(input.style.letterSpacing, 0);
      expect(input.style.fontWeight, FontWeight.w400);
    }
    (await frame(tester, key, 'storage-closed-125')).dispose();
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    expect(find.text('仅汇总 · 不保留长期逐条审计'), findsOneWidget);
    expect(find.text('关闭普通历史 · 保留必要恢复状态'), findsOneWidget);
    expect(find.byIcon(Icons.check_rounded), findsOneWidget);
    expect(
      tester
          .widget<DropdownButton<String>>(find.byType(DropdownButton<String>))
          .style!
          .fontFamily,
      'Microsoft YaHei',
    );
    expect(tester.takeException(), isNull);
    (await frame(tester, key, 'storage-open-125')).dispose();

    final mouse = await tester.createGesture(kind: PointerDeviceKind.mouse);
    await mouse.addPointer(location: Offset.zero);
    await mouse.moveTo(tester.getCenter(find.text('仅汇总 · 不保留长期逐条审计')));
    await tester.pumpAndSettle();
    (await frame(tester, key, 'storage-open-hover-125')).dispose();
    await tester.tap(find.text('仅汇总 · 不保留长期逐条审计'));
    await tester.pumpAndSettle();
    expect(find.text('仅汇总 · 不保留长期逐条审计'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await mouse.removePointer();
    await tester.pumpWidget(const SizedBox());
    debugDisableShadows = true;
  });

  testWidgets(
    'Material DropdownMenu overlay has the same opaque surface at 125 percent',
    (tester) async {
      await loadReadableFonts();
      await tester.binding.setSurfaceSize(const Size(720, 390));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final key = GlobalKey();
      await tester.pumpWidget(
        formHost(
          key,
          const Surface(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                SectionHeading('下拉菜单主题'),
                SizedBox(height: 18),
                DropdownMenu<String>(
                  width: 600,
                  initialSelection: 'first',
                  label: Text('调度模式'),
                  dropdownMenuEntries: [
                    DropdownMenuEntry(value: 'first', label: '原有策略 · 当前已选'),
                    DropdownMenuEntry(value: 'second', label: '公平调度 · 试算'),
                    DropdownMenuEntry(value: 'third', label: '公平调度'),
                  ],
                ),
                SizedBox(height: 14),
                Text('下面的正文与参数不应透过菜单：Asia/Shanghai 14 180 64 16'),
              ],
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      (await frame(tester, key, 'material-dropdown-closed-125')).dispose();
      await tester.tap(find.byIcon(Icons.arrow_drop_down).first);
      await tester.pumpAndSettle();
      expect(find.text('公平调度 · 试算').hitTestable(), findsOneWidget);
      expect(tester.takeException(), isNull);
      (await frame(tester, key, 'material-dropdown-open-125')).dispose();
      await tester.pumpWidget(const SizedBox());
      debugDisableShadows = true;
    },
  );
}
