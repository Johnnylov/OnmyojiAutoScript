import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/args/index.dart';
import 'package:oasx/solana/solana_widgets.dart';
import 'package:oasx/translation/i18n.dart';

void main() {
  testWidgets(
    'empty device inputs are visible and all twelve controls remain editable',
    (tester) async {
      Get.testMode = true;
      await tester.binding.setSurfaceSize(const Size(760, 800));
      addTearDown(() async {
        await tester.binding.setSurfaceSize(null);
        Get.reset();
      });
      const capture = bool.fromEnvironment('SOLANA_CAPTURE');
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
      final args = Get.put(ArgsController());
      const serialHelp =
          '常见的模拟器 Serial 可以查询下方列表。填 auto 自动检测模拟器；多个模拟器正在运行时需要手动填写。\n'
          'MuMu 模拟器 12：127.0.0.1:16384\nMuMu 模拟器：127.0.0.1:7555\n'
          '雷电模拟器：emulator-5554 或 127.0.0.1:5555。\n'
          '如果使用模拟器多开功能，请通过 adb devices 查询各自连接地址。';
      final fields = <Map<String, dynamic>>[
        {
          'name': '稳定设备标识',
          'type': 'string',
          'value': '',
          'description': '同一模拟器的不同配置填写相同值；留空按连接地址识别。',
        },
        {
          'name': '模拟器 Serial',
          'type': 'string',
          'value': 'auto',
          'description': serialHelp,
        },
        {
          'name': '句柄 Handle',
          'type': 'string',
          'value': 'auto',
          'description': '填 auto 自动检测；有多个模拟器时手动填写。',
        },
        for (final name in ['游戏包名', '截图方式', '控制方式'])
          {
            'name': name,
            'type': 'enum',
            'value': 'auto',
            'enumEnum': ['auto', 'manual'],
          },
        {'name': '重启 ADB', 'type': 'boolean', 'value': false},
        {
          'name': '模拟器类型',
          'type': 'enum',
          'value': 'auto',
          'enumEnum': ['auto', 'MuMu'],
        },
        {'name': '模拟器名称', 'type': 'string', 'value': ''},
        {'name': '模拟器路径', 'type': 'string', 'value': ''},
        {'name': '最小化窗口', 'type': 'boolean', 'value': false},
        {'name': '仅后台运行', 'type': 'boolean', 'value': false},
      ];
      await args.loadGroups(
        config: 'fixture',
        task: 'Script',
        stagingMode: true,
        preloadedGroups: {'模拟器设置': fields},
        saveArgumentOverride: (_, _, _, _, _, _) async => true,
      );
      final key = GlobalKey();
      await tester.pumpWidget(
        GetMaterialApp(
          translations: Messages(),
          locale: const Locale('zh', 'CN'),
          theme: solanaTheme(Brightness.light),
          home: MediaQuery(
            data: const MediaQueryData(
              size: Size(760, 800),
              textScaler: TextScaler.linear(1.25),
            ),
            child: RepaintBoundary(
              key: key,
              child: const Scaffold(
                backgroundColor: Color(0xFFFAF8FC),
                body: Column(
                  children: [
                    Padding(
                      padding: EdgeInsets.all(12),
                      child: Text('参数表单 · 测试数据 · 125% 字体缩放'),
                    ),
                    Expanded(
                      child: Args(
                        scriptName: 'fixture',
                        taskName: 'Script',
                        stagingMode: true,
                        groupDraggable: false,
                        readableLayout: true,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      // The long editor builds fields as they enter the viewport.
      expect(find.byType(TextFormField), findsWidgets);
      final empty = find.byKey(const ValueKey('argument-input-模拟器设置-稳定设备标识'));
      final field = tester.widget<TextField>(
        find.descendant(of: empty, matching: find.byType(TextField)),
      );
      expect(
        field.decoration?.enabledBorder?.borderSide.style,
        BorderStyle.solid,
      );
      expect(find.text('输入稳定设备标识'), findsOneWidget);
      await tester.enterText(empty, 'same-emulator');
      await tester.pump(const Duration(milliseconds: 180));
      expect(args.findArgument('模拟器设置', '稳定设备标识')?.value, 'same-emulator');
      expect(args.hasDraftChanges, isTrue);
      expect(tester.takeException(), isNull);
      if (capture) {
        await tester.tap(find.text('参数表单 · 测试数据 · 125% 字体缩放'));
        await tester.pumpAndSettle();
        await tester.runAsync(() async {
          final boundary =
              key.currentContext!.findRenderObject() as RenderRepaintBoundary;
          final frame = await boundary.toImage();
          final png = await frame.toByteData(format: ui.ImageByteFormat.png);
          final output = File('build/solana-forms/device-fields-125.png');
          await output.parent.create(recursive: true);
          await output.writeAsBytes(png!.buffer.asUint8List());
          frame.dispose();
        });
      }
      await tester.tap(find.text('展开完整说明'));
      await tester.pumpAndSettle();
      expect(find.text('收起说明'), findsOneWidget);
      expect(tester.takeException(), isNull);
      final scrollable = find.byWidgetPredicate(
        (widget) =>
            widget is Scrollable && widget.axisDirection == AxisDirection.down,
      );
      for (final entry in fields) {
        final argument = find.byKey(
          ValueKey('args-fixture-Script-模拟器设置-${entry['name']}'),
        );
        await tester.scrollUntilVisible(argument, 250, scrollable: scrollable);
        await tester.pumpAndSettle();
        final control = switch (entry['type']) {
          'boolean' => find.byType(Checkbox),
          'enum' => find.byType(DropdownButtonFormField<String>),
          _ => find.byType(TextFormField),
        };
        expect(
          find.descendant(of: argument, matching: control),
          findsOneWidget,
        );
      }
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );
}
