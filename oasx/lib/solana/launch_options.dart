import 'dart:io';

import 'package:get_storage/get_storage.dart';
import 'package:oasx/modules/common/models/storage_key.dart';

/// Explicit preview arguments are parsed before any default storage is opened.
class LaunchOptions {
  final String? dataDirectory;
  final String? backend;

  /// Suppresses saved deployment/run actions only for this process launch.
  /// Never persisted: a normal later launch still honors the saved preferences.
  final bool skipStartupActions;
  const LaunchOptions({
    this.dataDirectory,
    this.backend,
    this.skipStartupActions = false,
  });

  factory LaunchOptions.parse(List<String> arguments) {
    final values = <String, String>{};
    var skipStartupActions = false;
    for (var index = 0; index < arguments.length; index++) {
      final argument = arguments[index];
      final equals = argument.indexOf('=');
      final key = equals >= 0 ? argument.substring(0, equals) : argument;
      if (key == '--skip-startup-actions') {
        if (equals >= 0 || skipStartupActions) {
          throw const FormatException(
            '--skip-startup-actions is a single flag without a value',
          );
        }
        skipStartupActions = true;
        continue;
      }
      if (key != '--data-dir' && key != '--backend') continue;
      if (values.containsKey(key)) {
        throw FormatException('Duplicate launch argument: $key');
      }
      final String value;
      if (equals >= 0) {
        value = argument.substring(equals + 1).trim();
      } else if (index + 1 < arguments.length &&
          !arguments[index + 1].startsWith('--')) {
        value = arguments[++index].trim();
      } else {
        throw FormatException('Missing value for $key');
      }
      if (value.isEmpty || value.contains('\u0000')) {
        throw FormatException('Invalid value for $key');
      }
      values[key] = value;
    }
    final directory = values['--data-dir'];
    if (directory != null && !Directory(directory).isAbsolute) {
      throw const FormatException('--data-dir must be an absolute path');
    }
    var backend = values['--backend'];
    if (backend != null) {
      if (directory == null) {
        throw const FormatException(
          '--backend requires an explicit --data-dir',
        );
      }
      final uri = Uri.tryParse(backend);
      if (uri == null ||
          !['http', 'https'].contains(uri.scheme) ||
          uri.host.isEmpty ||
          uri.port < 1 ||
          uri.port > 65535 ||
          uri.userInfo.isNotEmpty ||
          uri.hasQuery ||
          uri.hasFragment ||
          (uri.path.isNotEmpty && uri.path != '/')) {
        throw const FormatException(
          '--backend must be an HTTP(S) server origin',
        );
      }
      backend = '${uri.scheme}://${uri.authority}';
    }
    return LaunchOptions(
      dataDirectory: directory,
      backend: backend,
      skipStartupActions: skipStartupActions,
    );
  }

  Future<void> initializeIsolatedStorage() async {
    final path = dataDirectory;
    if (path == null) return;
    final directory = await Directory(path).create(recursive: true);
    final cache = await Directory(
      '${directory.path}${Platform.pathSeparator}cache',
    ).create(recursive: true);
    // GetStorage caches by container name; every existing GetStorage() consumer
    // then resolves to this explicitly located container, including initService.
    final storage = GetStorage('GetStorage', directory.path);
    await storage.initStorage;
    if (backend != null) {
      await storage.write(StorageKey.address.name, backend);
    }
    await storage.write(StorageKey.temporaryDirectory.name, cache.path);
  }
}
