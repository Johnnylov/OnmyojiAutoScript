import 'package:get/get.dart';
import 'package:oasx/translation/i18n.dart';

import 'solana_api.dart';

String _taskKey(String value) =>
    value.replaceAll(RegExp(r'[\s_-]'), '').toLowerCase();

// Use the existing task/menu vocabulary, so snake_case API identifiers and
// PascalCase menu identifiers share a display name without changing identity.
final _taskTranslationKeys = <String, String>{
  for (final key in Messages().all_cn_translate.keys)
    if (RegExp(r'^[A-Z]').hasMatch(key)) _taskKey(key): key,
};

String taskLabel(Object? value) {
  final raw = textValue(value);
  final translated = raw.tr;
  if (translated != raw) return translated;
  final canonical = _taskTranslationKeys[_taskKey(raw)];
  if (canonical == null) return raw;
  final label = canonical.tr;
  return label == canonical ? raw : label;
}
