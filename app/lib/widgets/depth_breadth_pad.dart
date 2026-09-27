import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../theme.dart';

/// Depth and breadth as one square you move through: depth up the side
/// (gist -> rigorous), breadth along the bottom (focused -> wide). The
/// space of possibilities made visible -- the learner's two limits are a
/// single place, and moving it re-pitches the answer and its directions.
///
/// Drag or tap to move the dot; arrow keys nudge it by [step]. Every change
/// is reported at once; the caller decides when to act (ChatController
/// saves once it rests).
class DepthBreadthPad extends StatelessWidget {
  const DepthBreadthPad({
    super.key,
    required this.padKey,
    required this.depth,
    required this.breadth,
    required this.onChanged,
    this.step = 5,
  });

  final Key padKey;
  final int depth;
  final int breadth;
  final void Function(int depth, int breadth) onChanged;
  final int step;

  void _moveTo(Offset local, Size size) {
    final b = (local.dx / size.width * 100).round().clamp(0, 100);
    final d = ((1 - local.dy / size.height) * 100).round().clamp(0, 100);
    if (b != breadth || d != depth) onChanged(d, b);
  }

  KeyEventResult _onKey(FocusNode _, KeyEvent event) {
    if (event is! KeyDownEvent && event is! KeyRepeatEvent) return KeyEventResult.ignored;
    var d = depth, b = breadth;
    switch (event.logicalKey) {
      case LogicalKeyboardKey.arrowUp:
        d += step;
      case LogicalKeyboardKey.arrowDown:
        d -= step;
      case LogicalKeyboardKey.arrowRight:
        b += step;
      case LogicalKeyboardKey.arrowLeft:
        b -= step;
      default:
        return KeyEventResult.ignored;
    }
    d = d.clamp(0, 100);
    b = b.clamp(0, 100);
    if (d != depth || b != breadth) onChanged(d, b);
    return KeyEventResult.handled;
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            Expanded(child: Text('Depth × breadth', style: sans(12.5, color: Paper.body, weight: FontWeight.w600))),
            Text('$depth · $breadth', key: ValueKey('$padKey-value'), style: mono(10.5, color: Paper.accent)),
          ]),
          const SizedBox(height: 4),
          Text('rigorous', style: mono(9)),
          const SizedBox(height: 2),
          AspectRatio(
            aspectRatio: 1,
            child: LayoutBuilder(builder: (context, c) {
              final size = Size(c.maxWidth, c.maxHeight);
              return Semantics(
                label: 'Depth $depth of 100, breadth $breadth of 100. Arrow keys move it.',
                child: Focus(
                  onKeyEvent: _onKey,
                  child: Builder(builder: (context) {
                    final focused = Focus.of(context).hasFocus;
                    return GestureDetector(
                      key: padKey,
                      behavior: HitTestBehavior.opaque,
                      onTapDown: (d) {
                        Focus.of(context).requestFocus();
                        _moveTo(d.localPosition, size);
                      },
                      onPanStart: (d) => _moveTo(d.localPosition, size),
                      onPanUpdate: (d) => _moveTo(d.localPosition, size),
                      child: CustomPaint(
                        size: size,
                        painter: _PadPainter(depth: depth, breadth: breadth, focused: focused),
                      ),
                    );
                  }),
                ),
              );
            }),
          ),
          const SizedBox(height: 2),
          Row(children: [
            Text('gist · focused', style: mono(9)),
            const Spacer(),
            Text('wide', style: mono(9)),
          ]),
        ],
      ),
    );
  }
}

class _PadPainter extends CustomPainter {
  _PadPainter({required this.depth, required this.breadth, required this.focused});
  final int depth;
  final int breadth;
  final bool focused;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final r = RRect.fromRectAndRadius(rect, const Radius.circular(12));
    canvas.drawRRect(r, Paint()..color = Paper.card);
    // a soft wash that deepens toward rigorous and wide
    canvas.drawRRect(
      r,
      Paint()
        ..shader = const LinearGradient(
          begin: Alignment.bottomLeft,
          end: Alignment.topRight,
          colors: [Color(0x00C85A2E), Color(0x22C85A2E)],
        ).createShader(rect),
    );
    final grid = Paint()
      ..color = Paper.border
      ..strokeWidth = 1;
    for (var i = 1; i < 4; i++) {
      final t = i / 4;
      canvas.drawLine(Offset(size.width * t, 0), Offset(size.width * t, size.height), grid);
      canvas.drawLine(Offset(0, size.height * t), Offset(size.width, size.height * t), grid);
    }
    canvas.drawRRect(
      r,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = focused ? 1.6 : 1
        ..color = focused ? Paper.accent : Paper.borderStrong,
    );
    final dot = Offset(size.width * breadth / 100, size.height * (1 - depth / 100));
    // the dashed guides from the dot to each axis
    final guide = Paint()
      ..color = Paper.accentLine
      ..strokeWidth = 1;
    canvas.drawLine(Offset(dot.dx, dot.dy), Offset(dot.dx, size.height), guide);
    canvas.drawLine(Offset(0, dot.dy), Offset(dot.dx, dot.dy), guide);
    canvas.drawCircle(dot, 11, Paint()..color = const Color(0x33C85A2E));
    canvas.drawCircle(dot, 7, Paint()..color = Paper.accent);
    canvas.drawCircle(dot, 2.5, Paint()..color = Paper.card);
  }

  @override
  bool shouldRepaint(_PadPainter old) =>
      old.depth != depth || old.breadth != breadth || old.focused != focused;
}
