import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';

/// Design lab: three ways to present "where this could go" once the card
/// library is bigger than a screen (server: directions.py lib-v2 -- 16 card
/// types in four families, a hand dealt from a pool). Design only: sample
/// cards for one answer, nothing is sent to the server or recorded.
///
///   Hand of 3      three cards, one per family, "other directions" deals more
///   Compass        four ways out of the answer, one card per family
///   Constellation  every card type as a star, placed by where it sits in the
///                  space (concrete <-> abstract, simpler <-> deeper)
class DirectionsLabScreen extends StatelessWidget {
  const DirectionsLabScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        backgroundColor: Paper.page,
        appBar: AppBar(
          backgroundColor: Paper.surface,
          elevation: 0,
          title: Text('Design lab · where this could go', style: serif(18)),
          bottom: TabBar(
            labelColor: Paper.accentDark,
            unselectedLabelColor: Paper.muted,
            indicatorColor: Paper.accent,
            labelStyle: sans(13, weight: FontWeight.w600),
            tabs: const [
              Tab(key: ValueKey('lab-tab-hand'), text: 'Hand of 3'),
              Tab(key: ValueKey('lab-tab-compass'), text: 'Compass'),
              Tab(key: ValueKey('lab-tab-constellation'), text: 'Constellation'),
            ],
          ),
        ),
        body: const TabBarView(children: [_HandMock(), _CompassMock(), _ConstellationMock()]),
      ),
    );
  }
}

// --------------------------------------------------------------- sample data

enum _Family { real, deeper, simpler, wider }

extension on _Family {
  String get label => switch (this) {
    _Family.real => 'Make it real',
    _Family.deeper => 'Go deeper',
    _Family.simpler => 'Make it simpler',
    _Family.wider => 'Go wider',
  };

  IconData get icon => switch (this) {
    _Family.real => Icons.construction_rounded,
    _Family.deeper => Icons.south_rounded,
    _Family.simpler => Icons.lightbulb_outline_rounded,
    _Family.wider => Icons.open_in_full_rounded,
  };

  Color get color => switch (this) {
    _Family.real => const Color(0xFFC85A2E),
    _Family.deeper => const Color(0xFF3F6E9A),
    _Family.simpler => const Color(0xFF6D9A5C),
    _Family.wider => const Color(0xFF8A5CA8),
  };
}

class _Card {
  const _Card(this.type, this.family, this.text, this.concrete, this.depth, this.breadth);
  final String type;
  final _Family family;
  final String text;
  // where the type sits in the space, -1..1 (server: directions.COORDS)
  final double concrete;
  final double depth;
  final double breadth;
}

const _answer =
    'A derivative is the rate of change at a single instant — the slope of the curve '
    'right at that point, found by shrinking the interval until it vanishes.';

const _library = <_Card>[
  _Card('example', _Family.real, 'Work one out: the slope of x³ at 2', 1, 0, -.5),
  _Card('use', _Family.real, 'Where engineers use it: speed from position', .5, 0, .5),
  _Card('try_it', _Family.real, 'Let me try: estimate a slope by hand', 1, 0, -.5),
  _Card('real_data', _Family.real, 'A real car’s speedometer data', 1, .5, 0),
  _Card('why', _Family.deeper, 'Why shrinking the gap gives the exact slope', -.5, 1, 0),
  _Card('deeper', _Family.deeper, 'The formal limit definition', -.5, 1, -.5),
  _Card('prove_it', _Family.deeper, 'Prove the power rule', -1, 1, -.5),
  _Card('mistake', _Family.deeper, 'The mistake everyone makes with dx', .5, .5, -.5),
  _Card('intuition', _Family.simpler, 'The speedometer picture', .5, -1, 0),
  _Card('visualise', _Family.simpler, 'Show me the tangent line sliding', .5, -.5, 0),
  _Card('story', _Family.simpler, 'How Newton and Leibniz both found it', .5, -.5, .5),
  _Card('summary', _Family.simpler, 'The whole idea in one sentence', -.5, -1, -.5),
  _Card('next', _Family.wider, 'What comes next: the integral', -.5, .5, .5),
  _Card('compare', _Family.wider, 'Derivative vs average rate of change', 0, 0, 1),
  _Card('connect', _Family.wider, 'The same idea in economics: marginal cost', 0, 0, 1),
  _Card('debate', _Family.wider, 'Were infinitesimals ever “real”?', -.5, .5, 1),
];

/// A hand: one card from each of three families, at random -- the same
/// layered draw as the server's deal_hand (design copy).
List<_Card> _deal(math.Random rng, {Set<String> skip = const {}}) {
  final families = [..._Family.values]..shuffle(rng);
  return [
    for (final f in families.take(3))
      (_library.where((c) => c.family == f && !skip.contains(c.type)).toList()..shuffle(rng)).first,
  ];
}

class _AnswerBubble extends StatelessWidget {
  const _AnswerBubble();

  @override
  Widget build(BuildContext context) => ConstrainedBox(
    constraints: const BoxConstraints(maxWidth: 620),
    child: Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(_answer, style: sans(14, height: 1.55)),
    ),
  );
}

class _Note extends StatelessWidget {
  const _Note(this.text);
  final String text;

  @override
  Widget build(BuildContext context) => Text(
    text,
    style: sans(12, color: Paper.muted, height: 1.45).copyWith(fontStyle: FontStyle.italic),
  );
}

// ---------------------------------------------------------------- hand of 3

class _HandMock extends StatefulWidget {
  const _HandMock();

  @override
  State<_HandMock> createState() => _HandMockState();
}

class _HandMockState extends State<_HandMock> {
  final _rng = math.Random(7);
  late List<_Card> _hand = _deal(_rng);
  final Set<String> _seen = {};
  _Card? _taken;
  bool _fork = false;

  void _more() => setState(() {
    _seen.addAll(_hand.map((c) => c.type));
    _hand = _deal(_rng, skip: _seen.length > 12 ? const {} : _seen);
    _taken = null;
  });

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 24, 24, 48),
      children: [
        const _AnswerBubble(),
        const SizedBox(height: 14),
        if (_fork)
          Wrap(
            key: const ValueKey('lab-hand-fork'),
            crossAxisAlignment: WrapCrossAlignment.center,
            runSpacing: 4,
            children: [
              Text(
                'Continue with →  ',
                style: sans(13.5, color: Paper.muted, weight: FontWeight.w600),
              ),
              for (final (i, c) in _hand.indexed) ...[
                if (i > 0) Text('  ·  ', style: sans(13.5, color: Paper.faint)),
                GestureDetector(
                  onTap: () => setState(() => _taken = c),
                  child: Text(
                    c.text,
                    style: sans(13.5, color: Paper.accentDark, weight: FontWeight.w500).copyWith(
                      decoration: TextDecoration.underline,
                      decorationColor: Paper.accentLine,
                      decorationThickness: 2,
                    ),
                  ),
                ),
              ],
            ],
          )
        else ...[
          Text('WHERE THIS COULD GO', style: mono(9.5)),
          const SizedBox(height: 8),
          Wrap(
            key: const ValueKey('lab-hand'),
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final c in _hand)
                ActionChip(
                  key: ValueKey('lab-card-${c.type}'),
                  avatar: Icon(c.family.icon, size: 14, color: c.family.color),
                  label: Text(c.text, style: sans(13, color: Paper.ink)),
                  backgroundColor: _taken == c ? Paper.accentSoft : Paper.card,
                  side: BorderSide(color: _taken == c ? Paper.accentLine : Paper.border),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
                  onPressed: () => setState(() => _taken = c),
                ),
            ],
          ),
        ],
        const SizedBox(height: 6),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            key: const ValueKey('lab-more'),
            onPressed: _more,
            icon: const Icon(Icons.refresh_rounded, size: 14, color: Paper.muted),
            label: Text('other directions', style: sans(12, color: Paper.muted)),
          ),
        ),
        if (_taken != null) ...[
          const SizedBox(height: 8),
          _Note(
            'Taken: “${_taken!.text}” — ${_taken!.family.label.toLowerCase()}, '
            'read against the other two in this hand.',
          ),
        ],
        const SizedBox(height: 24),
        Row(
          children: [
            Text('Cards', style: sans(12.5, color: _fork ? Paper.muted : Paper.ink)),
            Switch(
              key: const ValueKey('lab-fork-switch'),
              value: _fork,
              activeThumbColor: Colors.white,
              activeTrackColor: Paper.accent,
              onChanged: (v) => setState(() => _fork = v),
            ),
            Text('Fork links', style: sans(12.5, color: _fork ? Paper.ink : Paper.muted)),
          ],
        ),
        const SizedBox(height: 8),
        const _Note(
          'Three cards, one from each of three families, dealt at random from this answer’s pool. '
          '“Other directions” deals the next three instantly — and tells Versa nothing here matched.',
        ),
      ],
    );
  }
}

// ------------------------------------------------------------------ compass

class _CompassMock extends StatefulWidget {
  const _CompassMock();

  @override
  State<_CompassMock> createState() => _CompassMockState();
}

class _CompassMockState extends State<_CompassMock> {
  final _rng = math.Random(3);
  late Map<_Family, _Card> _points = _draw();
  _Family? _taken;

  Map<_Family, _Card> _draw() => {
    for (final f in _Family.values) f: (_library.where((c) => c.family == f).toList()..shuffle(_rng)).first,
  };

  Widget _point(_Family f, double width) {
    final card = _points[f]!;
    final taken = _taken == f;
    return GestureDetector(
      key: ValueKey('lab-compass-${f.name}'),
      onTap: () => setState(() => _taken = f),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        width: width,
        padding: EdgeInsets.all(width < 150 ? 10 : 12),
        decoration: BoxDecoration(
          color: taken ? f.color.withValues(alpha: .12) : Paper.card,
          border: Border.all(color: taken ? f.color : Paper.border, width: taken ? 1.6 : 1),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(f.icon, size: 14, color: f.color),
                const SizedBox(width: 6),
                Flexible(
                  child: Text(f.label.toUpperCase(), style: mono(9.5, color: f.color)),
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(card.text, style: sans(width < 150 ? 12.5 : 13, height: 1.35)),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, box) {
        final narrow = box.maxWidth < 640;
        // on a phone the two side cards share the row; on a desktop they flank the centre
        final side = narrow ? (box.maxWidth - 48 - 52 - 16) / 2 : 190.0;
        // on a phone: a small rose between the side cards, so the whole
        // compass fits one screen; on a desktop: the topic in a circle
        final center = narrow
            ? Container(
                width: 52,
                height: 52,
                decoration: BoxDecoration(
                  color: Paper.surface,
                  shape: BoxShape.circle,
                  border: Border.all(color: Paper.borderStrong),
                ),
                child: const Icon(Icons.explore_outlined, size: 24, color: Paper.muted),
              )
            : Container(
                width: 280,
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: Paper.surface,
                  shape: BoxShape.circle,
                  border: Border.all(color: Paper.borderStrong),
                ),
                child: AspectRatio(
                  aspectRatio: 1,
                  child: Center(
                    child: Text(
                      'the derivative\n— rate of change at an instant',
                      textAlign: TextAlign.center,
                      style: serif(15, height: 1.4),
                    ),
                  ),
                ),
              );
        return ListView(
          padding: const EdgeInsets.fromLTRB(24, 24, 24, 48),
          children: [
            const _AnswerBubble(),
            const SizedBox(height: 24),
            Center(
              child: Column(
                children: [
                  _point(_Family.deeper, side),
                  const SizedBox(height: 14),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      _point(_Family.real, side),
                      SizedBox(width: narrow ? 8 : 20),
                      center,
                      SizedBox(width: narrow ? 8 : 20),
                      _point(_Family.wider, side),
                    ],
                  ),
                  const SizedBox(height: 14),
                  _point(_Family.simpler, side),
                ],
              ),
            ),
            const SizedBox(height: 12),
            Center(
              child: TextButton.icon(
                key: const ValueKey('lab-compass-more'),
                onPressed: () => setState(() {
                  _points = _draw();
                  _taken = null;
                }),
                icon: const Icon(Icons.refresh_rounded, size: 14, color: Paper.muted),
                label: Text('other directions', style: sans(12, color: Paper.muted)),
              ),
            ),
            const SizedBox(height: 16),
            const _Note(
              'One card for each way out of an answer: up goes deeper, down makes it simpler, left makes it '
              'real, right goes wider. The pick is a direction — exactly what the family-level style reads.',
            ),
          ],
        );
      },
    );
  }
}

// ------------------------------------------------------------ constellation

class _ConstellationMock extends StatefulWidget {
  const _ConstellationMock();

  @override
  State<_ConstellationMock> createState() => _ConstellationMockState();
}

class _ConstellationMockState extends State<_ConstellationMock> {
  _Card? _focus;
  // the answer's own pool: the stars that are lit and labelled
  final Set<String> _lit = {'example', 'use', 'why', 'deeper', 'intuition', 'visualise', 'next', 'connect'};

  /// Which side each label goes on: placed one by one (the tapped star
  /// first), each on the side whose box overlaps least with every star and
  /// every label already placed -- and never off the sky.
  bool _labelRight(_Card c, Size size) {
    final key = '${size.width}x${size.height}/${_focus?.type}';
    if (_sidesFor != key) {
      _sides = {};
      final labelled = _library.where((o) => _lit.contains(o.type) || o == _focus).toList()
        ..sort((a, b) => (b == _focus ? 1 : 0) - (a == _focus ? 1 : 0));
      final placed = <Rect>[];
      Rect box(_Card o, bool right) {
        final p = _at(o, size);
        final w = o.type.length * (o == _focus ? 7.0 : 6.2) + 6;
        return right ? Rect.fromLTWH(p.dx + 10, p.dy - 8, w, 16) : Rect.fromLTWH(p.dx - 10 - w, p.dy - 8, w, 16);
      }

      double cost(Rect r) {
        if (r.left < 4 || r.right > size.width - 4) return 1e9;
        var total = 0.0;
        for (final q in placed) {
          final i = r.intersect(q);
          if (i.width > 0 && i.height > 0) total += i.width * i.height;
        }
        for (final o in _library) {
          final star = Rect.fromCircle(center: _at(o, size), radius: 8);
          final i = r.intersect(star);
          if (i.width > 0 && i.height > 0) total += i.width * i.height;
        }
        return total;
      }

      for (final o in labelled) {
        final right = cost(box(o, true)) <= cost(box(o, false));
        _sides[o.type] = right;
        placed.add(box(o, right));
      }
      _sidesFor = key;
    }
    return _sides[c.type] ?? true;
  }

  String? _sidesFor;
  Map<String, bool> _sides = {};

  Size? _laidFor;
  Map<String, Offset> _laid = {};

  /// Where each star sits: concrete to the left, abstract to the right;
  /// deeper up, simpler down. Types that share a spot (e.g. "why" and
  /// "deeper") are nudged apart until no two stars are closer than a
  /// finger's width, and kept clear of the edges.
  Offset _at(_Card c, Size size) {
    if (_laidFor != size) {
      const mx = 56.0, my = 30.0, minGap = 30.0;
      final pos = {
        for (final k in _library)
          k.type: Offset(
            (1 - k.concrete) / 2 * (size.width - 2 * mx) + mx + k.breadth * 10,
            (1 - k.depth) / 2 * (size.height - 2 * my) + my + k.breadth * 6,
          ),
      };
      final keys = pos.keys.toList();
      for (var round = 0; round < 60; round++) {
        for (var i = 0; i < keys.length; i++) {
          for (var j = i + 1; j < keys.length; j++) {
            final a = pos[keys[i]]!, b = pos[keys[j]]!;
            var d = b - a;
            if (d.distance == 0) d = Offset(1, (i - j).toDouble());
            if (d.distance < minGap) {
              final push = d / d.distance * ((minGap - d.distance) / 2);
              pos[keys[i]] = a - push;
              pos[keys[j]] = b + push;
            }
          }
        }
        for (final k in keys) {
          final p = pos[k]!;
          pos[k] = Offset(p.dx.clamp(mx, size.width - mx), p.dy.clamp(my, size.height - my));
        }
      }
      _laid = pos;
      _laidFor = size;
    }
    return _laid[c.type]!;
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 24, 24, 48),
      children: [
        const _AnswerBubble(),
        const SizedBox(height: 18),
        Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 760),
            child: AspectRatio(
              aspectRatio: 1.35,
              child: LayoutBuilder(
                builder: (context, box) {
                  final size = Size(box.maxWidth, box.maxHeight);
                  return Container(
                    key: const ValueKey('lab-constellation'),
                    decoration: BoxDecoration(color: const Color(0xFF1F1D2B), borderRadius: BorderRadius.circular(16)),
                    child: Stack(
                      children: [
                        Positioned.fill(child: CustomPaint(painter: _SkyPainter(_library, _at, _lit, _focus))),
                        for (final (label, alignment) in const [
                          ('deeper', Alignment.topCenter),
                          ('simpler', Alignment.bottomCenter),
                          ('concrete', Alignment.centerLeft),
                          ('abstract', Alignment.centerRight),
                        ])
                          Align(
                            alignment: alignment,
                            child: Padding(
                              padding: const EdgeInsets.all(8),
                              child: Text(
                                label.toUpperCase(),
                                style: mono(9, color: Colors.white.withValues(alpha: .35)),
                              ),
                            ),
                          ),
                        for (final c in _library)
                          Positioned(
                            left: _at(c, size).dx - 22,
                            top: _at(c, size).dy - 22,
                            child: GestureDetector(
                              key: ValueKey('lab-star-${c.type}'),
                              behavior: HitTestBehavior.opaque,
                              onTap: () => setState(() => _focus = c),
                              child: const SizedBox(width: 44, height: 44),
                            ),
                          ),
                        // short labels, on the side with room; the full card text
                        // shows under the sky when a star is tapped
                        for (final c in _library.where((c) => _lit.contains(c.type) || c == _focus))
                          Positioned(
                            left: _labelRight(c, size) ? _at(c, size).dx + 10 : null,
                            right: _labelRight(c, size) ? null : size.width - _at(c, size).dx + 10,
                            top: _at(c, size).dy - 8,
                            child: IgnorePointer(
                              child: ConstrainedBox(
                                constraints: const BoxConstraints(maxWidth: 110),
                                child: Text(
                                  c.type.replaceAll('_', ' '),
                                  style: sans(
                                    c == _focus ? 12 : 10.5,
                                    color: Colors.white.withValues(alpha: c == _focus ? 1 : .7),
                                    weight: c == _focus ? FontWeight.w600 : FontWeight.w400,
                                  ),
                                ),
                              ),
                            ),
                          ),
                      ],
                    ),
                  );
                },
              ),
            ),
          ),
        ),
        const SizedBox(height: 12),
        if (_focus != null)
          Row(
            children: [
              Expanded(child: Text('“${_focus!.text}” — ${_focus!.family.label.toLowerCase()}', style: sans(13.5))),
              FilledButton(
                key: const ValueKey('lab-star-go'),
                style: FilledButton.styleFrom(backgroundColor: Paper.accent),
                onPressed: () {},
                child: const Text('Go there'),
              ),
            ],
          )
        else
          const _Note('Tap a star.'),
        const SizedBox(height: 16),
        const _Note(
          'Every kind of next step as a star, placed by where it sits — concrete to abstract, simpler '
          'to deeper. This answer’s own cards are lit; the rest wait in the dark. A learner’s picks '
          'would trace a region of the sky over time: their thinking style, drawn.',
        ),
      ],
    );
  }
}

class _SkyPainter extends CustomPainter {
  _SkyPainter(this.cards, this.at, this.lit, this.focus);
  final List<_Card> cards;
  final Offset Function(_Card, Size) at;
  final Set<String> lit;
  final _Card? focus;

  @override
  void paint(Canvas canvas, Size size) {
    final dim = Paint()..color = Colors.white.withValues(alpha: .08);
    for (var i = 0; i < 60; i++) {
      final r = math.Random(i);
      canvas.drawCircle(Offset(r.nextDouble() * size.width, r.nextDouble() * size.height), .8, dim);
    }
    final centre = Offset(size.width / 2, size.height / 2);
    canvas.drawCircle(centre, 5, Paint()..color = Colors.white.withValues(alpha: .9));
    for (final c in cards) {
      final p = at(c, size);
      final on = lit.contains(c.type) || c == focus;
      if (on) {
        canvas.drawLine(
          centre,
          p,
          Paint()
            ..color = c.family.color.withValues(alpha: c == focus ? .8 : .25)
            ..strokeWidth = c == focus ? 1.6 : 1,
        );
      }
      canvas.drawCircle(p, on ? 9 : 4, Paint()..color = c.family.color.withValues(alpha: on ? .25 : .12));
      canvas.drawCircle(p, on ? 4 : 2.2, Paint()..color = on ? c.family.color : Colors.white.withValues(alpha: .35));
    }
  }

  @override
  bool shouldRepaint(_SkyPainter old) => old.focus != focus;
}
