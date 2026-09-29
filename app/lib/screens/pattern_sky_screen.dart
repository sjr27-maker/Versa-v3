import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../models.dart';
import '../theme.dart';
import '../widgets/directions_compass.dart';

/// One thinking-style fact, opened: the picks it rests on drawn as a sky
/// (server: style_patterns.py evidence + the card space). Every kind of next
/// step has a place -- concrete to the left, abstract to the right, deeper up,
/// simpler down -- and each of the learner's picks is a star at the card they
/// took. The picks that bear the fact out glow; the rest stay dim; a chat's
/// picks are joined into the path they walked. For a range (a level they
/// set), a band instead of a sky.
class PatternSkyScreen extends StatelessWidget {
  const PatternSkyScreen({super.key, required this.pattern, this.facets = const []});
  final StylePattern pattern;

  /// Weaker patterns pointing the same way -- "also seen as".
  final List<StylePattern> facets;

  @override
  Widget build(BuildContext context) {
    final p = pattern;
    final (label, color) = switch (p.status) {
      'confirmed' => ('Confirmed', Paper.olive),
      'fading' => ('Fading', Paper.warn),
      _ => ('Emerging', Paper.muted),
    };
    return Scaffold(
      backgroundColor: Paper.page,
      appBar: AppBar(
        backgroundColor: Paper.surface,
        elevation: 0,
        title: Text('How you explore', style: serif(18)),
      ),
      body: ListView(
        key: const ValueKey('pattern-sky'),
        padding: const EdgeInsets.fromLTRB(20, 20, 20, 40),
        children: [
          Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 760),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(label.toUpperCase(), style: mono(10.5).copyWith(color: color)),
                  const SizedBox(height: 6),
                  Text(p.statement, style: serif(20, height: 1.35)),
                  if (p.trials > 0) ...[
                    const SizedBox(height: 6),
                    Text(
                      'Guessed from your earlier picks, it was right ${p.hits} of ${p.trials} times.',
                      style: sans(13, color: Paper.muted),
                    ),
                  ],
                  const SizedBox(height: 16),
                  if (p.kind == 'range') _RangeBand(pattern: p) else _Sky(pattern: p),
                  const SizedBox(height: 10),
                  if (p.kind != 'range') const _Legend(),
                  const SizedBox(height: 22),
                  if (facets.isNotEmpty) ...[
                    Text('Also seen as', style: serif(17)),
                    const SizedBox(height: 6),
                    for (final f in facets)
                      Padding(
                        padding: const EdgeInsets.only(top: 4),
                        child: Text('\u2022 ${f.statement}', style: sans(13, color: Paper.body, height: 1.4)),
                      ),
                    const SizedBox(height: 20),
                  ],
                  Text('The checks it had to pass', style: serif(17)),
                  const SizedBox(height: 8),
                  for (final g in p.gates)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            g.ok ? Icons.check_rounded : Icons.remove_rounded,
                            size: 16,
                            color: g.ok ? Paper.olive : Paper.faint,
                          ),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              '${g.label}: ${g.have} (needs ${g.need})',
                              style: sans(13, color: Paper.body, height: 1.4),
                            ),
                          ),
                        ],
                      ),
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// --------------------------------------------------------------------- sky

class _Sky extends StatelessWidget {
  const _Sky({required this.pattern});
  final StylePattern pattern;

  @override
  Widget build(BuildContext context) {
    return AspectRatio(
      aspectRatio: 1.3,
      child: LayoutBuilder(
        builder: (context, box) {
          final size = Size(box.maxWidth, box.maxHeight);
          final layout = _SkyLayout(pattern.space, size);
          return ClipRRect(
            borderRadius: BorderRadius.circular(18),
            child: Stack(
              children: [
                Positioned.fill(
                  child: CustomPaint(key: const ValueKey('sky-paint'), painter: _SkyPainter(pattern, layout)),
                ),
                // names for the card types they actually met, placed clear of each other
                for (final entry in layout.labels(pattern).entries)
                  Positioned(
                    left: entry.value.dx,
                    top: entry.value.dy,
                    child: IgnorePointer(
                      child: Text(
                        pattern.space[entry.key]!.label,
                        style: sans(
                          10.5,
                          color: Colors.white.withValues(alpha: _isTarget(pattern, entry.key) ? .95 : .55),
                          weight: _isTarget(pattern, entry.key) ? FontWeight.w600 : FontWeight.w400,
                        ),
                      ),
                    ),
                  ),
                // the four ways out, named in their own colour at the edges
                for (final (family, alignment, turns) in const [
                  ('deeper', Alignment.topCenter, 0),
                  ('simpler', Alignment.bottomCenter, 0),
                  ('real', Alignment.centerLeft, 3),
                  ('wider', Alignment.centerRight, 1),
                ])
                  Align(
                    alignment: alignment,
                    child: Padding(
                      padding: const EdgeInsets.all(6),
                      child: RotatedBox(
                        quarterTurns: turns,
                        child: Text(
                          DirectionsCompass.label(family).toUpperCase(),
                          style: mono(8.5, color: DirectionsCompass.color(family).withValues(alpha: .75)),
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          );
        },
      ),
    );
  }
}

/// The card type(s) a pattern is about: the way in, both ends of a "then",
/// every type of a family, or the card or family passed over.
bool _isTarget(StylePattern p, String type) {
  final key = p.key;
  if (p.kind == 'shape') return false; // a movement, not a place
  if (p.kind == 'speed') return p.space[type]?.family == key; // key: a family
  if (p.kind == 'conditional') {
    // "position:real|deeper": both sides' ways out are what it is about
    return key.split(':').last.split('|').contains(p.space[type]?.family);
  }
  if (key.startsWith('family:')) {
    final fams = key.substring(7).split('>');
    return fams.contains(p.space[type]?.family);
  }
  return key.split('>').contains(type);
}

class _SkyLayout {
  /// Laid out like the compass: the answer in the middle, the four ways out
  /// around it -- go deeper up, make it simpler down, make it real left, go
  /// wider right -- and each family's card types fanned out across its own
  /// region (ordered by where they sit on the other axis), so nothing piles up.
  _SkyLayout(this.space, this.size) {
    final c = size.center(Offset.zero);
    final w = size.width, h = size.height;
    final regions = <String, (Offset, Offset)>{
      // (region centre, direction the types fan out along)
      'deeper': (Offset(c.dx, h * .19), const Offset(1, 0)),
      'simpler': (Offset(c.dx, h * .81), const Offset(1, 0)),
      'real': (Offset(w * .2, c.dy), const Offset(0, 1)),
      'wider': (Offset(w * .8, c.dy), const Offset(0, 1)),
    };
    final pos = <String, Offset>{};
    for (final entry in regions.entries) {
      final types = space.entries.where((e) => e.value.family == entry.key).toList()
        // across the region: fan by the other axis (deeper ones higher on the
        // sides, more concrete ones further left at the top and bottom)
        ..sort(
          (a, b) => entry.value.$2.dy != 0
              ? b.value.depth.compareTo(a.value.depth)
              : b.value.concrete.compareTo(a.value.concrete),
        );
      final (centre, along) = entry.value;
      final spacing = along.dy != 0 ? h * .16 : w * .17;
      for (final (i, t) in types.indexed) {
        final off = (i - (types.length - 1) / 2) * spacing;
        // a slight bow toward the middle, so a region reads as an arc
        final bow = (centre - c) / (centre - c).distance * (-(off.abs()) * .18);
        pos[t.key] = centre + along * off + bow;
      }
    }
    at = pos;
    centre = c;
  }

  late final Offset centre;

  final Map<String, SpacePoint> space;
  final Size size;
  late final Map<String, Offset> at;

  /// A pick drawn near its card's place, a little apart from the others there.
  Offset pick(String type, int index) {
    final base = at[type] ?? Offset(size.width / 2, size.height / 2);
    final angle = index * 2.39996; // golden angle: an even spread, the same every time
    final r = 7.0 + 3.6 * math.sqrt(index.toDouble());
    return base + Offset(math.cos(angle) * r, math.sin(angle) * r);
  }

  /// Label positions for the types in play, each on the side with room.
  Map<String, Offset> labels(StylePattern p) {
    final met = <String>{
      for (final e in p.evidence)
        if (e.card != null) e.card!,
    };
    for (final t in space.keys) {
      if (_isTarget(p, t)) met.add(t);
    }
    // each card's cluster of picks, as the painter spreads them (see pick())
    final counts = <String, int>{};
    for (final e in p.evidence) {
      if (e.card != null) counts.update(e.card!, (v) => v + 1, ifAbsent: () => 1);
    }
    double reach(String t) => (counts[t] ?? 0) == 0 ? 9 : 10 + 3.6 * math.sqrt(counts[t]!.toDouble());
    final placed = <Rect>[];
    final out = <String, Offset>{};
    final order = met.toList()..sort((a, b) => (_isTarget(p, b) ? 1 : 0) - (_isTarget(p, a) ? 1 : 0));
    for (final t in order) {
      final c = at[t];
      if (c == null) continue;
      final w = (space[t]?.label.length ?? 6) * 5.6 + 6;
      final r0 = reach(t);
      // four places to put a name: beside, above or below its cluster
      final boxes = [
        Rect.fromLTWH(c.dx + r0 + 3, c.dy - 7, w, 14),
        Rect.fromLTWH(c.dx - r0 - 3 - w, c.dy - 7, w, 14),
        Rect.fromLTWH(c.dx - w / 2, c.dy - r0 - 16, w, 14),
        Rect.fromLTWH(c.dx - w / 2, c.dy + r0 + 2, w, 14),
      ];
      double cost(Rect r) {
        if (r.left < 14 || r.right > size.width - 14 || r.top < 14 || r.bottom > size.height - 14) return 1e9;
        var total = 0.0;
        for (final q in placed) {
          final i = r.intersect(q);
          if (i.width > 0 && i.height > 0) total += i.width * i.height;
        }
        for (final e in at.entries) {
          final i = r.intersect(Rect.fromCircle(center: e.value, radius: reach(e.key)));
          if (i.width > 0 && i.height > 0) total += i.width * i.height;
        }
        return total;
      }

      var best = boxes.first;
      for (final r in boxes.skip(1)) {
        if (cost(r) < cost(best)) best = r;
      }
      placed.add(best);
      out[t] = best.topLeft;
    }
    return out;
  }
}

class _SkyPainter extends CustomPainter {
  _SkyPainter(this.pattern, this.layout);
  final StylePattern pattern;
  final _SkyLayout layout;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    canvas.drawRect(
      rect,
      Paint()
        ..shader = const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Color(0xFF12112A), Color(0xFF1E1A33), Color(0xFF231C2C)],
        ).createShader(rect),
    );
    // faint background stars, the same every time
    final dust = Paint()..color = Colors.white.withValues(alpha: .10);
    final rng = math.Random(11);
    for (var i = 0; i < 90; i++) {
      canvas.drawCircle(
        Offset(rng.nextDouble() * size.width, rng.nextDouble() * size.height),
        rng.nextDouble() * .9 + .3,
        dust,
      );
    }
    // a nebula for each family: the region of the space its cards live in
    final byFamily = <String, List<Offset>>{};
    layout.space.forEach((t, s) => byFamily.putIfAbsent(s.family, () => []).add(layout.at[t]!));
    byFamily.forEach((family, points) {
      final centre = points.reduce((a, b) => a + b) / points.length.toDouble();
      final lit = _isTarget(pattern, points.isEmpty ? '' : _typeAt(family));
      final radius = size.shortestSide * .34;
      canvas.drawCircle(
        centre,
        radius,
        Paint()
          ..shader = RadialGradient(
            colors: [
              DirectionsCompass.color(family).withValues(alpha: lit ? .30 : .14),
              DirectionsCompass.color(family).withValues(alpha: 0),
            ],
          ).createShader(Rect.fromCircle(center: centre, radius: radius)),
      );
    });
    // every card type, faint: the places a pick could land
    layout.at.forEach((t, p) {
      final target = _isTarget(pattern, t);
      final color = DirectionsCompass.color(layout.space[t]!.family);
      if (target) {
        canvas.drawCircle(
          p,
          13,
          Paint()
            ..color = color.withValues(alpha: .9)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.4,
        );
        canvas.drawCircle(
          p,
          19,
          Paint()
            ..color = color.withValues(alpha: .35)
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1,
        );
      }
      canvas.drawCircle(p, 2.2, Paint()..color = Colors.white.withValues(alpha: target ? .9 : .28));
    });
    // a "then" pattern: the step it names, as an arrow between the two cards
    final keyParts = pattern.key.replaceFirst('family:', '').split('>');
    if (pattern.kind == 'then' && keyParts.length == 2 && !pattern.key.startsWith('family:')) {
      final a = layout.at[keyParts[0]], b = layout.at[keyParts[1]];
      if (a != null && b != null) _arrow(canvas, a, b, Colors.white.withValues(alpha: .75));
    }
    // a lean on the concrete or depth axis: an arrow across the sky its way
    // (concrete is toward "make it real" on the left, deeper is up); the
    // shape of a chat is drawn the same way -- where its picks move to
    if ((pattern.kind == 'lean' || pattern.kind == 'shape') && pattern.rate != null) {
      final centre = size.center(Offset.zero);
      final reach = size.shortestSide * .32;
      final lean = pattern.rate!;
      final dir = switch (pattern.key) {
        'concrete' => Offset(lean > 0 ? -reach : reach, 0),
        'depth' => Offset(0, lean > 0 ? -reach : reach),
        _ => null,
      };
      if (dir != null) _arrow(canvas, centre - dir * .35, centre + dir, Colors.white.withValues(alpha: .55));
    }
    // the answer, in the middle: every way out starts here
    canvas.drawCircle(layout.centre, 4, Paint()..color = Colors.white.withValues(alpha: .9));
    canvas.drawCircle(
      layout.centre,
      9,
      Paint()
        ..color = Colors.white.withValues(alpha: .18)
        ..style = PaintingStyle.stroke,
    );
    // the path a chat walked: its picks, joined in order
    final bySession = <String, List<Offset>>{};
    final perType = <String, int>{};
    final stars = <(Offset, bool, String)>[];
    for (final e in pattern.evidence) {
      final card = e.card;
      if (card == null || !layout.at.containsKey(card)) continue;
      final n = perType.update(card, (v) => v + 1, ifAbsent: () => 0);
      final pos = layout.pick(card, n);
      stars.add((pos, e.supports, layout.space[card]!.family));
      if (e.session != null) bySession.putIfAbsent(e.session!, () => []).add(pos);
    }
    if (pattern.kind == 'way_in') {
      // a first move is a way out of the answer: a ray from the middle
      for (final (pos, supports, family) in stars) {
        canvas.drawLine(
          layout.centre,
          pos,
          Paint()
            ..color = (supports ? DirectionsCompass.color(family) : Colors.white).withValues(
              alpha: supports ? .28 : .08,
            )
            ..strokeWidth = 1,
        );
      }
    } else {
      final path = Paint()
        ..color = Colors.white.withValues(alpha: .10)
        ..strokeWidth = 1;
      for (final points in bySession.values) {
        for (var i = 1; i < points.length; i++) {
          canvas.drawLine(points[i - 1], points[i], path);
        }
      }
    }
    // the picks themselves: the ones that bear the fact out glow
    for (final (pos, supports, family) in stars) {
      final color = DirectionsCompass.color(family);
      if (supports) {
        canvas.drawCircle(
          pos,
          6,
          Paint()
            ..color = color.withValues(alpha: .45)
            ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 4),
        );
        canvas.drawCircle(pos, 2.6, Paint()..color = Color.lerp(color, Colors.white, .45)!);
      } else {
        canvas.drawCircle(pos, 1.6, Paint()..color = Colors.white.withValues(alpha: .35));
      }
    }
  }

  String _typeAt(String family) =>
      layout.space.entries.firstWhere((e) => e.value.family == family, orElse: () => layout.space.entries.first).key;

  void _arrow(Canvas canvas, Offset from, Offset to, Color color) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 1.6
      ..style = PaintingStyle.stroke;
    final mid = (from + to) / 2 + Offset(-(to - from).dy, (to - from).dx) * .15;
    canvas.drawPath(
      Path()
        ..moveTo(from.dx, from.dy)
        ..quadraticBezierTo(mid.dx, mid.dy, to.dx, to.dy),
      paint,
    );
    final dir = (to - mid) / (to - mid).distance;
    final left = Offset(-dir.dy, dir.dx);
    canvas.drawPath(
      Path()
        ..moveTo(to.dx, to.dy)
        ..lineTo(to.dx - dir.dx * 9 + left.dx * 4.5, to.dy - dir.dy * 9 + left.dy * 4.5)
        ..lineTo(to.dx - dir.dx * 9 - left.dx * 4.5, to.dy - dir.dy * 9 - left.dy * 4.5)
        ..close(),
      Paint()..color = color,
    );
  }

  @override
  bool shouldRepaint(_SkyPainter old) => old.pattern != pattern;
}

class _Legend extends StatelessWidget {
  const _Legend();

  @override
  Widget build(BuildContext context) {
    Widget item(Widget mark, String text) => Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        mark,
        const SizedBox(width: 6),
        Text(text, style: sans(12, color: Paper.muted)),
      ],
    );
    return Wrap(
      spacing: 16,
      runSpacing: 6,
      children: [
        item(
          Container(
            width: 9,
            height: 9,
            decoration: const BoxDecoration(color: Paper.accent, shape: BoxShape.circle),
          ),
          'a pick that bears it out',
        ),
        item(
          Container(
            width: 5,
            height: 5,
            decoration: const BoxDecoration(color: Paper.faint, shape: BoxShape.circle),
          ),
          'your other picks',
        ),
        item(Container(width: 16, height: 1.5, color: Paper.faint), 'your path through a chat'),
        item(
          Container(
            width: 12,
            height: 12,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              border: Border.all(color: Paper.accent),
            ),
          ),
          'what it is about',
        ),
      ],
    );
  }
}

// ------------------------------------------------------------------- range

class _RangeBand extends StatelessWidget {
  const _RangeBand({required this.pattern});
  final StylePattern pattern;

  @override
  Widget build(BuildContext context) {
    final values = [
      for (final e in pattern.evidence)
        if (e.value != null) e.value!,
    ];
    return Container(
      key: const ValueKey('range-band'),
      padding: const EdgeInsets.fromLTRB(16, 18, 16, 14),
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            height: 46,
            child: LayoutBuilder(
              builder: (context, box) {
                final w = box.maxWidth;
                final lo = values.isEmpty ? 0 : values.reduce(math.min);
                final hi = values.isEmpty ? 0 : values.reduce(math.max);
                return Stack(
                  children: [
                    Positioned(left: 0, right: 0, top: 21, child: Container(height: 4, color: Paper.border)),
                    if (values.isNotEmpty)
                      Positioned(
                        left: w * lo / 100,
                        width: math.max(4, w * (hi - lo) / 100),
                        top: 17,
                        child: Container(
                          height: 12,
                          decoration: BoxDecoration(
                            color: Paper.accentSoft,
                            border: Border.all(color: Paper.accentLine),
                            borderRadius: BorderRadius.circular(6),
                          ),
                        ),
                      ),
                    for (final (i, v) in values.indexed)
                      Positioned(
                        left: (w * v / 100 - 5).clamp(0, w - 10),
                        top: 18 + (i % 2) * 2,
                        child: Container(
                          width: 10,
                          height: 10,
                          decoration: BoxDecoration(color: Paper.accent.withValues(alpha: .75), shape: BoxShape.circle),
                        ),
                      ),
                  ],
                );
              },
            ),
          ),
          Row(
            children: [
              Text('0', style: mono(10)),
              const Spacer(),
              Text('each dot: a session, where you left it', style: sans(11.5, color: Paper.muted)),
              const Spacer(),
              Text('100', style: mono(10)),
            ],
          ),
        ],
      ),
    );
  }
}
