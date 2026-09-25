import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';
import 'engine.dart';
import 'script.dart';

/// Slime colours: a light, glossy green (the shape follows the slime in
/// "That Time I Got Reincarnated as a Slime" -- round on top, bulging out
/// where it sits -- in the user's chosen colour).
class Slime {
  static const top = Color(0xFFD2F2B8);
  static const mid = Color(0xFFB4E596);
  static const bottom = Color(0xFF93D374);
  static const outline = Color(0xFF6BB556);
  static const shine = Color(0xE6FFFFFF);
  static const eyeInk = Color(0xFF26301F);
  static const mouth = Color(0xFF7A2E24);
  static const tongue = Color(0xFFE77C6B);
  static const blush = Color(0x66F08A7A);
  static const sweat = Color(0xFF8CC7EA);
}

/// Named colours a script may ask for (never raw hex -- see SpawnAction).
Color propColor(String? name, Color fallback) => switch (name) {
      'accent' => Paper.accent,
      'olive' => Paper.olive,
      'warn' || 'yellow' => const Color(0xFFE2B33C),
      'ink' => Paper.ink,
      'blue' => const Color(0xFF4F7CAC),
      'pink' => const Color(0xFFE0708A),
      'red' => Paper.danger,
      'green' => Slime.bottom,
      _ => fallback,
    };

/// Draws the ground, the props and the blob for one frame of [engine].
/// Bubbles and choices are widgets on top (stage_view.dart), not paint.
class StagePainter extends CustomPainter {
  StagePainter(this.engine) : super(repaint: engine);
  final StageEngine engine;

  @override
  void paint(Canvas canvas, Size size) {
    final t = engine.time;
    final groundY = kGroundY * size.height;

    canvas.drawLine(
      Offset(16, groundY),
      Offset(size.width - 16, groundY),
      Paint()
        ..color = Paper.borderStrong
        ..strokeWidth = 1.5
        ..strokeCap = StrokeCap.round,
    );

    for (final p in engine.props.values) {
      _paintProp(canvas, size, p, t);
    }
    _paintBlob(canvas, size, t);
    _paintParticles(canvas, size, t);
  }

  // ---------------------------------------------------------------- props

  void _paintProp(Canvas canvas, Size size, StageProp p, double t) {
    final at = engine.propAt(p);
    var o = Offset(at.dx * size.width, at.dy * size.height);
    if (t < p.shakeUntil) o += Offset(math.sin(t * 70) * 2.2, 0);

    final age = t - p.bornAt;
    var scale = Curves.elasticOut.transform((age / 0.55).clamp(0.0, 1.0));
    var alpha = 1.0;
    if (p.diedAt != null) {
      final k = ((t - p.diedAt!) / 0.45).clamp(0.0, 1.0);
      _poof(canvas, o, engine.propHalf(p), k);
      scale *= 1 - k;
      alpha = 1 - k;
    }
    if (scale <= 0.001) return;

    final half = engine.propHalf(p);
    final span = _isSpan(p.kind);
    final grown = p.scale.at(t);
    final turn = p.spin.at(t);
    canvas.save();
    // Props stand on their base point: scale from there, so they "grow" up
    // out of the ground rather than from their middle. Spans (arrows, lines,
    // waves, paths) draw themselves out instead of popping.
    canvas.translate(o.dx, o.dy);
    if (!span) canvas.scale(scale * grown);
    if (turn != 0) {
      final pivot = span ? 0.0 : -_propHeight(p, size) / 2;
      canvas.translate(0, pivot);
      canvas.rotate(turn * math.pi * 2);
      canvas.translate(0, -pivot);
    }
    final ink = propColor(p.color, Paper.ink).withValues(alpha: alpha);

    switch (p.kind) {
      case PropKind.box:
        final r = Rect.fromLTWH(-half, -half * 2, half * 2, half * 2);
        final rr = RRect.fromRectAndRadius(r, Radius.circular(half * 0.18));
        canvas.drawRRect(rr, Paint()..color = propColor(p.color, const Color(0xFFD9B98A)).withValues(alpha: alpha));
        final edge = Paint()
          ..color = const Color(0xFF9C7A4A).withValues(alpha: alpha)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2;
        canvas.drawRRect(rr, edge);
        canvas.drawLine(r.topLeft + Offset(half * 0.25, half * 0.25), r.bottomRight - Offset(half * 0.25, half * 0.25),
            edge..strokeWidth = 1.2);
        if (p.label != null) _label(canvas, p.label!, Offset(0, -half), half * 0.55, Paper.ink, alpha, box: true);

      case PropKind.ball:
        final c = Offset(0, -half);
        canvas.drawCircle(c, half, Paint()..color = propColor(p.color, Paper.accent).withValues(alpha: alpha));
        canvas.drawCircle(c + Offset(-half * 0.35, -half * 0.35), half * 0.25,
            Paint()..color = Colors.white.withValues(alpha: 0.5 * alpha));
        if (p.label != null) _label(canvas, p.label!, Offset(0, -half * 2.6), 13, Paper.ink, alpha);

      case PropKind.arrow:
        final grow = _drawn(p, age, scale) * grown;
        final d = p.headDelta ?? const Offset(0.15, 0);
        final head = Offset(d.dx * size.width, d.dy * size.height) * grow;
        final col = propColor(p.color, Paper.accent).withValues(alpha: alpha);
        final paint = Paint()
          ..color = col
          ..strokeWidth = 4
          ..strokeCap = StrokeCap.round;
        canvas.drawLine(Offset.zero, head, paint);
        if (head.distance > 6) {
          final ang = math.atan2(head.dy, head.dx);
          const wing = 0.5;
          final len = 12.0;
          final tip = Path()
            ..moveTo(head.dx, head.dy)
            ..lineTo(head.dx - len * math.cos(ang - wing), head.dy - len * math.sin(ang - wing))
            ..lineTo(head.dx - len * math.cos(ang + wing), head.dy - len * math.sin(ang + wing))
            ..close();
          canvas.drawPath(tip, Paint()..color = col);
        }
        if (p.label != null && grow > 0.6) {
          _label(canvas, p.label!, head / 2 + const Offset(0, -16), 14, col, alpha, bold: true);
        }

      case PropKind.text:
        _label(canvas, p.label ?? '', Offset.zero, 15 * p.size, propColor(p.color, Paper.ink), alpha, bold: true);

      case PropKind.star:
        canvas.rotate(math.sin(t * 2) * 0.2);
        canvas.drawPath(_star(half), Paint()..color = propColor(p.color, const Color(0xFFE2B33C)).withValues(alpha: alpha));
        if (p.label != null) _label(canvas, p.label!, Offset(0, half * 1.6), 12, Paper.ink, alpha);

      case PropKind.heart:
        canvas.drawPath(_heart(half), Paint()..color = propColor(p.color, const Color(0xFFE0708A)).withValues(alpha: alpha));

      case PropKind.cloud:
        final fill = Paint()..color = Colors.white.withValues(alpha: alpha);
        final line = Paint()
          ..color = Paper.borderStrong.withValues(alpha: alpha)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.5;
        final w = half * 1.8;
        final puffs = [
          Offset(-w * 0.55, -half * 0.3),
          Offset(0, -half * 0.75),
          Offset(w * 0.55, -half * 0.3),
          Offset(-w * 0.2, 0),
          Offset(w * 0.25, 0),
        ];
        for (final c in puffs) {
          canvas.drawCircle(c, half * 0.65, line);
        }
        for (final c in puffs) {
          canvas.drawCircle(c, half * 0.65, fill);
        }
        if (p.label != null) _label(canvas, p.label!, Offset(0, -half * 0.25), 12, Paper.ink, alpha);

      case PropKind.emoji:
        final tp = TextPainter(
          text: TextSpan(text: p.label ?? '❓', style: TextStyle(fontSize: half * 2.1, height: 1)),
          textDirection: TextDirection.ltr,
        )..layout();
        tp.paint(canvas, Offset(-tp.width / 2, -tp.height));

      case PropKind.circle:
        final rad = (p.w ?? 0.12) * size.height / 2 * p.size;
        final c = Offset(0, -rad);
        final col = propColor(p.color, const Color(0xFF4F7CAC));
        canvas.drawCircle(c, rad, Paint()..color = col.withValues(alpha: 0.85 * alpha));
        canvas.drawCircle(c, rad, _outline(col, alpha));
        if (p.label != null) _label(canvas, p.label!, c, rad * 0.7, Colors.white, alpha, bold: true);

      case PropKind.rect || PropKind.triangle:
        final bw = (p.w ?? 0.15) * size.height * p.size;
        final bh = (p.h ?? p.w ?? 0.15) * size.height * p.size;
        final col = propColor(p.color, Paper.accent);
        final shape = p.kind == PropKind.rect
            ? (Path()..addRRect(RRect.fromRectAndRadius(Rect.fromLTWH(-bw / 2, -bh, bw, bh), const Radius.circular(4))))
            : (Path()
              ..moveTo(0, -bh)
              ..lineTo(bw / 2, 0)
              ..lineTo(-bw / 2, 0)
              ..close());
        canvas.drawPath(shape, Paint()..color = col.withValues(alpha: 0.85 * alpha));
        canvas.drawPath(shape, _outline(col, alpha));
        if (p.label != null) {
          _label(canvas, p.label!, Offset(0, p.kind == PropKind.rect ? -bh / 2 : -bh * 0.35),
              math.min(bw, bh) * 0.3, Colors.white, alpha, bold: true);
        }

      case PropKind.line:
        final grow = _drawn(p, age, scale) * grown;
        final d = p.headDelta ?? const Offset(0.2, 0);
        final end = Offset(d.dx * size.width, d.dy * size.height) * grow;
        canvas.drawLine(Offset.zero, end, _stroke(ink, 3));
        if (p.label != null && grow > 0.6) _label(canvas, p.label!, end / 2 + const Offset(0, -14), 13, ink, alpha);

      case PropKind.wave:
        final grow = _drawn(p, age, scale) * grown;
        final d = p.headDelta ?? const Offset(0.3, 0);
        final len = d.dx * size.width;
        final amp = p.amp * size.height;
        final col = propColor(p.color, const Color(0xFF4F7CAC)).withValues(alpha: alpha);
        final path = Path()..moveTo(0, 0);
        const steps = 80;
        for (var i = 1; i <= steps * grow; i++) {
          final x = len * i / steps;
          path.lineTo(x, d.dy * size.height * i / steps - amp * math.sin(2 * math.pi * p.cycles * i / steps - t * 5));
        }
        canvas.drawPath(path, _stroke(col, 3)..style = PaintingStyle.stroke);
        if (p.label != null) _label(canvas, p.label!, Offset(len / 2, -amp - 14), 13, col, alpha);

      case PropKind.path:
        if (p.points.isEmpty) break;
        final grow = _drawn(p, age, scale) * grown;
        final pts = [Offset.zero, for (final q in p.points) Offset(q.dx * size.width, q.dy * size.height)];
        final shown = (pts.length - 1) * grow;
        final path = Path()..moveTo(0, 0);
        for (var i = 1; i < pts.length; i++) {
          if (i <= shown) {
            path.lineTo(pts[i].dx, pts[i].dy);
          } else {
            final part = Offset.lerp(pts[i - 1], pts[i], shown - (i - 1))!;
            path.lineTo(part.dx, part.dy);
            break;
          }
        }
        final col = propColor(p.color, Paper.accent).withValues(alpha: alpha);
        canvas.drawPath(path, _stroke(col, 3)..style = PaintingStyle.stroke);
        if (p.label != null && grow >= 1) _label(canvas, p.label!, pts.last + const Offset(0, -14), 13, col, alpha);
    }
    canvas.restore();
  }

  bool _isSpan(PropKind k) => k == PropKind.arrow || k == PropKind.line || k == PropKind.wave || k == PropKind.path;

  /// 0..1: how much of a span has drawn itself out (and undraws as it poofs).
  double _drawn(StageProp p, double age, double poofScale) =>
      Curves.easeOutCubic.transform((age / 0.45).clamp(0.0, 1.0)) * (p.diedAt == null ? 1 : poofScale);

  double _propHeight(StageProp p, Size size) => switch (p.kind) {
        PropKind.rect || PropKind.triangle => (p.h ?? p.w ?? 0.15) * size.height * p.size,
        PropKind.circle => (p.w ?? 0.12) * size.height * p.size,
        _ => engine.propHalf(p) * 2,
      };

  Paint _stroke(Color c, double w) => Paint()
    ..color = c
    ..strokeWidth = w
    ..strokeCap = StrokeCap.round
    ..strokeJoin = StrokeJoin.round;

  Paint _outline(Color c, double alpha) => Paint()
    ..color = Color.lerp(c, Colors.black, 0.25)!.withValues(alpha: alpha)
    ..style = PaintingStyle.stroke
    ..strokeWidth = 2;

  // ------------------------------------------------------------ particles

  static const _confetti = [Color(0xFFE2B33C), Paper.accent, Color(0xFF4F7CAC), Color(0xFF93D374), Color(0xFFE0708A)];

  void _paintParticles(Canvas canvas, Size size, double t) {
    for (final q in engine.particles) {
      final age = t - q.born;
      if (age < 0) continue;
      final k = (age / q.life).clamp(0.0, 1.0);
      final fade = 1 - k;
      final o = Offset(q.pos.dx * size.width, q.pos.dy * size.height);
      switch (q.kind) {
        case EffectKind.confetti:
          canvas.save();
          canvas.translate(o.dx, o.dy);
          canvas.rotate(age * 8 + q.hue * 6);
          canvas.drawRect(Rect.fromCenter(center: Offset.zero, width: q.size, height: q.size * 0.5),
              Paint()..color = _confetti[(q.hue * _confetti.length).floor() % _confetti.length].withValues(alpha: fade));
          canvas.restore();
        case EffectKind.sparks:
          final dir = q.vel.distance == 0 ? Offset.zero : q.vel / q.vel.distance;
          final col = Color.lerp(const Color(0xFFFFE08A), Paper.accent, q.hue)!.withValues(alpha: fade);
          canvas.drawLine(o, o - Offset(dir.dx * size.width, dir.dy * size.height) * 0.02 * fade, _stroke(col, 2.5));
        case EffectKind.stars:
          canvas.save();
          canvas.translate(o.dx, o.dy + q.size);
          canvas.rotate(age * 3);
          canvas.drawPath(_star(q.size), Paint()..color = const Color(0xFFE2B33C).withValues(alpha: fade));
          canvas.restore();
        case EffectKind.splash:
          canvas.drawCircle(o, q.size, Paint()..color = const Color(0xFF6FB6E0).withValues(alpha: fade));
        case EffectKind.smoke:
          canvas.drawCircle(o, q.size * (1 + k * 1.5), Paint()..color = const Color(0xFF9A948A).withValues(alpha: 0.35 * fade));
        case EffectKind.fire:
          final col = Color.lerp(const Color(0xFFFFD35A), const Color(0xFFD9481F), k)!;
          canvas.drawCircle(o, q.size * (1 - k * 0.7), Paint()..color = col.withValues(alpha: 0.85 * fade));
        case EffectKind.rain:
          canvas.drawLine(o, o + Offset(1, q.size), _stroke(const Color(0xFF6FA8D8).withValues(alpha: 0.8), 1.6));
        case EffectKind.snow:
          canvas.drawCircle(o, q.size, Paint()..color = Colors.white.withValues(alpha: fade));
          canvas.drawCircle(o, q.size, _stroke(const Color(0xFFB9C8D6).withValues(alpha: fade), 0.8)..style = PaintingStyle.stroke);
        case EffectKind.bubbles:
          canvas.drawCircle(o, q.size, _stroke(const Color(0xFF7EC4E6).withValues(alpha: fade), 1.5)..style = PaintingStyle.stroke);
          canvas.drawCircle(o - Offset(q.size * 0.35, q.size * 0.35), q.size * 0.2,
              Paint()..color = Colors.white.withValues(alpha: fade));
        case EffectKind.hearts:
          canvas.save();
          canvas.translate(o.dx, o.dy + q.size);
          canvas.drawPath(_heart(q.size), Paint()..color = const Color(0xFFE0708A).withValues(alpha: fade));
          canvas.restore();
        case EffectKind.zzz:
          _label(canvas, 'z', o, q.size, Paper.muted, fade, bold: true);
      }
    }
  }

  void _poof(Canvas canvas, Offset o, double half, double k) {
    if (k >= 1) return;
    final paint = Paint()..color = Paper.faint.withValues(alpha: 0.5 * (1 - k));
    for (var i = 0; i < 7; i++) {
      final a = i / 7 * math.pi * 2;
      final c = o + Offset(math.cos(a), math.sin(a) * 0.6 - 0.6) * (half * (0.6 + 1.4 * k));
      canvas.drawCircle(c, half * 0.28 * (1 - k * 0.5), paint);
    }
  }

  Path _star(double r) {
    final path = Path();
    for (var i = 0; i < 10; i++) {
      final rad = i.isEven ? r : r * 0.45;
      final a = -math.pi / 2 + i * math.pi / 5;
      final pt = Offset(math.cos(a) * rad, math.sin(a) * rad - r);
      i == 0 ? path.moveTo(pt.dx, pt.dy) : path.lineTo(pt.dx, pt.dy);
    }
    return path..close();
  }

  Path _heart(double r) {
    final path = Path()
      ..moveTo(0, -r * 0.2)
      ..cubicTo(-r * 1.2, -r * 1.3, -r * 1.3, r * 0.1, 0, r * 0.9)
      ..cubicTo(r * 1.3, r * 0.1, r * 1.2, -r * 1.3, 0, -r * 0.2)
      ..close();
    return path.shift(Offset(0, -r));
  }

  void _label(Canvas canvas, String text, Offset center, double fontSize, Color color, double alpha,
      {bool bold = false, bool box = false}) {
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          fontSize: fontSize.clamp(9.0, 22.0),
          color: color.withValues(alpha: alpha),
          fontWeight: bold || box ? FontWeight.w700 : FontWeight.w500,
        ),
      ),
      textDirection: TextDirection.ltr,
      textAlign: TextAlign.center,
    )..layout(maxWidth: 160);
    tp.paint(canvas, center - Offset(tp.width / 2, tp.height / 2));
  }

  // ----------------------------------------------------------------- blob

  void _paintBlob(Canvas canvas, Size size, double t) {
    final e = engine;
    final pos = e.blobPos;
    final ground = e.blob.base(t);
    final r = e.blobRadius;
    final squash = e.blobSquash;
    final breathe = math.sin(t * 2.6) * 0.025;

    final cx = pos.dx * size.width;
    final base = pos.dy * size.height;
    final h = e.blobHeight;
    final w = r * 1.08 * (1 - (squash + breathe) * 0.45);
    final top = base - h;

    // Shadow on the ground, shrinking as the blob leaves it.
    final lift = (ground.dy - pos.dy) * size.height;
    final shadowW = w * 2.1 * (1 - (lift / (r * 3)).clamp(0.0, 0.6));
    canvas.drawOval(
      Rect.fromCenter(center: Offset(cx, ground.dy * size.height + 2), width: shadowW, height: r * 0.24),
      Paint()..color = const Color(0x22000000),
    );

    // Body, as a profile from head (u = 0) to where it sits (u = 1): an
    // ordinary round dome on top, widest low down at [belly], then the
    // bulge rolling under onto a flat seat. Squashing pushes the bulge out
    // (the weight settles into it); the dome keeps a slow jelly wobble; the
    // whole thing shears when leaning into something.
    const belly = 0.8;
    double halfWidth(double u) {
      double hw;
      if (u <= belly) {
        // A plain round head that swells as it goes down: the weight of the
        // jelly settling toward the ground.
        final k = u / belly;
        final v = 1 - k;
        hw = math.sqrt(math.max(0, 1 - v * v)) * (0.86 + 0.3 * k * k * k);
        hw *= 1 + 0.025 * math.sin(t * 3.1 + u * 7) * u;
      } else {
        // The bulge rolling under onto the flat seat.
        final v = (u - belly) / (1 - belly);
        hw = 1.16 * math.sqrt(math.max(0, 1 - 0.55 * v * v));
      }
      return w * hw * (1 - squash * 0.9 * u * u);
    }

    double shear(double u) => e.lean * h * (1 - u) * 0.45;

    const n = 26;
    final right = <Offset>[
      for (var i = 0; i <= n; i++) Offset(cx + halfWidth(i / n) + shear(i / n), top + h * i / n),
    ];
    final left = <Offset>[
      for (var i = n; i >= 0; i--) Offset(cx - halfWidth(i / n) + shear(i / n), top + h * i / n),
    ];
    final pts = [...right, ...left.skip(1).take(left.length - 2)];
    final body = Path()..moveTo((pts[0].dx + pts[1].dx) / 2, (pts[0].dy + pts[1].dy) / 2);
    for (var i = 1; i <= pts.length; i++) {
      final p = pts[i % pts.length], q = pts[(i + 1) % pts.length];
      body.quadraticBezierTo(p.dx, p.dy, (p.dx + q.dx) / 2, (p.dy + q.dy) / 2);
    }
    body.close();

    final bounds = Rect.fromLTRB(cx - w * 1.3, top, cx + w * 1.3, base);
    canvas.drawPath(
      body,
      Paint()
        ..shader = const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [Slime.top, Slime.mid, Slime.bottom],
          stops: [0, 0.5, 1],
        ).createShader(bounds),
    );
    // A soft inner glow low in the bulge: reads as jelly volume.
    canvas.save();
    canvas.clipPath(body);
    canvas.drawOval(
      Rect.fromCenter(center: Offset(cx + shear(0.85), base - h * 0.12), width: w * 1.5, height: h * 0.28),
      Paint()..color = Colors.white.withValues(alpha: 0.18),
    );
    canvas.restore();
    canvas.drawPath(
      body,
      Paint()
        ..color = Slime.outline
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.2,
    );
    // The gloss: a big highlight up on the dome, a small dot beside it.
    canvas.save();
    canvas.translate(cx - w * 0.38 + shear(0.25), top + h * 0.24);
    canvas.rotate(-0.6);
    canvas.drawOval(Rect.fromCenter(center: Offset.zero, width: w * 0.5, height: h * 0.14), Paint()..color = Slime.shine);
    canvas.restore();
    canvas.drawCircle(Offset(cx - w * 0.1 + shear(0.15), top + h * 0.13), r * 0.06, Paint()..color = Slime.shine);

    _paintFace(canvas, Offset(cx + shear(0.55), top + h * 0.56), r, w * 0.85, h, t, pos.dx);
    _paintExtras(canvas, Offset(cx + shear(0.3), top + h / 2), r, w * 0.85, h, t);
  }

  void _paintFace(Canvas canvas, Offset face, double r, double w, double h, double t, double blobX) {
    final e = engine;
    final mood = e.mood;
    final eyeGap = w * 0.4;
    final eyeR = r * 0.22;
    final blink = e.blink;

    // Pupils follow whatever the blob is looking at.
    var look = Offset.zero;
    final lx = e.lookX;
    if (lx != null) look = Offset(((lx - blobX) * 6).clamp(-1.0, 1.0), 0);
    if (mood == Mood.thinking) look = const Offset(0.7, -0.8);
    if (mood == Mood.sad) look = Offset(look.dx, 0.6);

    final ink = Paint()..color = Slime.eyeInk;
    final white = Paint()..color = Colors.white;
    final line = Paint()
      ..color = Slime.eyeInk
      ..style = PaintingStyle.stroke
      ..strokeWidth = math.max(2, r * 0.07)
      ..strokeCap = StrokeCap.round;

    for (final side in const [-1.0, 1.0]) {
      final c = face + Offset(side * eyeGap, 0);
      switch (mood) {
        case Mood.happy:
          // ^ ^
          canvas.drawArc(Rect.fromCircle(center: c + Offset(0, eyeR * 0.4), radius: eyeR * 0.8), math.pi * 1.15,
              math.pi * 0.7, false, line);
        case Mood.strain:
          // > <
          final d = side * eyeR * 0.7;
          canvas.drawLine(c + Offset(-d, -eyeR * 0.6), c + Offset(d * 0.6, 0), line);
          canvas.drawLine(c + Offset(d * 0.6, 0), c + Offset(-d, eyeR * 0.6), line);
        default:
          var er = eyeR;
          var squint = 1.0;
          if (mood == Mood.surprised || mood == Mood.excited) er *= 1.2;
          if (mood == Mood.confused && side > 0) squint = 0.55;
          final open = (1 - blink) * squint;
          final eyeRect = Rect.fromCenter(center: c, width: er * 2, height: er * 2.3 * math.max(open, 0.08));
          if (open < 0.15) {
            canvas.drawLine(c - Offset(er, 0), c + Offset(er, 0), line);
          } else {
            canvas.drawOval(eyeRect, white);
            canvas.drawOval(eyeRect, line..strokeWidth = math.max(1.5, r * 0.045));
            final pr = mood == Mood.surprised ? er * 0.35 : er * 0.58;
            final pc = c + Offset(look.dx * er * 0.4, look.dy * er * 0.45 * open);
            canvas.save();
            canvas.clipPath(Path()..addOval(eyeRect));
            canvas.drawCircle(pc, pr, ink);
            canvas.drawCircle(pc + Offset(-pr * 0.35, -pr * 0.4), pr * 0.32, white);
            if (mood == Mood.excited) canvas.drawCircle(pc + Offset(pr * 0.3, pr * 0.3), pr * 0.18, white);
            canvas.restore();
            if (mood == Mood.proud) {
              // Smug half-lid.
              canvas.drawRect(
                Rect.fromLTRB(eyeRect.left - 1, eyeRect.top - 1, eyeRect.right + 1, c.dy - er * 0.05),
                Paint()..color = Slime.mid,
              );
              canvas.drawLine(Offset(eyeRect.left, c.dy - er * 0.05), Offset(eyeRect.right, c.dy - er * 0.05), line);
            }
          }
      }

      // Brows.
      final browY = face.dy - eyeR * 1.7;
      final bx = c.dx;
      final bw = eyeR * 0.9;
      line.strokeWidth = math.max(2, r * 0.065);
      switch (mood) {
        case Mood.sad:
          canvas.drawLine(Offset(bx - side * bw, browY + eyeR * 0.1), Offset(bx + side * bw, browY - eyeR * 0.4), line);
        case Mood.strain:
          canvas.drawLine(Offset(bx - side * bw, browY - eyeR * 0.3), Offset(bx + side * bw * 0.4, browY + eyeR * 0.3), line);
        case Mood.confused || Mood.thinking:
          if (side < 0) {
            canvas.drawArc(Rect.fromCircle(center: Offset(bx, browY - eyeR * 0.1), radius: bw), math.pi * 1.2,
                math.pi * 0.6, false, line);
          } else {
            canvas.drawLine(Offset(bx - bw, browY + eyeR * 0.35), Offset(bx + bw, browY + eyeR * 0.2), line);
          }
        case Mood.surprised:
          canvas.drawArc(Rect.fromCircle(center: Offset(bx, browY - eyeR * 0.4), radius: bw), math.pi * 1.2,
              math.pi * 0.6, false, line);
        default:
          break;
      }
    }

    // Blush.
    if (mood == Mood.happy || mood == Mood.excited || mood == Mood.proud) {
      for (final side in const [-1.0, 1.0]) {
        canvas.drawOval(
          Rect.fromCenter(center: face + Offset(side * eyeGap * 1.45, eyeR * 1.3), width: eyeR * 1.3, height: eyeR * 0.7),
          Paint()..color = Slime.blush,
        );
      }
    }

    _paintMouth(canvas, face + Offset(0, r * 0.42), r, t);
  }

  void _paintMouth(Canvas canvas, Offset m, double r, double t) {
    final e = engine;
    final mw = r * 0.34;
    final line = Paint()
      ..color = Slime.mouth
      ..style = PaintingStyle.stroke
      ..strokeWidth = math.max(2, r * 0.07)
      ..strokeCap = StrokeCap.round;
    final fill = Paint()..color = Slime.mouth;

    if (e.talking && e.mood != Mood.strain) {
      final open = 0.25 + 0.75 * (math.sin(t * 15)).abs() * (0.6 + 0.4 * math.sin(t * 3.7).abs());
      final rect = Rect.fromCenter(center: m, width: mw * 1.3, height: mw * 1.1 * open);
      canvas.drawOval(rect, fill);
      return;
    }

    switch (e.mood) {
      case Mood.happy || Mood.excited:
        final big = e.mood == Mood.excited ? 1.25 : 1.0;
        final path = Path()
          ..moveTo(m.dx - mw * big, m.dy - mw * 0.2)
          ..quadraticBezierTo(m.dx, m.dy + mw * 1.6 * big, m.dx + mw * big, m.dy - mw * 0.2)
          ..close();
        canvas.drawPath(path, fill);
        canvas.save();
        canvas.clipPath(path);
        canvas.drawCircle(m + Offset(0, mw * 1.0 * big), mw * 0.6, Paint()..color = Slime.tongue);
        canvas.restore();
      case Mood.surprised:
        canvas.drawOval(Rect.fromCenter(center: m + Offset(0, mw * 0.2), width: mw * 0.8, height: mw * 1.0), fill);
      case Mood.sad:
        canvas.drawArc(Rect.fromCircle(center: m + Offset(0, mw * 0.8), radius: mw * 0.8), math.pi * 1.2,
            math.pi * 0.6, false, line);
      case Mood.strain:
        final rect = RRect.fromRectAndRadius(
            Rect.fromCenter(center: m, width: mw * 2, height: mw * 0.8), Radius.circular(mw * 0.3));
        canvas.drawRRect(rect, Paint()..color = Colors.white);
        canvas.drawRRect(rect, line..strokeWidth = math.max(1.8, r * 0.05));
        canvas.drawLine(Offset(m.dx - mw, m.dy), Offset(m.dx + mw, m.dy), line);
        for (final dx in [-0.4, 0.0, 0.4]) {
          canvas.drawLine(Offset(m.dx + mw * dx, m.dy - mw * 0.4), Offset(m.dx + mw * dx, m.dy + mw * 0.4), line);
        }
      case Mood.confused:
        final path = Path()..moveTo(m.dx - mw, m.dy);
        for (var i = 1; i <= 8; i++) {
          final x = m.dx - mw + i * mw / 4;
          path.lineTo(x, m.dy + (i.isOdd ? -1 : 1) * mw * 0.18);
        }
        canvas.drawPath(path, line..strokeWidth = math.max(1.8, r * 0.055));
      case Mood.proud:
        final path = Path()
          ..moveTo(m.dx - mw * 0.7, m.dy + mw * 0.1)
          ..quadraticBezierTo(m.dx + mw * 0.2, m.dy + mw * 0.5, m.dx + mw, m.dy - mw * 0.35);
        canvas.drawPath(path, line);
      case Mood.thinking:
        canvas.drawLine(Offset(m.dx - mw * 0.1, m.dy + mw * 0.1), Offset(m.dx + mw * 0.6, m.dy - mw * 0.05), line);
      case Mood.neutral:
        canvas.drawArc(Rect.fromCircle(center: m - Offset(0, mw * 0.5), radius: mw * 0.8), math.pi * 0.2,
            math.pi * 0.6, false, line);
    }
  }

  /// Sweat when straining, sparkles when excited, the "?" when asking.
  void _paintExtras(Canvas canvas, Offset center, double r, double w, double h, double t) {
    final e = engine;
    final top = center.dy - h / 2;

    if (e.mood == Mood.strain) {
      for (var i = 0; i < 2; i++) {
        final phase = (t * 1.4 + i * 0.5) % 1.0;
        final side = i == 0 ? 1.0 : -1.0;
        final p = Offset(center.dx + side * w * 0.95, top + h * 0.15 + phase * h * 0.35);
        final s = r * 0.13;
        final drop = Path()
          ..moveTo(p.dx, p.dy - s * 1.6)
          ..quadraticBezierTo(p.dx + s, p.dy, p.dx, p.dy + s)
          ..quadraticBezierTo(p.dx - s, p.dy, p.dx, p.dy - s * 1.6);
        canvas.drawPath(drop, Paint()..color = Slime.sweat.withValues(alpha: 1 - phase * 0.7));
      }
    }

    if (e.mood == Mood.excited) {
      for (var i = 0; i < 3; i++) {
        final a = t * 1.5 + i * 2.1;
        final p = Offset(center.dx + math.cos(a) * w * 1.4, top + h * 0.2 + math.sin(a) * h * 0.4);
        canvas.save();
        canvas.translate(p.dx, p.dy + r * 0.1);
        canvas.drawPath(_star(r * 0.12), Paint()..color = const Color(0xFFE2B33C));
        canvas.restore();
      }
    }

    if (e.hat != null) {
      final tp = TextPainter(
        text: TextSpan(text: e.hat, style: TextStyle(fontSize: r * 0.85, height: 1)),
        textDirection: TextDirection.ltr,
      )..layout();
      canvas.save();
      canvas.translate(center.dx, top + r * 0.12);
      canvas.rotate(e.lean * 0.4 - 0.12);
      tp.paint(canvas, Offset(-tp.width / 2, -tp.height));
      canvas.restore();
    }

    if (e.asking || e.mood == Mood.confused) {
      final bob = math.sin(t * 3.2) * r * 0.08;
      final tilt = math.sin(t * 2.1) * 0.12;
      final tp = TextPainter(
        text: TextSpan(
          text: '?',
          style: TextStyle(fontSize: r * 0.95, fontWeight: FontWeight.w800, color: Paper.accent, height: 1),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      canvas.save();
      final aside = e.hat != null ? r * 0.9 : 0.0;
      canvas.translate(center.dx + aside, top - r * 0.2 - tp.height / 2 + bob);
      canvas.rotate(tilt);
      tp.paint(canvas, Offset(-tp.width / 2, -tp.height / 2));
      canvas.restore();
    }
  }

  @override
  bool shouldRepaint(covariant StagePainter old) => old.engine != engine;
}
