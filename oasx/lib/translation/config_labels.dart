import 'package:get/get.dart';

/// Numbered configuration groups share the base label; protocol keys stay intact.
String configGroupLabel(String name) {
  final direct = name.tr;
  if (direct != name) return direct;
  final repeated = RegExp(r'^(.+)_([1-9][0-9]*)$').firstMatch(name);
  if (repeated == null) return name;
  final base = repeated.group(1)!;
  final translated = base.tr;
  return translated == base ? name : '$translated ${repeated.group(2)}';
}
