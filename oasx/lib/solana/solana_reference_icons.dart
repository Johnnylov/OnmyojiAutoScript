import 'dart:math' as math;

import 'package:flutter/material.dart';

part 'solana_reference_icon_data.dart';

/// Uses the original README SVG geometry, with no font or SVG dependency.
class SolanaReferenceIcon extends StatelessWidget {
  final String name;
  final double size;
  final Color? color;
  const SolanaReferenceIcon(this.name, {super.key, this.size = 18, this.color});

  @override
  Widget build(BuildContext context) => SizedBox.square(
    dimension: size,
    child: CustomPaint(painter: _ReferenceIconPainter(name, color)),
  );
}

class _ReferenceVectorData {
  final Rect viewBox;
  final Color color;
  final double opacity;
  final String path;
  const _ReferenceVectorData(this.viewBox, this.color, this.opacity, this.path);
}

class _ReferenceIconPainter extends CustomPainter {
  final String name;
  final Color? color;
  const _ReferenceIconPainter(this.name, this.color);
  static final _paths = <String, Path>{};

  @override
  void paint(Canvas canvas, Size size) {
    final source = _referenceVectors[name]!;
    final path = _paths.putIfAbsent(
      name,
      () => _parseOriginalPath(source.path),
    );
    final box = source.viewBox;
    final scale = math.min(size.width / box.width, size.height / box.height);
    final paintColor = color ?? source.color;
    canvas.save();
    canvas.translate(
      (size.width - box.width * scale) / 2,
      (size.height - box.height * scale) / 2,
    );
    canvas.scale(scale);
    canvas.translate(-box.left, -box.top);
    canvas.drawPath(
      path,
      Paint()
        ..color = paintColor.withValues(alpha: paintColor.a * source.opacity),
    );
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant _ReferenceIconPainter oldDelegate) =>
      name != oldDelegate.name || color != oldDelegate.color;
}

/// The nine original paths use only absolute M/L/C/H/V/Z commands.
/// Fail for any other command rather than approximating an unsupported shape.
Path _parseOriginalPath(String data) {
  final tokens = RegExp(
    r'[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?',
  ).allMatches(data).map((match) => match.group(0)!).toList();
  final path = Path();
  var index = 0;
  var command = '';
  var x = 0.0, y = 0.0, startX = 0.0, startY = 0.0;
  double number() => double.parse(tokens[index++]);
  while (index < tokens.length) {
    if (RegExp(r'^[A-Z]$').hasMatch(tokens[index])) command = tokens[index++];
    switch (command) {
      case 'M':
        x = number();
        y = number();
        startX = x;
        startY = y;
        path.moveTo(x, y);
        command = 'L';
      case 'L':
        x = number();
        y = number();
        path.lineTo(x, y);
      case 'H':
        x = number();
        path.lineTo(x, y);
      case 'V':
        y = number();
        path.lineTo(x, y);
      case 'C':
        final x1 = number(), y1 = number(), x2 = number(), y2 = number();
        x = number();
        y = number();
        path.cubicTo(x1, y1, x2, y2, x, y);
      case 'Z':
        path.close();
        x = startX;
        y = startY;
        command = '';
      default:
        throw FormatException(
          'Unsupported reference icon path command: $command',
        );
    }
  }
  return path;
}
