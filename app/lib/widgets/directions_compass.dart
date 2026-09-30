import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models.dart';
import '../theme.dart';

/// "Where this could go" as a compass (server: presentation "compass" -- one
/// card per family): go deeper up, make it simpler down, make it real left,
/// go wider right, with a small rose in the middle. [onStage] only makes it
/// roomier (the stage places it clear of the character). Every card it is
/// given is shown: a second card of a family (a hand dealt before switching
/// to the compass can have one) sits in a row under the cross.
class DirectionsCompass extends StatelessWidget {
  const DirectionsCompass({
    super.key,
    required this.cards,
    required this.enabled,
    required this.onPick,
    this.onStage = false,
  });

  final List<DirectionCard> cards;
  final bool enabled;
  final void Function(DirectionCard card) onPick;
  final bool onStage;

  static const families = ['deeper', 'real', 'wider', 'simpler'];

  static String label(String family) => switch (family) {
    'real' => 'Make it real',
    'deeper' => 'Go deeper',
    'simpler' => 'Make it simpler',
    'wider' => 'Go wider',
    _ => 'Somewhere else',
  };

  static IconData icon(String family) => switch (family) {
    'real' => Icons.construction_rounded,
    'deeper' => Icons.south_rounded,
    'simpler' => Icons.lightbulb_outline_rounded,
    'wider' => Icons.open_in_full_rounded,
    _ => Icons.auto_awesome_outlined,
  };

  static Color color(String family) => switch (family) {
    'real' => const Color(0xFFC85A2E),
    'deeper' => const Color(0xFF3F6E9A),
    'simpler' => const Color(0xFF6D9A5C),
    'wider' => const Color(0xFF8A5CA8),
    _ => Paper.muted,
  };

  /// The card on each family's point: the first of that family.
  Map<String, DirectionCard> get _points {
    final out = <String, DirectionCard>{};
    for (final c in cards) {
      final f = c.family;
      if (f != null && families.contains(f)) out.putIfAbsent(f, () => c);
    }
    return out;
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, box) {
        // the middle (the character's space on the stage) and the cards shrink
        // with the room they have, so the cross always fits its width
        const middle = 44.0;
        final side = ((box.maxWidth - middle - 16) / 2).clamp(0.0, onStage ? 220.0 : 200.0);
        final points = _points;
        final extra = [
          for (final c in cards)
            if (!points.values.contains(c)) c,
        ];
        Widget point(String family) {
          final card = points[family];
          if (card == null) return SizedBox(width: side);
          return _Point(card: card, family: family, width: side, enabled: enabled, onTap: () => onPick(card));
        }

        final rose = Container(
          width: middle,
          height: middle,
          decoration: BoxDecoration(
            color: Paper.surface,
            shape: BoxShape.circle,
            border: Border.all(color: Paper.borderStrong),
          ),
          child: Icon(Icons.explore_outlined, size: 20, color: Paper.muted),
        );
        return Column(
          key: const ValueKey('directions-compass'),
          mainAxisSize: MainAxisSize.min,
          children: [
            point('deeper'),
            const SizedBox(height: 10),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [point('real'), const SizedBox(width: 8), rose, const SizedBox(width: 8), point('wider')],
            ),
            const SizedBox(height: 10),
            point('simpler'),
            for (final c in extra) ...[
              const SizedBox(height: 10),
              _Point(
                card: c,
                family: c.family ?? '',
                width: math.min(box.maxWidth, side * 2 + middle + 16),
                enabled: enabled,
                onTap: () => onPick(c),
              ),
            ],
          ],
        );
      },
    );
  }
}

class _Point extends StatelessWidget {
  const _Point({
    required this.card,
    required this.family,
    required this.width,
    required this.enabled,
    required this.onTap,
  });
  final DirectionCard card;
  final String family;
  final double width;
  final bool enabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = DirectionsCompass.color(family);
    final narrow = width < 150;
    return MouseRegion(
      cursor: enabled ? SystemMouseCursors.click : SystemMouseCursors.basic,
      child: GestureDetector(
        key: ValueKey('direction-${card.id}'),
        onTap: enabled ? onTap : null,
        child: Container(
          width: width,
          padding: EdgeInsets.all(narrow ? 10 : 12),
          decoration: BoxDecoration(
            color: Paper.card.withValues(alpha: .96),
            border: Border.all(color: color.withValues(alpha: .45)),
            borderRadius: BorderRadius.circular(12),
            boxShadow: [BoxShadow(color: color.withValues(alpha: .08), blurRadius: 10, offset: const Offset(0, 3))],
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(DirectionsCompass.icon(family), size: 13, color: color),
                  const SizedBox(width: 5),
                  Flexible(
                    child: Text(DirectionsCompass.label(family).toUpperCase(), style: mono(9, color: color)),
                  ),
                ],
              ),
              const SizedBox(height: 5),
              Text(card.text, style: sans(narrow ? 12.5 : 13, color: enabled ? Paper.ink : Paper.faint, height: 1.35)),
            ],
          ),
        ),
      ),
    );
  }
}
