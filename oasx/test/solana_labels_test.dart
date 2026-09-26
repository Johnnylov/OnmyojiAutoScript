import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/solana/solana_widgets.dart';
import 'package:oasx/translation/i18n.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    Get.testMode = true;
    Get.locale = const Locale('zh', 'CN');
    Get.addTranslations(Messages().keys);
  });

  tearDown(() {
    Get.reset();
    Get.locale = null;
  });

  test('queue task identifiers share the existing Chinese menu names', () {
    const labels = {
      'restart': '重启',
      'Restart': '重启',
      'area_boss': '地域鬼王',
      'AreaBoss': '地域鬼王',
      'realm_raid': '个人突破',
      'RealmRaid': '个人突破',
      'kekkai_utilize': '结界蹭卡',
      'KekkaiUtilize': '结界蹭卡',
      'kekkai_activation': '结界挂卡',
      'KekkaiActivation': '结界挂卡',
      'demon_encounter': '逢魔之时',
      'DemonEncounter': '逢魔之时',
    };
    for (final entry in labels.entries) {
      expect(taskLabel(entry.key), entry.value, reason: entry.key);
    }
  });

  test('newer backend task names use the confirmed Chinese vocabulary', () {
    const labels = {
      'auto_checkin_big_god': '大神签到',
      'AutoCheckinBigGod': '大神签到',
      'other_world_twilight': '彼世逢魔',
      'OtherWorldTwilight': '彼世逢魔',
      'moonlight': '月华流光',
      'Moonlight': '月华流光',
      'martial_tournament': '武道大会',
      'MartialTournament': '武道大会',
      'gugu_art_studio': '呱呱画室',
      'GuguArtStudio': '呱呱画室',
      'guild_activity_monitor': '寮活动监控',
      'GuildActivityMonitor': '寮活动监控',
    };
    for (final entry in labels.entries) {
      expect(taskLabel(entry.key), entry.value, reason: entry.key);
    }
  });

  test('task aliases normalize for display while custom names stay intact', () {
    for (final alias in [
      'HeroTest',
      'hero_test',
      'herotest',
      'HERO_TEST',
      'hero-test',
    ]) {
      expect(taskLabel(alias), '英杰试炼', reason: alias);
    }
    for (final name in ['我的御魂小分队@123', 'custom_unknown_task', 'OrochiMoans']) {
      expect(taskLabel(name), name, reason: name);
    }
  });

  test('runtime and operation states have Chinese display labels', () {
    const codes = [
      'running',
      'waiting_resource',
      'needs_reconciliation',
      'paused',
      'stopping',
      'crashed',
      'active',
      'requested',
      'acquired',
      'partially_executed',
      'executed_not_saved',
      'unknown_state_accepted',
      'completed',
      'rejected',
      'identity_reconciled',
      'not_applied',
      'accepted',
      'not_saved',
      'already_stopped',
      'observed',
      'stale',
      'saved',
      'not_recorded',
    ];
    for (final code in codes) {
      final label = stateLabel(code);
      expect(label, matches(RegExp(r'[\u4e00-\u9fff]')), reason: code);
      expect(label, isNot(code), reason: code);
      expect(label, isNot('状态未知'), reason: code);
    }
    expect(stateLabel('new_backend_state'), '状态未知');
    expect(stateLabel('等待设备连接'), '等待设备连接');
  });

  test(
    'scheduler and recovery reasons explain actual backend codes in Chinese',
    () {
      const codes = [
        'scheduled',
        'real_deadline',
        'waiting_limit',
        'recovery_blocked',
        'legacy_order',
        'device_owned',
        'another_profile_selected',
        'device_recovery_pending',
        'previous_process_still_owns_device',
        'safe_stop',
        'resume',
        'unverified_checkpoint',
        'activity_window_closed',
        'service_restarted',
        'executor_exit',
        'legacy_audit_persistence_failed',
        'legacy_mutation_unresolved',
        'user_closed_uncertain_run',
        'retry_scheduled',
        'server_update_delayed',
        'team_wait_failed',
        'team_preempted',
        'team_partner_finished',
        'skipped',
        'business_precondition:activity_window_closed',
        'recovered',
      ];
      for (final code in codes) {
        final label = reasonLabel(code);
        expect(label, matches(RegExp(r'[\u4e00-\u9fff]')), reason: code);
        expect(label, isNot(code), reason: code);
        expect(label, isNot('暂无原因说明'), reason: code);
      }
      expect(reasonLabel('new_backend_reason'), '暂无原因说明');
      expect(reasonLabel('等待模拟器重新连接'), '等待模拟器重新连接');
    },
  );

  test(
    'queue schedule timestamps are introduced as a Chinese planned time',
    () {
      const timestamp = '2026-09-26T08:30:00+08:00';
      final label = reasonLabel(timestamp);
      expect(label, startsWith('计划时间：'));
      expect(label, contains(displayTime(timestamp)));
      expect(label, isNot(contains('T08:30:00+08:00')));
    },
  );
}
