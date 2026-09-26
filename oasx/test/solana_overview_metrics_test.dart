import 'dart:io';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/solana_api.dart';
import 'package:oasx/solana/solana_controller.dart';
import 'package:oasx/solana/solana_overview_metrics.dart';
import 'package:oasx/solana/solana_shell.dart';
import 'package:oasx/solana/solana_widgets.dart';

import 'solana_console_test.dart' show FixtureApi, fixtureController;

const _capture = bool.fromEnvironment('SOLANA_CAPTURE');
final _now = DateTime.parse('2026-09-26T04:20:00Z');

JsonObject runFixture({String state = 'running'}) => {
  'profile_id': 'profile-1',
  'run_id': 'current-task-run',
  'task_id': 'AreaBoss',
  'task_name': '狭间暗域',
  'state': state,
  'current_count': 18,
  'target_count': 50,
  'count_supported': true,
  'count_unit': '次挑战',
  'progress_phase': state,
  'battle_supported': true,
  'battle_count': 18,
  'battle_scope': 'current_run',
  'observed_at': '2026-09-26T04:16:00Z',
  'execution_seconds': 755,
  'target_seconds': 2100,
};

JsonObject cycleFixture() => {
  'profile_id': 'profile-1',
  'cycle_id': 'current-executor-session',
  'started_at': '2026-09-26T04:00:00Z',
  'completed_tasks': 3,
  'total_tasks': 12,
  'failed_tasks': 1,
};

JsonObject overviewFixture({String state = 'running'}) => {
  'profiles': [
    {
      'id': 'profile-1',
      'name': '测试配置',
      'state': state,
      'state_version': 7,
      'executor_alive': true,
    },
    {'id': 'profile-2', 'name': '另一个配置', 'state': 'running'},
  ],
  // Other profiles deliberately come first to catch accidental cross-profile
  // selection and addition of unrelated battle/session totals.
  'current_runs': [
    {
      ...runFixture(),
      'profile_id': 'profile-2',
      'run_id': 'other-task-run',
      'current_count': 999,
      'battle_count': 999,
    },
    runFixture(state: state),
  ],
  'execution_cycles': [
    {
      ...cycleFixture(),
      'profile_id': 'profile-2',
      'completed_tasks': 9,
      'total_tasks': 99,
    },
    cycleFixture(),
  ],
};

class _MetricsApi extends FixtureApi {
  JsonObject overview = overviewFixture();
  @override
  Future<JsonObject> get(String path, {JsonObject? query}) async {
    if (path == '/api/v2/overview') return overview;
    return super.get(path, query: query);
  }
}

Future<void> mountMetrics(
  WidgetTester tester,
  SolanaController controller, {
  GlobalKey? screenshotKey,
  double factor = 1,
  Size size = const Size(1124, 751),
}) async {
  tester.view.physicalSize = size * factor;
  tester.view.devicePixelRatio = factor;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(() async {
    await tester.pumpWidget(const SizedBox());
    controller.dispose();
  });
  await tester.pumpWidget(
    MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: solanaTheme(Brightness.light),
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(
          context,
        ).copyWith(textScaler: TextScaler.linear(factor)),
        child: child!,
      ),
      home: RepaintBoundary(
        key: screenshotKey,
        child: Stack(
          children: [
            SolanaShell(
              controller: controller,
              autoStart: false,
              showCaption: false,
              terminalBuilder: (_) => const Center(
                child: Text('TEST DATA — OVERVIEW METRICS CONTRACT'),
              ),
            ),
          ],
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Finder metric(String label) => find.byKey(ValueKey('overview-metric-$label'));

void main() {
  setUpAll(() async {
    if (!_capture) return;
    final font = File(r'C:\Windows\Fonts\msyh.ttc');
    expect(font.existsSync(), isTrue);
    await (FontLoader('Microsoft YaHei')
          ..addFont(Future.value(ByteData.sublistView(font.readAsBytesSync()))))
        .load();
    await (FontLoader(
      'MaterialIcons',
    )..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'))).load();
  });

  test('reference values use distinct backend scopes and observation age', () {
    final metrics = SolanaOverviewMetrics(
      run: runFixture(),
      cycle: cycleFixture(),
      now: _now,
    );
    expect(metrics.current.value, '18 / 50');
    expect(metrics.current.subtitle, '当前值 · 4分钟前');
    expect(metrics.current.tooltip, contains('次挑战'));
    expect(metrics.battles.value, '18');
    expect(metrics.battles.tooltip, contains('已确认结算'));
    expect(metrics.tasks.value, '3 / 12');
    expect(metrics.tasks.tooltip, contains('失败任务：1 项'));
    expect(metrics.tasks.fraction, .25);
  });

  test('unknown target is distinct from explicit unlimited and zero', () {
    final run = runFixture()..['target_count'] = null;
    var metric = SolanaOverviewMetrics(run: run, cycle: {}).current;
    expect(metric.value, '18 / —');
    expect(metric.tooltip, contains('尚未上报数量目标'));
    run['target_unbounded'] = true;
    metric = SolanaOverviewMetrics(run: run, cycle: {}).current;
    expect(metric.value, '18 / 不限');
    run['current_count'] = 0;
    run['target_count'] = 0;
    metric = SolanaOverviewMetrics(run: run, cycle: {}).current;
    expect(metric.value, '0 / 0');
    expect(metric.fraction, isNull);
  });

  test('unsupported and not-yet-observed counts never masquerade as zero', () {
    final run = runFixture()
      ..['count_supported'] = false
      ..['battle_supported'] = false
      ..['count_unavailable_reason'] = '旧任务没有进度观测点'
      ..['battle_unavailable_reason'] = '尚未接入结算观测';
    var metrics = SolanaOverviewMetrics(run: run, cycle: {});
    expect(metrics.current.value, '暂不支持');
    expect(metrics.current.tooltip, contains('旧任务没有进度观测点'));
    expect(metrics.battles.value, '暂不支持');
    expect(metrics.battles.tooltip, contains('尚未接入结算观测'));
    run['count_supported'] = true;
    run['battle_supported'] = true;
    run['current_count'] = null;
    run['battle_count'] = null;
    metrics = SolanaOverviewMetrics(run: run, cycle: {});
    expect(metrics.current.value, '待上报');
    expect(metrics.battles.value, '待上报');
    run['current_count'] = 0;
    run['battle_count'] = 0;
    metrics = SolanaOverviewMetrics(run: run, cycle: {});
    expect(metrics.current.value, '0 / 50');
    expect(metrics.battles.value, '0');
    run['battle_supported'] = false;
    run['battle_unavailable_reason'] = 'not_observed_yet';
    metrics = SolanaOverviewMetrics(run: run, cycle: {});
    expect(metrics.battles.value, '待观测');
    expect(metrics.battles.tooltip, contains('尚未观测到'));
  });

  test('profile selection never borrows another run or execution cycle', () {
    final overview = overviewFixture();
    final metrics = SolanaOverviewMetrics(
      run: selectedOverviewRun(overview, 'profile-1'),
      cycle: selectedExecutionCycle(overview, 'profile-1'),
    );
    expect(metrics.current.value, '18 / 50');
    expect(metrics.battles.value, '18');
    expect(metrics.tasks.value, '3 / 12');
    expect(selectedOverviewRun(overview, 'missing'), isEmpty);
    expect(selectedOverviewRun(overview, null), isEmpty);
    expect(selectedExecutionCycle(overview, 'missing'), isEmpty);
    expect(selectedExecutionCycle(overview, null), isEmpty);
  });

  test(
    'session survives between tasks and latest restart resets the scope',
    () {
      final overview = overviewFixture()..['current_runs'] = <JsonObject>[];
      var metrics = SolanaOverviewMetrics(
        run: selectedOverviewRun(overview, 'profile-1'),
        cycle: selectedExecutionCycle(overview, 'profile-1'),
      );
      expect(metrics.current.value, '— / —');
      expect(metrics.battles.value, '—');
      expect(metrics.tasks.value, '3 / 12');
      (overview['execution_cycles'] as List).add({
        ...cycleFixture(),
        'cycle_id': 'new-session',
        'started_at': '2026-09-26T05:00:00Z',
        'completed_tasks': 0,
        'total_tasks': 4,
        'failed_tasks': 0,
      });
      metrics = SolanaOverviewMetrics(
        run: {},
        cycle: selectedExecutionCycle(overview, 'profile-1'),
      );
      expect(metrics.tasks.value, '0 / 4');
    },
  );

  testWidgets(
    'overview refresh and pause preserve true current task counters',
    (tester) async {
      final api = _MetricsApi();
      final c = fixtureController(api: api)..overview.data = api.overview;
      await mountMetrics(tester, c);
      expect(find.text('18 / 50'), findsOneWidget);
      expect(
        find.descendant(of: metric('战斗次数'), matching: find.text('18')),
        findsOneWidget,
      );
      expect(find.text('3 / 12'), findsOneWidget);
      expect(find.text('999 / 50'), findsNothing);

      api.overview = overviewFixture(state: 'paused');
      (api.overview['current_runs'] as List).last['progress_phase'] = '目标处理';
      await c.refreshAll();
      await tester.pumpAndSettle();
      expect(find.text('18 / 50'), findsOneWidget);
      expect(find.text('3 / 12'), findsOneWidget);
      expect(tester.widget<Tooltip>(metric('当前值')).message, contains('已暂停'));

      api.overview = overviewFixture();
      (api.overview['current_runs'] as List).last['current_count'] = 19;
      (api.overview['current_runs'] as List).last['battle_count'] = 19;
      await c.refreshAll();
      await tester.pumpAndSettle();
      expect(find.text('19 / 50'), findsOneWidget);
      expect(
        find.descendant(of: metric('战斗次数'), matching: find.text('19')),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'profile switch and task completion cannot leak previous values',
    (tester) async {
      final api = _MetricsApi();
      final c = fixtureController(api: api)..overview.data = api.overview;
      await mountMetrics(tester, c);
      await c.selectProfile('profile-2');
      await tester.pumpAndSettle();
      expect(find.text('999 / 50'), findsOneWidget);
      expect(find.text('9 / 99'), findsOneWidget);
      expect(find.text('18 / 50'), findsNothing);
      api.overview['current_runs'] = <JsonObject>[];
      await c.refreshAll();
      await tester.pumpAndSettle();
      expect(find.text('999 / 50'), findsNothing);
      expect(find.text('9 / 99'), findsOneWidget);
      expect(find.text('暂无运行目标'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  for (final scenario in [
    (1.0, const Size(1124, 751)),
    (1.25, const Size(1124, 751)),
    (1.5, const Size(960, 800)),
    (1.25, const Size(720, 820)),
  ]) {
    testWidgets(
      'three metrics readable at width ${scenario.$2.width} scale ${scenario.$1}',
      (tester) async {
        final c = fixtureController()..overview.data = overviewFixture();
        (c.overview.data!['current_runs'] as List).last['observed_at'] =
            DateTime.now()
                .subtract(const Duration(minutes: 4))
                .toUtc()
                .toIso8601String();
        final key = GlobalKey();
        await mountMetrics(
          tester,
          c,
          screenshotKey: key,
          factor: scenario.$1,
          size: scenario.$2,
        );
        for (final label in ['当前值', '战斗次数', '任务进度']) {
          expect(metric(label).hitTestable(), findsOneWidget);
        }
        expect(find.text('18 / 50'), findsOneWidget);
        expect(find.text('3 / 12'), findsOneWidget);
        expect(tester.takeException(), isNull);
        if (_capture) {
          await tester.runAsync(() async {
            final boundary =
                key.currentContext!.findRenderObject()!
                    as RenderRepaintBoundary;
            final image = await boundary.toImage(pixelRatio: scenario.$1);
            final bytes = await image.toByteData(
              format: ui.ImageByteFormat.png,
            );
            final directory = Directory('build/solana-metrics');
            directory.createSync(recursive: true);
            await File(
              '${directory.path}/metrics-${scenario.$2.width.toInt()}-${(scenario.$1 * 100).round()}.png',
            ).writeAsBytes(bytes!.buffer.asUint8List());
            image.dispose();
          });
        }
      },
    );
  }
}
