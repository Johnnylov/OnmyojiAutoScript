import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:oasx/solana/launch_options.dart';
import 'package:oasx/routes.dart';
import 'package:oasx/solana/solana_shell.dart';

void main() {
  test(
    'preview arguments require isolated storage and a valid backend origin',
    () {
      final defaults = LaunchOptions.parse([]);
      expect(defaults.dataDirectory, isNull);
      expect(defaults.backend, isNull);
      expect(defaults.skipStartupActions, isFalse);
      final isolatedPath =
          '${Directory.systemTemp.path}${Platform.pathSeparator}preview data';
      final options = LaunchOptions.parse([
        '--data-dir',
        isolatedPath,
        '--backend=http://127.0.0.1:22370/',
      ]);
      expect(options.dataDirectory, isolatedPath);
      expect(options.backend, 'http://127.0.0.1:22370');
      for (final invalid in [
        ['--backend', 'http://127.0.0.1:22370'],
        ['--data-dir', 'relative'],
        ['--data-dir'],
        ['--data-dir', r'D:\preview', '--backend', 'file:///tmp/server'],
        ['--data-dir', r'D:\preview', '--backend', 'http://host/api'],
        ['--data-dir', r'D:\preview', '--data-dir', r'D:\other'],
      ]) {
        expect(() => LaunchOptions.parse(invalid), throwsFormatException);
      }
    },
  );

  test(
    'one-launch startup suppression reaches the shell without isolation',
    () {
      final options = LaunchOptions.parse(['--skip-startup-actions']);
      expect(options.dataDirectory, isNull);
      expect(options.backend, isNull);
      expect(options.skipStartupActions, isTrue);
      final suppressed =
          Routes.build(
                skipStartupActions: options.skipStartupActions,
              ).single.page()
              as SolanaShell;
      expect(suppressed.skipStartupActions, isTrue);
      expect(suppressed.autoStart, isTrue);
      final nextNormalLaunch = Routes.routes.single.page() as SolanaShell;
      expect(nextNormalLaunch.skipStartupActions, isFalse);
      expect(nextNormalLaunch.autoStart, isTrue);
      for (final invalid in [
        ['--skip-startup-actions=true'],
        ['--skip-startup-actions', '--skip-startup-actions'],
      ]) {
        expect(() => LaunchOptions.parse(invalid), throwsFormatException);
      }
    },
  );
}
