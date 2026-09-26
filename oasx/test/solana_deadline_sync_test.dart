import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/args/index.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_tasks.dart';
import 'package:oasx/solana/solana_widgets.dart';
import 'package:oasx/translation/i18n.dart';

import 'solana_connection_settings_test.dart' show ConnectionApi;

const _capture = bool.fromEnvironment('SOLANA_CAPTURE');
final _captureBoundary = GlobalKey();

class _DeadlineFormApi extends ConnectionApi {
  bool taskEnabled = true;
  int formReads = 0;
  final mutations = <JsonObject>[];
  Completer<JsonObject>? pendingSaveResponse;

  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    if (path.endsWith('/args')) {
      formReads++;
      return {
        'revision': taskEnabled ? 'before-expiry' : 'after-expiry',
        'args': {
          'scheduler': [
            {'name': 'enable', 'type': 'boolean', 'value': taskEnabled},
            {
              'name': 'real_deadline',
              'type': 'string',
              'value': '2020-01-01T06:30:00+08:00',
            },
          ],
          'settings': [
            {'name': 'count', 'type': 'integer', 'value': 10},
            {'name': 'ap_limit', 'type': 'integer', 'value': 999},
          ],
        },
      };
    }
    return super.get(path, query: query);
  }

  @override
  Future<JsonObject> request(
    String method,
    String path, {
    JsonObject? body,
    JsonObject? query,
  }) async {
    mutations.add({'method': method, 'path': path, 'body': body});
    if (pendingSaveResponse != null) return pendingSaveResponse!.future;
    return {'saved': true, 'persisted': true, 'revision': 'unexpected-write'};
  }

  void expire({
    String profile = 'same-id',
    String task = 'activity_shikigami',
    int sequence = 2,
  }) {
    sockets.single.stream.subscription!.queued(
      jsonEncode({
        'type': 'config.changed',
        'stream_id': 'old-stream',
        'stream_seq': sequence,
        'event_id': 'deadline-$sequence',
        // Preserve the actual stream's outer record and inner change payload.
        'payload': {
          'profile_id': profile,
          'payload': {'reason': 'deadline_expired', 'task_id': task},
        },
      }),
    );
  }
}

Future<SolanaController> _mountForm(
  WidgetTester tester,
  _DeadlineFormApi api,
) async {
  Get.testMode = true;
  await tester.binding.setSurfaceSize(const Size(1000, 850));
  if (_capture) {
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
  final controller = SolanaController(api: api);
  await controller.start();
  await tester.pumpWidget(
    GetMaterialApp(
      translations: Messages(),
      locale: const Locale('zh', 'CN'),
      theme: solanaTheme(Brightness.light),
      home: RepaintBoundary(
        key: _captureBoundary,
        child: Scaffold(
          body: SolanaTasks(
            controller: controller,
            initialTask: 'ActivityShikigami',
            showCatalog: false,
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
  expect(
    Get.find<ArgsController>().findArgument('scheduler', 'enable')?.value,
    isTrue,
  );
  return controller;
}

Future<void> _disposeForm(
  WidgetTester tester,
  SolanaController controller,
) async {
  // Widget-test timer invariants run before addTearDown, so cancel both the
  // form fallback timer and the controller stream/poll timers inside the test.
  await tester.pumpWidget(const SizedBox.shrink());
  controller.dispose();
  await tester.pump();
  await tester.binding.setSurfaceSize(null);
  Get.reset();
}

Future<void> _captureCleanDeadline(WidgetTester tester) async {
  if (!_capture) return;
  await tester.runAsync(() async {
    final boundary =
        _captureBoundary.currentContext!.findRenderObject()!
            as RenderRepaintBoundary;
    final image = await boundary.toImage(pixelRatio: 1);
    try {
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      final directory = Directory.fromUri(
        Directory.current.parent.uri.resolve('validation/v046-deadline/'),
      ).absolute;
      await directory.create(recursive: true);
      final output = File('${directory.path}/expired-clean-form.png');
      await output.writeAsBytes(bytes!.buffer.asUint8List());
      debugPrint('DEADLINE_SCREENSHOT ${output.absolute.path}');
    } finally {
      image.dispose();
    }
  });
}

Finder _enabledCheckbox() => find.descendant(
  of: find.byKey(const ValueKey('args-old-ActivityShikigami-scheduler-enable')),
  matching: find.byType(Checkbox),
);

void main() {
  testWidgets('nested expiry event reloads a clean task and unchecks enable', (
    tester,
  ) async {
    final api = _DeadlineFormApi();
    final controller = await _mountForm(tester, api);
    final reads = api.formReads;
    api.taskEnabled = false;
    api.expire();
    await tester.pumpAndSettle();

    expect(controller.deadlineChanges.value?['profile_id'], 'same-id');
    expect(controller.deadlineChanges.value?['task_id'], 'activity_shikigami');
    expect(api.formReads, reads + 1);
    expect(
      Get.find<ArgsController>().findArgument('scheduler', 'enable')?.value,
      isFalse,
    );
    expect(tester.widget<Checkbox>(_enabledCheckbox()).value, isFalse);
    expect(find.text('该任务已到截止时间，已自动取消启用。'), findsOneWidget);
    expect(api.mutations, isEmpty);
    expect(tester.takeException(), isNull);
    await _captureCleanDeadline(tester);
    await _disposeForm(tester, controller);
  });

  testWidgets(
    'expiry preserves a dirty form and blocks any stale draft write',
    (tester) async {
      final api = _DeadlineFormApi();
      final controller = await _mountForm(tester, api);
      final args = Get.find<ArgsController>();
      final count = find.byKey(const ValueKey('argument-input-settings-count'));
      await tester.enterText(count, '77');
      expect(args.hasDraftChanges, isTrue);
      final reads = api.formReads;

      api.taskEnabled = false;
      api.expire();
      await tester.pumpAndSettle();

      expect(api.formReads, reads);
      expect(args.findArgument('settings', 'count')?.value.toString(), '77');
      expect(tester.widget<TextFormField>(count).controller!.text, '77');
      expect(find.textContaining('当前未保存的修改已保留'), findsOneWidget);
      // Exercise the same callback as the Save button, including validation.
      expect(await args.saveDraftChanges(), isFalse);
      expect(api.mutations, isEmpty);
      expect(args.hasDraftChanges, isTrue);
      expect(args.findArgument('settings', 'count')?.value.toString(), '77');
      expect(tester.takeException(), isNull);
      await _disposeForm(tester, controller);
    },
  );

  testWidgets(
    'expiry during a pending save keeps the conflict and blocks remaining fields',
    (tester) async {
      final api = _DeadlineFormApi();
      final controller = await _mountForm(tester, api);
      final args = Get.find<ArgsController>();
      args.stageArgumentChange('settings', 'count', '77', 'integer');
      args.stageArgumentChange('settings', 'ap_limit', '88', 'integer');
      final gate = Completer<JsonObject>();
      api.pendingSaveResponse = gate;

      try {
        final saving = args.saveDraftChanges();
        await tester.pump();
        expect(args.isSavingDraft.value, isTrue);
        expect(api.mutations, hasLength(1));
        expect(object(api.mutations.single['body'])['argument'], 'count');

        // The backend has already committed the first field, but its response
        // arrives after the deadline's config.changed notification.
        api.taskEnabled = false;
        api.expire();
        await tester.pump();
        expect(find.textContaining('当前未保存的修改已保留'), findsOneWidget);
        gate.complete({
          'saved': true,
          'persisted': true,
          'revision': 'saved-before-expiry',
        });
        expect(await saving, isFalse);
        await tester.pumpAndSettle();

        expect(find.textContaining('当前未保存的修改已保留'), findsOneWidget);
        expect(api.mutations, hasLength(1));
        expect(args.isFieldDirty('settings', 'count'), isTrue);
        expect(args.isFieldDirty('settings', 'ap_limit'), isTrue);
        expect(args.findArgument('settings', 'ap_limit')?.value, '88');
        expect(args.isSavingDraft.value, isFalse);

        // A delayed success must not clear the block for a later Save click.
        expect(await args.saveDraftChanges(), isFalse);
        expect(api.mutations, hasLength(1));
        expect(tester.takeException(), isNull);
      } finally {
        if (!gate.isCompleted) {
          gate.complete({'saved': false, 'persisted': true});
          await tester.pump();
        }
        await _disposeForm(tester, controller);
      }
    },
  );

  testWidgets(
    'expiry for another profile or task cannot replace the current form',
    (tester) async {
      final api = _DeadlineFormApi();
      final controller = await _mountForm(tester, api);
      final reads = api.formReads;
      api.taskEnabled = false;

      api.expire(profile: 'another-profile');
      await tester.pumpAndSettle();
      api.expire(task: 'another_task', sequence: 3);
      await tester.pumpAndSettle();

      expect(api.formReads, reads);
      expect(
        Get.find<ArgsController>().findArgument('scheduler', 'enable')?.value,
        isTrue,
      );
      expect(tester.widget<Checkbox>(_enabledCheckbox()).value, isTrue);
      expect(find.textContaining('已到截止时间'), findsNothing);
      expect(api.mutations, isEmpty);
      expect(tester.takeException(), isNull);
      await _disposeForm(tester, controller);
    },
  );

  testWidgets('missed stream expiry is recovered by the saved-deadline poll', (
    tester,
  ) async {
    final api = _DeadlineFormApi();
    final controller = await _mountForm(tester, api);
    api.taskEnabled = false;
    controller.streamConnected = false;

    await tester.pump(const Duration(seconds: 15));
    await tester.pumpAndSettle();

    expect(
      Get.find<ArgsController>().findArgument('scheduler', 'enable')?.value,
      isFalse,
    );
    expect(tester.widget<Checkbox>(_enabledCheckbox()).value, isFalse);
    expect(api.mutations, isEmpty);
    expect(tester.takeException(), isNull);
    await _disposeForm(tester, controller);
  });
}
