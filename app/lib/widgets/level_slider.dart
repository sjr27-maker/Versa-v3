import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

import '../theme.dart';

/// A 0-100 session knob: drag it, or scroll the mouse wheel over it (down =
/// more). Every change is reported at once; the caller decides when to act.
class LevelSlider extends StatelessWidget {
  const LevelSlider({
    super.key,
    required this.sliderKey,
    required this.label,
    required this.lowLabel,
    required this.highLabel,
    required this.value,
    required this.onChanged,
    this.wheelStep = 5,
  });

  final Key sliderKey;
  final String label;
  final String lowLabel;
  final String highLabel;
  final int value;
  final ValueChanged<int> onChanged;
  final int wheelStep;

  void _onWheel(PointerSignalEvent event) {
    if (event is! PointerScrollEvent || event.scrollDelta.dy == 0) return;
    final next = (value + (event.scrollDelta.dy > 0 ? wheelStep : -wheelStep)).clamp(0, 100);
    if (next != value) onChanged(next);
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(label, style: sans(12, weight: FontWeight.w500))),
              Text('$value', key: ValueKey('$sliderKey-value'), style: mono(11, color: Paper.body)),
            ],
          ),
          Listener(
            onPointerSignal: _onWheel,
            child: SliderTheme(
              data: SliderTheme.of(context).copyWith(
                trackHeight: 3,
                activeTrackColor: Paper.accent,
                inactiveTrackColor: Paper.border,
                thumbColor: Paper.accent,
                overlayShape: const RoundSliderOverlayShape(overlayRadius: 14),
                thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 7),
              ),
              child: Slider(
                key: sliderKey,
                min: 0,
                max: 100,
                divisions: 100,
                value: value.toDouble(),
                semanticFormatterCallback: (v) => '$label ${v.round()} of 100',
                onChanged: (v) {
                  final next = v.round();
                  if (next != value) onChanged(next);
                },
              ),
            ),
          ),
          Row(
            children: [
              Text(lowLabel, style: sans(10.5, color: Paper.faint)),
              const Spacer(),
              Text(highLabel, style: sans(10.5, color: Paper.faint)),
            ],
          ),
        ],
      ),
    );
  }
}
