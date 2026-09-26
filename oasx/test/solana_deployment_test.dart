import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/modules/log/log_widget.dart';
import 'package:oasx/modules/server/controllers/server_controller.dart';
import 'package:oasx/modules/server/widgets/deploy_section_panel.dart';
import 'package:oasx/modules/server/widgets/deploy_yaml_editor.dart';
import 'package:oasx/solana/solana_deployment.dart';
import 'package:oasx/solana/solana_widgets.dart';
import 'package:oasx/translation/i18n.dart';
import 'package:oasx/translation/i18n_content.dart';

class DeploymentFixture extends ServerController {
  int initCalls = 0;
  int runCalls = 0;
  String? saved;
  Completer<void>? pendingRun;

  DeploymentFixture({bool valid = true}) {
    rootPathServer.value = r'C:\test-only\synthetic-oas';
    rootPathAuthenticated.value = valid;
    deployContent.value =
        'Deploy:\n  Git:\n    Branch: fixture\n    AutoUpdate: false\n';
    logs.add('INFO: synthetic deployment log; no command was executed');
  }

  @override
  // The fixture deliberately bypasses host storage and shell initialization.
  // ignore: must_call_super
  void onInit() {
    // No storage, process or filesystem dependency in this widget fixture.
    initCalls++;
  }

  @override
  Future<void> run() async {
    runCalls++;
    isDeployLoading.value = true;
    await pendingRun?.future;
    isDeployLoading.value = false;
  }

  @override
  void writeDeploy(String value) {
    saved = value;
    deployContent.value = value;
  }
}

Widget host(Widget child) => GetMaterialApp(
  translations: Messages(),
  locale: const Locale('zh', 'CN'),
  theme: solanaTheme(Brightness.light),
  builder: (context, child) => MediaQuery(
    data: MediaQuery.of(
      context,
    ).copyWith(textScaler: const TextScaler.linear(1.25)),
    child: child!,
  ),
  home: Scaffold(
    body: Padding(padding: const EdgeInsets.all(16), child: child),
  ),
);

void main() {
  setUp(() => Get.testMode = true);
  tearDown(() => Get.reset());

  testWidgets(
    'embedding registers once and returning never deploys or creates a nested page',
    (tester) async {
      final controller = DeploymentFixture();
      var backCalls = 0;
      await tester.pumpWidget(
        host(
          SolanaDeployment(controller: controller, onBack: () => backCalls++),
        ),
      );
      await tester.pumpAndSettle();
      expect(Get.find<ServerController>(), same(controller));
      expect(controller.initCalls, 1);
      expect(controller.runCalls, 0);
      expect(find.byType(Scaffold), findsOneWidget);
      expect(find.byType(DeploySectionPanel), findsOneWidget);
      expect(find.byType(LogWidget), findsOneWidget);
      expect(find.text('后端部署'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('deployment-back')));
      expect(backCalls, 1);
      expect(controller.runCalls, 0);
      await tester.pumpWidget(host(const SizedBox()));
      expect(Get.find<ServerController>(), same(controller));
      await tester.pumpWidget(host(SolanaDeployment(onBack: () {})));
      await tester.pumpAndSettle();
      expect(controller.initCalls, 1);
      expect(controller.logs.single, contains('no command was executed'));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    },
  );

  testWidgets(
    'only the explicit enabled action deploys and duplicate clicks are blocked',
    (tester) async {
      final controller = DeploymentFixture(valid: false);
      await tester.pumpWidget(
        host(SolanaDeployment(controller: controller, onBack: () {})),
      );
      await tester.pumpAndSettle();
      final run = find.byKey(const ValueKey('deployment-run'));
      expect(tester.widget<FilledButton>(run).onPressed, isNull);
      expect(controller.runCalls, 0);
      controller.rootPathAuthenticated.value = true;
      controller.pendingRun = Completer<void>();
      await tester.pump();
      await tester.tap(run);
      await tester.pump();
      expect(controller.runCalls, 1);
      expect(tester.widget<FilledButton>(run).onPressed, isNull);
      expect(
        tester
            .widget<OutlinedButton>(
              find.byKey(const ValueKey('deployment-select-directory')),
            )
            .onPressed,
        isNull,
      );
      controller.pendingRun!.complete();
      await tester.pumpAndSettle();
      expect(controller.runCalls, 1);
      expect(tester.widget<FilledButton>(run).onPressed, isNotNull);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    },
  );

  testWidgets(
    'the original structured editor saves through the shared controller without deployment',
    (tester) async {
      final controller = DeploymentFixture();
      await tester.pumpWidget(
        host(SolanaDeployment(controller: controller, onBack: () {})),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text(I18n.setupDeploy.tr));
      await tester.pumpAndSettle();
      expect(find.byType(DeployYamlEditor), findsOneWidget);
      final branch = find.byWidgetPredicate(
        (widget) => widget is TextField && widget.controller?.text == 'fixture',
      );
      await tester.enterText(branch, 'review-only');
      await tester.pump();
      await tester.tap(find.byIcon(Icons.save_rounded));
      await tester.pumpAndSettle();
      expect(controller.saved, contains('Branch: review-only'));
      expect(controller.runCalls, 0);
      expect(tester.takeException(), isNull);
      await tester.pump(const Duration(seconds: 4));
      await tester.pumpAndSettle();
      await tester.pumpWidget(const SizedBox());
    },
  );

  testWidgets(
    'narrow embedded content stays reachable at 125 percent text scale',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(440, 600));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final controller = DeploymentFixture(valid: false);
      await tester.pumpWidget(
        host(SolanaDeployment(controller: controller, onBack: () {})),
      );
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('deployment-select-directory')),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
      controller.rootPathAuthenticated.value = true;
      await tester.pump();
      await tester.tap(find.text(I18n.setupDeploy.tr));
      await tester.pumpAndSettle();
      expect(find.byType(DeployYamlEditor), findsOneWidget);
      expect(tester.takeException(), isNull);
      expect(controller.runCalls, 0);
      await tester.pumpWidget(const SizedBox());
    },
  );
}
