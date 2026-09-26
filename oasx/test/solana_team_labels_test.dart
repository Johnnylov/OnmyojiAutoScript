import 'dart:ui';

import 'package:flutter_test/flutter_test.dart';
import 'package:get/get.dart';
import 'package:oasx/translation/i18n.dart';

void main() {
  test(
    'local team and True Orochi fields use Chinese without changing keys',
    () {
      Get.addTranslations(Messages().keys);
      Get.locale = const Locale('zh', 'CN');
      addTearDown(Get.reset);
      const labels = {
        'local_team': '本机组队联动',
        'partner_config': '队友配置名',
        'sync_orochi': '八岐大蛇联动',
        'sync_bondling': '契灵联动',
        'sync_true_orochi': '真八岐大蛇联动',
        'ready_timeout': '就绪等待上限（秒）',
        'team_config': '真蛇双开组队',
        'teammate_config': '队友 OAS 配置名',
        'hosting_mode': '真蛇开车模式',
        'true_orochi_split': '队长、队员各一次',
      };
      for (final entry in labels.entries) {
        expect(entry.key.tr, entry.value, reason: entry.key);
      }
      expect('Sync True Orochi'.tr, '真八岐大蛇联动');
      expect('true_orochi_team_enable_help'.tr, contains('真八岐大蛇联动'));
      expect('true_orochi_teammate_config_help'.tr, contains('全局配置'));
    },
  );
}
