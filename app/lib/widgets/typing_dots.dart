import 'package:flutter/material.dart';

import '../theme.dart';

/// Three pulsing dots: "the tutor is working on it".
class TypingDots extends StatefulWidget {
  const TypingDots({super.key});

  @override
  State<TypingDots> createState() => _TypingDotsState();
}

class _TypingDotsState extends State<TypingDots> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 1200))..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 22,
      child: AnimatedBuilder(
        animation: _c,
        builder: (context, _) => Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            for (var i = 0; i < 3; i++)
              Padding(
                padding: const EdgeInsets.only(right: 4),
                child: Opacity(
                  opacity: 0.3 + 0.7 * _pulse((_c.value - i * 0.15) % 1.0),
                  child: Container(
                    key: const ValueKey('typing-dot'),
                    width: 6,
                    height: 6,
                    decoration: const BoxDecoration(color: Paper.accent, shape: BoxShape.circle),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  double _pulse(double t) => t < 0.4 ? t / 0.4 : (t < 0.8 ? (0.8 - t) / 0.4 : 0.0);
}
