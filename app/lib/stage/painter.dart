import 'dart:math' as math;
import 'dart:typed_data';

import 'package:flutter/material.dart';

import '../theme.dart';
import 'engine.dart';
import 'solids.dart';
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

  /// Words asked for while painting a thing, drawn after everything else
  /// (with the transform they were asked for under), so no object, the
  /// slime or a later prop can cover them.
  final List<(Float64List, void Function(Canvas))> _words = [];

  void _later(Canvas canvas, void Function(Canvas) draw) => _words.add((canvas.getTransform(), draw));

  @override
  void paint(Canvas canvas, Size size) {
    _words.clear();
    final base = canvas.getTransform();
    final t = engine.time;
    final groundY = kGroundY * size.height;
    final room = engine.worldT;

    if (room < 1) {
      canvas.drawLine(
        Offset(16, groundY),
        Offset(size.width - 16, groundY),
        Paint()
          ..color = Paper.borderStrong.withValues(alpha: 1 - room)
          ..strokeWidth = 1.5
          ..strokeCap = StrokeCap.round,
      );
    }
    if (room > 0) _paintFloor(canvas, size, room);
    _paintLanes(canvas, size, t);
    _paintSpotlight(canvas, size, room);

    // what lies on the floor first: shadows, and the light a new thing
    // arrives in
    for (final p in engine.props.values) {
      if (p.kind == PropKind.link) continue;
      if (isSolidKind(p.kind)) {
        if (p.around != null) _paintOrbitPath(canvas, size, p);
      } else if (!isGraphKind(p.kind) && p.kind != PropKind.math) {
        _paintGround(canvas, size, p, t);
      }
    }

    // back to front: everything deeper than the slime, far to near (solids
    // and flat things alike); then the graphs and what stands at the front;
    // the slime; a solid passing in front of it (an orbit's near side); and
    // words and arrows last, so nothing hides them
    final deep = <(double, StageProp)>[];
    final front = <StageProp>[], near = <StageProp>[], notes = <StageProp>[];
    for (final p in engine.props.values) {
      if (p.kind == PropKind.link) continue;
      if (isSolidKind(p.kind)) {
        final depth = engine.solidPlace(p).$3;
        depth > 0 ? deep.add((depth, p)) : near.add(p);
      } else if (p.z > 0 && !p.carried && !isGraphKind(p.kind)) {
        deep.add((p.z, p));
      } else if (_isSpan(p.kind) || p.kind == PropKind.text) {
        notes.add(p);
      } else {
        front.add(p);
      }
    }
    deep.sort((a, b) => b.$1.compareTo(a.$1));
    for (final (_, p) in deep) {
      isSolidKind(p.kind) ? _paintSolid(canvas, size, p, t) : _paintProp(canvas, size, p, t);
    }
    for (final p in front) {
      _paintProp(canvas, size, p, t);
    }
    _paintBlob(canvas, size, t);
    for (final p in near) {
      _paintSolid(canvas, size, p, t);
    }
    for (final p in notes) {
      _paintProp(canvas, size, p, t);
    }
    for (final p in engine.props.values) {
      if (p.kind == PropKind.link) _paintLink(canvas, size, p, t);
    }
    _paintParticles(canvas, size, t);
    // back to each word's own transform, from the one the frame started in
    final undo = Matrix4.fromFloat64List(base)..invert();
    for (final (m, draw) in _words) {
      canvas.save();
      canvas.transform((undo.clone()..multiply(Matrix4.fromFloat64List(m))).storage);
      draw(canvas);
      canvas.restore();
    }
    _words.clear();
  }

  // ------------------------------------------------------------- 3D world

  /// The 3D space: a floor that runs back to a horizon, lines converging on
  /// a vanishing point, fading in as the world turns 3D.
  void _paintFloor(Canvas canvas, Size size, double k) {
    final horizon = StageEngine.horizonY * size.height;
    final ground = kGroundY * size.height;
    final vx = engine.vanishX * size.width;

    // the back wall: a soft light behind the horizon, like a lit room
    final wall = Rect.fromLTRB(0, 0, size.width, horizon);
    canvas.drawRect(
      wall,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            Paper.sliver.withValues(alpha: 0),
            Color.lerp(Paper.sliver, Paper.border, 0.35)!.withValues(alpha: 0.7 * k),
          ],
        ).createShader(wall),
    );
    canvas.drawOval(
      Rect.fromCenter(center: Offset(vx, horizon), width: size.width * 0.9, height: size.height * 0.5),
      Paint()
        ..shader = RadialGradient(colors: [
          Colors.white.withValues(alpha: 0.55 * k),
          Colors.white.withValues(alpha: 0),
        ]).createShader(Rect.fromCenter(center: Offset(vx, horizon), width: size.width * 0.9, height: size.height * 0.5)),
    );

    final floor = Rect.fromLTRB(0, horizon, size.width, size.height);
    canvas.drawRect(
      floor,
      Paint()
        ..shader = LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [
            Paper.sliver.withValues(alpha: 0),
            Color.lerp(Paper.sliver, Paper.border, 0.6)!.withValues(alpha: 0.95 * k),
          ],
        ).createShader(floor),
    );
    // the lines fade out into the distance
    final line = Paint()
      ..strokeWidth = 1
      ..shader = LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [
          Paper.borderStrong.withValues(alpha: 0),
          Paper.borderStrong.withValues(alpha: 0.6 * k),
        ],
      ).createShader(floor);
    final vanish = Offset(vx, horizon);
    for (var i = -9; i <= 9; i++) {
      final foot = Offset(size.width / 2 + i * size.width / 7, size.height * 1.05);
      canvas.drawLine(vanish, foot, line);
    }
    for (var z = 0.0; z <= 1.0; z += 0.125) {
      final y = horizon + (ground - horizon) * StageEngine.perspective(z);
      canvas.drawLine(Offset(0, y), Offset(size.width, y), line);
    }
    // below the front row, the floor keeps coming toward us
    for (var y = ground + (ground - horizon) * 0.35; y < size.height; y += (ground - horizon) * 0.5) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), line);
    }
    canvas.drawLine(Offset(0, horizon), Offset(size.width, horizon),
        Paint()..color = Paper.body.withValues(alpha: 0.3 * k)..strokeWidth = 1.2);

    // dust drifting up through the light, at every depth
    for (var i = 0; i < 18; i++) {
      final h = _hash(i);
      final z = _hash(i + 40);
      final rise = (engine.time * (0.02 + 0.03 * _hash(i + 80)) + h) % 1.0;
      final at = Offset(_hash(i + 120) + math.sin(engine.time * 0.4 + i) * 0.01, kGroundY - rise * 0.6);
      final (o, s) = engine.project(at, z);
      final fade = math.sin(math.pi * rise);
      canvas.drawCircle(
        Offset(o.dx * size.width, o.dy * size.height),
        (1.2 + 2.2 * _hash(i + 160)) * s,
        Paint()..color = Color.lerp(Paper.accent, Colors.white, 0.55)!.withValues(alpha: 0.45 * fade * k),
      );
    }
  }

  static double _hash(int i) {
    final v = math.sin(i * 12.9898 + 78.233) * 43758.5453;
    return v - v.floorToDouble();
  }

  /// A pool of light on the floor where the slime stands.
  void _paintSpotlight(Canvas canvas, Size size, double k) {
    if (k <= 0) return;
    final foot = Offset(engine.blob.base(engine.time).dx * size.width, kGroundY * size.height);
    final r = Rect.fromCenter(center: foot, width: engine.blobRadius * 6, height: engine.blobRadius * 1.3);
    canvas.drawOval(
      r,
      Paint()
        ..shader = RadialGradient(colors: [
          Colors.white.withValues(alpha: 0.7 * k),
          Colors.white.withValues(alpha: 0),
        ]).createShader(r),
    );
  }

  /// Toward the vanishing point from [o]: where a thing's depth shows.
  Offset _depthDir(Offset o, Size size) {
    final v = Offset(engine.vanishX * size.width, StageEngine.horizonY * size.height) - o;
    final len = v.distance;
    return len < 1 ? const Offset(0, -1) : v / len;
  }

  /// Give a flat shape body: its sides, stepping back toward the vanishing
  /// point, in a darker shade -- the face is drawn on top by the caller.
  void _extrude(Canvas canvas, Path face, Color color, Offset dir, double depth, double alpha) {
    depth *= engine.worldT;
    if (depth < 0.8) return;
    final n = (depth / 1.4).clamp(2, 12).round();
    final side = Paint()..color = Color.lerp(color, Colors.black, 0.3)!.withValues(alpha: alpha);
    for (var i = n; i >= 1; i--) {
      canvas.drawPath(face.shift(dir * (depth * i / n)), side);
    }
    // a lit rim where the side meets the face
    canvas.drawPath(
      face.shift(dir * (depth / n)),
      Paint()..color = Color.lerp(color, Colors.black, 0.15)!.withValues(alpha: alpha),
    );
  }

  /// Under a prop: its shadow on the floor (fainter the higher it floats),
  /// and, as it arrives, a ring and a beam of light.
  void _paintGround(Canvas canvas, Size size, StageProp p, double t) {
    if (p.carried || engine.hostOf(p) != null) return;
    final at = p.pos.at(t);
    final (foot, k) = engine.project(Offset(at.dx, kGroundY), p.z);
    final o = Offset(foot.dx * size.width, foot.dy * size.height);
    final half = engine.propHalf(p) * k * p.size;
    final height = (kGroundY - at.dy).clamp(0.0, 1.0);
    final age = t - p.bornAt;
    final alive = (p.diedAt == null ? 1.0 : 1 - ((t - p.diedAt!) / 0.45).clamp(0.0, 1.0)) * p.dim.at(t);
    if (!_isSpan(p.kind) && p.kind != PropKind.text) {
      final far = (height * 2.5).clamp(0.0, 0.75);
      final grow = (age / 0.4).clamp(0.0, 1.0);
      canvas.drawOval(
        Rect.fromCenter(center: o, width: half * 2.3 * (1 - far * 0.5) * grow, height: half * 0.45 * (1 - far * 0.4)),
        Paint()
          ..color = Color.fromRGBO(0, 0, 0, 0.14 * (1 - far) * alive * engine.worldT)
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 3),
      );
    }
    if (age < 0.9) {
      final a = age / 0.9;
      final ring = half * (0.6 + 2.2 * Curves.easeOutCubic.transform(a));
      canvas.drawOval(
        Rect.fromCenter(center: o, width: ring * 2, height: ring * 0.5),
        Paint()
          ..color = Paper.accent.withValues(alpha: 0.55 * (1 - a))
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.5 * (1 - a) + 0.5,
      );
      final top = Offset(o.dx, (at.dy * size.height - half * 2.6 * k).clamp(0.0, o.dy));
      final beam = Rect.fromLTRB(o.dx - half * 0.9, top.dy - half, o.dx + half * 0.9, o.dy);
      canvas.drawRect(
        beam,
        Paint()
          ..shader = LinearGradient(
            begin: Alignment.bottomCenter,
            end: Alignment.topCenter,
            colors: [Colors.white.withValues(alpha: 0.75 * (1 - a)), Colors.white.withValues(alpha: 0)],
          ).createShader(beam),
      );
    }
  }

  void _paintSolid(Canvas canvas, Size size, StageProp p, double t) {
    final kind = solidKindFromName(p.kind.name);
    if (kind == null) return;
    final (base, scale, _) = engine.solidPlace(p);
    final age = t - p.bornAt;
    final (enterAt, enterScale, enterAlpha) = _entrance(p, age, size, base.dx);
    var pop = enterScale;
    var alpha = p.dim.at(t) * enterAlpha;
    var drift = 0.0;
    if (p.diedAt != null) {
      final k = ((t - p.diedAt!) / 0.45).clamp(0.0, 1.0);
      pop *= 1 - k;
      alpha *= 1 - k;
      drift = -18 * k;
    }
    if (pop <= 0.001) return;
    final px = engine.blobRadius * 1.7 * p.size * scale * p.scale.at(t) * pop;
    final bob = p.lift > 0 ? math.sin(t * 1.7 + _hashOf(p.id) * 6.28) * 3 * scale : 0.0;
    var contact = Offset(base.dx * size.width, base.dy * size.height) + enterAt + Offset(0, bob + drift);
    final cruising = p.cruise.at(t);
    if (cruising > 0.02) {
      contact += Offset(0, math.sin(t * 43) * 1.3 * cruising);
      _paintSpeedLines(canvas, contact, px / 2, cruising, t, p.color);
    }
    // a soft shadow on the floor, smaller and fainter the higher it floats
    final floorY = contact.dy + p.lift * scale * size.height;
    final lifted = (p.lift * 4).clamp(0.0, 0.8);
    canvas.drawOval(
      Rect.fromCenter(center: Offset(contact.dx, floorY), width: px * (1 - lifted * 0.5), height: px * 0.22),
      Paint()..color = Color.fromRGBO(0, 0, 0, 0.12 * alpha * (1 - lifted)),
    );
    final centre = contact - Offset(0, px / 2);
    final shake = t < p.shakeUntil ? Offset(math.sin(t * 70) * 2.2, 0) : Offset.zero;
    paintSolid(
      canvas,
      centre + shake,
      px,
      kind,
      yaw: 2 * math.pi * (p.yaw.at(t) + p.spinRate * t),
      pitch: 2 * math.pi * p.pitch.at(t),
      color: propColor(p.color, _solidColor(kind)),
      time: t,
      alpha: alpha,
    );
    if (p.label != null) _label(canvas, p.label!, contact + Offset(0, 14), 12, Paper.ink, alpha);
  }

  static Color _solidColor(SolidKind k) => switch (k) {
        SolidKind.cube => const Color(0xFFD9895B),
        SolidKind.sphere => const Color(0xFF4F7CAC),
        SolidKind.cylinder => const Color(0xFF6D9A5C),
        SolidKind.cone => const Color(0xFFE2B33C),
        SolidKind.pyramid => const Color(0xFFC85A2E),
        SolidKind.prism => const Color(0xFF8E6BB8),
        SolidKind.torus => const Color(0xFFE0708A),
        SolidKind.planet => const Color(0xFFD9A066),
        SolidKind.atom => const Color(0xFFC85A2E),
      };

  // ---------------------------------------------------------------- props

  void _paintProp(Canvas canvas, Size size, StageProp p, double t) {
    if (isGraphKind(p.kind)) return _paintGraph(canvas, size, p, t);
    if (p.kind == PropKind.math || p.kind == PropKind.photo) return; // StageView shows these, over the canvas
    final at = engine.propAt(p);
    final depthK = engine.propScale(p);
    var o = Offset(at.dx * size.width, at.dy * size.height);
    if (t < p.shakeUntil) o += Offset(math.sin(t * 70) * 2.2, 0);
    final dir = _depthDir(o, size);

    final age = t - p.bornAt;
    final span = _isSpan(p.kind);
    var scale = 1.0;
    var alpha = p.dim.at(t);
    if (span || p.kind == PropKind.text) {
      scale = Curves.elasticOut.transform((age / 0.55).clamp(0.0, 1.0));
    } else {
      final (enterAt, enterScale, enterAlpha) = _entrance(p, age, size, at.dx);
      o += enterAt;
      scale = enterScale;
      alpha *= enterAlpha;
      // alive while it waits: floating things bob, resting things breathe
      if (age > 0.9 && p.diedAt == null && !p.carried) {
        final h = _hashOf(p.id) * 6.28;
        final floating = p.pos.at(t).dy < kGroundY - 0.03;
        if (floating) o += Offset(0, math.sin(t * 1.7 + h) * 3 * depthK);
        scale *= 1 + 0.018 * math.sin(t * 2.1 + h);
      }
      _paintTrail(canvas, size, p, t, o, depthK);
      final cruising = p.cruise.at(t);
      if (cruising > 0.02) {
        o += Offset(0, math.sin(t * 43) * 1.3 * cruising);
        _paintSpeedLines(canvas, o, engine.propHalf(p) * depthK, cruising, t, p.color);
      }
    }
    if (p.diedAt != null) {
      final k = ((t - p.diedAt!) / 0.45).clamp(0.0, 1.0);
      _poof(canvas, o, engine.propHalf(p), k);
      o += Offset(0, -18 * k);
      scale *= 1 - k;
      alpha *= 1 - k;
    }
    if (scale <= 0.001) return;

    final half = engine.propHalf(p);
    final grown = p.scale.at(t);
    final turn = p.spin.at(t);
    canvas.save();
    // Props stand on their base point: scale from there, so they "grow" up
    // out of the ground rather than from their middle. Spans (arrows, lines,
    // waves, paths) draw themselves out instead of popping.
    canvas.translate(o.dx, o.dy);
    canvas.scale(depthK);
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
        final boxCol = propColor(p.color, const Color(0xFFD9B98A));
        _extrude(canvas, Path()..addRRect(rr), boxCol, dir, half * 0.7, alpha);
        canvas.drawRRect(
          rr,
          Paint()
            ..shader = LinearGradient(
              begin: Alignment.topLeft,
              end: Alignment.bottomRight,
              colors: [Color.lerp(boxCol, Colors.white, 0.18)!, boxCol],
            ).createShader(r)
            ..color = boxCol.withValues(alpha: alpha),
        );
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
        _sphere(canvas, c, half, propColor(p.color, Paper.accent), alpha);
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
        canvas.drawLine(dir * 3 * engine.worldT, head + dir * 3 * engine.worldT,
            Paint()..color = Color.lerp(col, Colors.black, 0.35)!..strokeWidth = 4..strokeCap = StrokeCap.round);
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
        final textCol = propColor(p.color, Paper.ink);
        final steps = (4 * engine.worldT).round();
        for (var i = steps; i >= 1; i--) {
          _label(canvas, p.label ?? '', dir * (i * 0.9), 15 * p.size, Color.lerp(textCol, Paper.border, 0.55)!, alpha,
              bold: true);
        }
        _label(canvas, p.label ?? '', Offset.zero, 15 * p.size, textCol, alpha, bold: true);

      case PropKind.star:
        canvas.rotate(math.sin(t * 2) * 0.2);
        final starCol = propColor(p.color, const Color(0xFFE2B33C));
        _extrude(canvas, _star(half), starCol, dir, half * 0.35, alpha);
        canvas.drawPath(_star(half), Paint()..color = starCol.withValues(alpha: alpha));
        if (p.label != null) _label(canvas, p.label!, Offset(0, half * 1.6), 12, Paper.ink, alpha);

      case PropKind.heart:
        final heartCol = propColor(p.color, const Color(0xFFE0708A));
        _extrude(canvas, _heart(half), heartCol, dir, half * 0.35, alpha);
        canvas.drawPath(_heart(half), Paint()..color = heartCol.withValues(alpha: alpha));

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
          canvas.drawCircle(c + Offset(0, half * 0.14), half * 0.65,
              Paint()..color = const Color(0xFFD9DEE6).withValues(alpha: alpha * engine.worldT));
        }
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
        if (p.caption != null) _caption(canvas, p.caption!, Offset(0, 14), alpha, t - p.shakeUntil);

      case PropKind.circle:
        final rad = (p.w ?? 0.12) * size.height / 2 * p.size;
        final c = Offset(0, -rad);
        final col = propColor(p.color, const Color(0xFF4F7CAC));
        _extrude(canvas, Path()..addOval(Rect.fromCircle(center: c, radius: rad)), col, dir, rad * 0.22, alpha);
        canvas.drawCircle(
          c,
          rad,
          Paint()
            ..shader = RadialGradient(
              center: const Alignment(-0.35, -0.4),
              colors: [Color.lerp(col, Colors.white, 0.25)!.withValues(alpha: alpha), col.withValues(alpha: alpha)],
            ).createShader(Rect.fromCircle(center: c, radius: rad)),
        );
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
        _extrude(canvas, shape, col, dir, math.min(bw, bh) * 0.18, alpha);
        canvas.drawPath(shape, Paint()..color = col.withValues(alpha: alpha));
        canvas.drawPath(shape, _outline(col, alpha));
        if (p.label != null) {
          _label(canvas, p.label!, Offset(0, p.kind == PropKind.rect ? -bh / 2 : -bh * 0.35),
              math.min(bw, bh) * 0.3, Colors.white, alpha, bold: true);
        }

      case PropKind.line:
        final grow = _drawn(p, age, scale) * grown;
        final d = p.headDelta ?? const Offset(0.2, 0);
        final end = Offset(d.dx * size.width, d.dy * size.height) * grow;
        canvas.drawLine(dir * 2.5 * engine.worldT, end + dir * 2.5 * engine.worldT,
            _stroke(Color.lerp(ink, Paper.border, 0.6)!, 3));
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
        canvas.drawPath(path.shift(dir * 2.5 * engine.worldT),
            _stroke(Color.lerp(col, Colors.black, 0.35)!, 3)..style = PaintingStyle.stroke);
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
        canvas.drawPath(path.shift(dir * 2.5 * engine.worldT),
            _stroke(Color.lerp(col, Colors.black, 0.35)!, 3)..style = PaintingStyle.stroke);
        canvas.drawPath(path, _stroke(col, 3)..style = PaintingStyle.stroke);
        if (p.label != null && grow >= 1) _label(canvas, p.label!, pts.last + const Offset(0, -14), 13, col, alpha);
      case PropKind.axes || PropKind.plot || PropKind.dot || PropKind.tangent || PropKind.math || PropKind.photo:
        break; // drawn by _paintGraph / StageView
      case PropKind.link:
        break; // drawn by _paintLink
      case PropKind.clock || PropKind.stopwatch || PropKind.counter || PropKind.gauge || PropKind.bar ||
            PropKind.thermometer:
        _paintInstrument(canvas, p, half, alpha, dir, t);
      case PropKind.cube || PropKind.sphere || PropKind.cylinder || PropKind.cone || PropKind.pyramid ||
            PropKind.prism || PropKind.torus || PropKind.planet || PropKind.atom:
        break; // drawn by _paintSolid
    }
    canvas.restore();
  }

  // ------------------------------------------------------------ graph kit

  /// Axes, a curve drawing itself along them, a dot riding the curve, and
  /// the tangent that follows the dot -- in the axes' own numbers.
  void _paintGraph(Canvas canvas, Size size, StageProp p, double t) {
    final axes = engine.axesOf(p);
    if (axes == null) return;
    final age = t - p.bornAt;
    final since = engine.fadingSince(p);
    final alpha = (since == null ? 1.0 : 1 - ((t - since) / 0.45).clamp(0.0, 1.0)) * axes.dim.at(t);
    if (alpha <= 0) return;
    Offset px(double gx, double gy) {
      final s = engine.graphToStage(axes, gx, gy);
      return Offset(s.dx * size.width, s.dy * size.height);
    }

    final box = Rect.fromPoints(px(axes.xmin, axes.ymax), px(axes.xmax, axes.ymin));
    switch (p.kind) {
      case PropKind.axes:
        final grow = Curves.easeOutCubic.transform((age / 0.7).clamp(0.0, 1.0));
        _paintBoard(canvas, size, box, grow, alpha);
        final ink = Paper.body.withValues(alpha: alpha);
        final faint = Paper.border.withValues(alpha: alpha * grow);
        final gx0 = (0 >= axes.xmin && 0 <= axes.xmax) ? 0.0 : axes.xmin;
        final gy0 = (0 >= axes.ymin && 0 <= axes.ymax) ? 0.0 : axes.ymin;
        // light grid on "nice" steps, with small numbers on the axes
        final xs = _niceStep(axes.xmax - axes.xmin), ys = _niceStep(axes.ymax - axes.ymin);
        for (var v = (axes.xmin / xs).ceil() * xs; v <= axes.xmax + 1e-9; v += xs) {
          final a = px(v, axes.ymin), b = px(v, axes.ymax);
          canvas.drawLine(a, b, Paint()..color = faint..strokeWidth = 1);
          if (v.abs() > 1e-9 && grow > 0.9) {
            _label(canvas, _num(v), px(v, gy0) + const Offset(0, 12), 10, Paper.faint, alpha);
          }
        }
        for (var v = (axes.ymin / ys).ceil() * ys; v <= axes.ymax + 1e-9; v += ys) {
          final a = px(axes.xmin, v), b = px(axes.xmax, v);
          canvas.drawLine(a, b, Paint()..color = faint..strokeWidth = 1);
          if (v.abs() > 1e-9 && grow > 0.9) {
            _label(canvas, _num(v), px(gx0, v) + const Offset(-14, 0), 10, Paper.faint, alpha);
          }
        }
        final xa = px(axes.xmin, gy0), xb = Offset.lerp(xa, px(axes.xmax, gy0), grow)!;
        final ya = px(gx0, axes.ymin), yb = Offset.lerp(ya, px(gx0, axes.ymax), grow)!;
        final axis = _stroke(ink, 2);
        canvas.drawLine(xa, xb, axis);
        canvas.drawLine(ya, yb, axis);
        _arrowHead(canvas, xb, 0, ink);
        _arrowHead(canvas, yb, -math.pi / 2, ink);

      case PropKind.plot:
        final f = p.formula;
        if (f == null) return;
        final grow = Curves.easeInOutCubic.transform((age / 1.3).clamp(0.0, 1.0));
        final col = propColor(p.color, Paper.accent).withValues(alpha: alpha);
        final span = axes.ymax - axes.ymin;
        const steps = 220;
        final path = Path();
        var pen = false;
        for (var i = 0; i <= steps * grow; i++) {
          final gx = axes.xmin + (axes.xmax - axes.xmin) * i / steps;
          final gy = f(gx);
          if (!gy.isFinite || gy < axes.ymin - span || gy > axes.ymax + span) {
            pen = false;
            continue;
          }
          final q = px(gx, gy);
          pen ? path.lineTo(q.dx, q.dy) : path.moveTo(q.dx, q.dy);
          pen = true;
        }
        canvas.save();
        canvas.clipRect(box.inflate(2));
        canvas.drawPath(path, _stroke(col, 3)..style = PaintingStyle.stroke);
        canvas.restore();

      case PropKind.dot:
        final at = engine.dotPoint(p);
        if (at == null) return;
        final o = Offset(at.dx * size.width, at.dy * size.height);
        final pop = Curves.elasticOut.transform((age / 0.55).clamp(0.0, 1.0));
        final col = propColor(p.color, Paper.accent).withValues(alpha: alpha);
        // dashed guides down to the x-axis and across to the y-axis
        final gx0 = (0 >= axes.xmin && 0 <= axes.xmax) ? 0.0 : axes.xmin;
        final gy0 = (0 >= axes.ymin && 0 <= axes.ymax) ? 0.0 : axes.ymin;
        final guide = Paint()
          ..color = col.withValues(alpha: 0.35 * alpha)
          ..strokeWidth = 1.2;
        _dashed(canvas, o, Offset(o.dx, px(0, gy0).dy), guide);
        _dashed(canvas, o, Offset(px(gx0, 0).dx, o.dy), guide);
        canvas.drawCircle(o, 8 * pop, Paint()..color = Colors.white.withValues(alpha: alpha));
        canvas.drawCircle(o, 6 * pop, Paint()..color = col);

      case PropKind.tangent:
        final dot = engine.parentOf(p);
        final at = engine.dotPoint(dot);
        final slope = engine.slopeUnder(p);
        if (dot == null || at == null || slope == null) return;
        final x0 = dot.at.at(t);
        final y0 = engine.plotOf(dot)!.formula!(x0);
        final grow = Curves.easeOutCubic.transform((age / 0.5).clamp(0.0, 1.0));
        final dx = 0.28 * (axes.xmax - axes.xmin) * grow;
        final a = px(x0 - dx, y0 - slope * dx), b = px(x0 + dx, y0 + slope * dx);
        canvas.save();
        canvas.clipRect(box.inflate(2));
        canvas.drawLine(a, b, _stroke(propColor(p.color, Paper.olive).withValues(alpha: alpha), 2.5));
        canvas.restore();

      default:
        break;
    }
  }

  /// A board the graph is drawn on, standing in the room: a shadow, an edge
  /// with depth, a white face. It unfolds from the middle as the axes grow.
  void _paintBoard(Canvas canvas, Size size, Rect box, double grow, double alpha) {
    final k = engine.worldT;
    if (k <= 0) return;
    final full = Rect.fromLTRB(box.left - 30, box.top - 30, box.right + 22, box.bottom + 26);
    final r = Rect.fromCenter(center: full.center, width: full.width, height: full.height * (0.2 + 0.8 * grow));
    final rr = RRect.fromRectAndRadius(r, const Radius.circular(10));
    final a = alpha * k;
    canvas.drawRRect(
      rr.shift(const Offset(6, 10)),
      Paint()
        ..color = Color.fromRGBO(0, 0, 0, 0.1 * a)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 10),
    );
    _extrude(canvas, Path()..addRRect(rr), Paper.border.withValues(alpha: a), _depthDir(r.center, size), 9, a);
    canvas.drawRRect(rr, Paint()..color = Colors.white.withValues(alpha: 0.94 * a));
    canvas.drawRRect(
      rr,
      Paint()
        ..color = Paper.border.withValues(alpha: a)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.2,
    );
  }

  static double _niceStep(double range) {
    final raw = range / 5;
    final mag = math.pow(10, (math.log(raw) / math.ln10).floor()).toDouble();
    for (final m in const [1.0, 2.0, 5.0, 10.0]) {
      if (raw <= m * mag) return m * mag;
    }
    return 10 * mag;
  }

  static String _num(double v) {
    final r = (v * 100).round() / 100;
    return r == r.roundToDouble() ? r.toInt().toString() : r.toString();
  }

  void _arrowHead(Canvas canvas, Offset tip, double angle, Color c) {
    const len = 8.0, wing = 0.5;
    final path = Path()
      ..moveTo(tip.dx, tip.dy)
      ..lineTo(tip.dx - len * math.cos(angle - wing), tip.dy - len * math.sin(angle - wing))
      ..lineTo(tip.dx - len * math.cos(angle + wing), tip.dy - len * math.sin(angle + wing))
      ..close();
    canvas.drawPath(path, Paint()..color = c);
  }

  void _dashed(Canvas canvas, Offset a, Offset b, Paint paint) {
    final d = b - a;
    final len = d.distance;
    if (len < 1) return;
    final dir = d / len;
    for (var s = 0.0; s < len; s += 8) {
      canvas.drawLine(a + dir * s, a + dir * math.min(s + 4, len), paint);
    }
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

  static final _confetti = [Color(0xFFE2B33C), Paper.accent, Color(0xFF4F7CAC), Color(0xFF93D374), Color(0xFFE0708A)];

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

  static double _hashOf(String id) => _hash(id.codeUnits.fold(7, (a, c) => (a * 31 + c) & 0xFFFF));

  /// How a thing arrives, by its "enter" or (so a scene varies) its id:
  /// pop, drop in with a bounce, rise out of the floor, or swoop in from the
  /// side. -> (offset in px, scale, alpha).
  (Offset, double, double) _entrance(StageProp p, double age, Size size, double x) {
    const styles = ['pop', 'drop', 'rise', 'swoop'];
    final style = styles.contains(p.enter) ? p.enter! : styles[(_hashOf(p.id) * 4).floor() % 4];
    switch (style) {
      case 'drop':
        final a = (age / 0.75).clamp(0.0, 1.0);
        final fall = 1 - Curves.bounceOut.transform(a);
        return (Offset(0, -fall * size.height * 0.32), 1.0, (age / 0.12).clamp(0.0, 1.0));
      case 'rise':
        final a = (age / 0.6).clamp(0.0, 1.0);
        return (
          Offset(0, (1 - Curves.easeOutCubic.transform(a)) * 26),
          0.55 + 0.45 * Curves.easeOutBack.transform(a),
          a,
        );
      case 'swoop':
        final a = (age / 0.7).clamp(0.0, 1.0);
        final from = x < 0.5 ? -1.0 : 1.0;
        final e = Curves.easeOutCubic.transform(a);
        return (
          Offset(from * (1 - e) * size.width * 0.35, -math.sin(math.pi * a) * 34),
          0.7 + 0.3 * e,
          (age / 0.2).clamp(0.0, 1.0),
        );
      default:
        return (Offset.zero, Curves.elasticOut.transform((age / 0.55).clamp(0.0, 1.0)), 1.0);
    }
  }

  /// A soft streak behind a thing on the move.
  void _paintTrail(Canvas canvas, Size size, StageProp p, double t, Offset now, double k) {
    final track = p.pos;
    if (p.carried || track.dur <= 0 || t < track.start || t > track.start + track.dur + 0.15) return;
    if ((track.to - track.from).distance < 0.03) return;
    final back = engine.project(track.at(math.max(track.start, t - 0.22)), p.z).$1;
    final tail = Offset(back.dx * size.width, back.dy * size.height) - Offset(0, engine.propHalf(p) * k);
    final head = now - Offset(0, engine.propHalf(p) * k);
    if ((head - tail).distance < 4) return;
    final col = propColor(p.color, Paper.accent);
    canvas.drawLine(
      tail,
      head,
      Paint()
        ..strokeWidth = engine.propHalf(p) * 1.1 * k
        ..strokeCap = StrokeCap.round
        ..shader = LinearGradient(colors: [col.withValues(alpha: 0), col.withValues(alpha: 0.28)])
            .createShader(Rect.fromPoints(tail, head)),
    );
  }

  /// The path an orbiting solid follows, faint on its plane.
  void _paintOrbitPath(Canvas canvas, Size size, StageProp p) {
    final c = engine.props[p.around];
    if (c == null || p.diedAt != null) return;
    final path = Path();
    for (var i = 0; i <= 48; i++) {
      final (at, scale, _) = engine.solidPlace(p, phase: i / 48);
      final px = engine.blobRadius * 1.7 * p.size * scale;
      final o = Offset(at.dx * size.width, at.dy * size.height - px / 2);
      i == 0 ? path.moveTo(o.dx, o.dy) : path.lineTo(o.dx, o.dy);
    }
    canvas.drawPath(
      path,
      Paint()
        ..color = Paper.borderStrong.withValues(alpha: 0.55 * p.dim.at(engine.time))
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.2,
    );
  }

  /// A flowing connection from one prop to another: an arc that draws
  /// itself out, dashes running along it and a few bright beads travelling
  /// from start to end. It follows both ends as they move.
  void _paintLink(Canvas canvas, Size size, StageProp p, double t) {
    final a = engine.props[p.on ?? ''], b = engine.props[p.to ?? ''];
    if (a == null || b == null) return;
    final ra = engine.propBounds(a), rb = engine.propBounds(b);
    if (ra == null || rb == null) return;
    var alpha = p.dim.at(t) * math.min(a.dim.at(t), b.dim.at(t));
    final dying = [p.diedAt, a.diedAt, b.diedAt].whereType<double>().fold<double?>(null, (m, v) => m == null ? v : math.min(m, v));
    if (dying != null) alpha *= 1 - ((t - dying) / 0.45).clamp(0.0, 1.0);
    if (alpha <= 0) return;
    Offset px(Offset f) => Offset(f.dx * size.width, f.dy * size.height);
    final from = px(ra.center), to = px(rb.center);
    final d = to - from;
    final len = d.distance;
    if (len < 8) return;
    final lift = math.min(90.0, len * 0.28 + 16);
    final ctrl = (from + to) / 2 - Offset(0, lift);
    final curve = Path()
      ..moveTo(from.dx, from.dy)
      ..quadraticBezierTo(ctrl.dx, ctrl.dy, to.dx, to.dy);
    final whole = curve.computeMetrics().firstOrNull;
    if (whole == null) return;
    final boxA = Rect.fromPoints(px(ra.topLeft), px(ra.bottomRight)).deflate(4);
    final boxB = Rect.fromPoints(px(rb.topLeft), px(rb.bottomRight)).deflate(4);
    var s0 = 0.0, s1 = whole.length;
    for (var i = 0; i <= 60; i++) {
      final at = whole.getTangentForOffset(whole.length * i / 60)?.position;
      if (at != null && boxA.contains(at)) s0 = whole.length * i / 60;
    }
    for (var i = 60; i >= 0; i--) {
      final at = whole.getTangentForOffset(whole.length * i / 60)?.position;
      if (at != null && boxB.contains(at)) s1 = whole.length * i / 60;
    }
    if (s1 - s0 < 12) {
      s0 = 0;
      s1 = whole.length;
    }
    final metric = whole.extractPath(s0 + 4, s1 - 4).computeMetrics().firstOrNull;
    if (metric == null) return;
    final col = propColor(p.color, Paper.accent);
    final grow = Curves.easeInOutCubic.transform(((t - p.bornAt) / 0.8).clamp(0.0, 1.0));
    final shown = metric.length * grow;
    canvas.drawPath(
      metric.extractPath(0, shown),
      Paint()
        ..color = col.withValues(alpha: 0.18 * alpha)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 7
        ..strokeCap = StrokeCap.round,
    );
    final dash = Paint()
      ..color = col.withValues(alpha: 0.8 * alpha)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.6
      ..strokeCap = StrokeCap.round;
    final shift = (t * 55) % 16;
    for (var s = shift - 16; s < shown; s += 16) {
      final a0 = math.max(0.0, s), a1 = math.min(shown, s + 8);
      if (a1 > a0) canvas.drawPath(metric.extractPath(a0, a1), dash);
    }
    if (grow >= 1) {
      for (var i = 0; i < 3; i++) {
        final f = (t * 0.45 + i / 3) % 1.0;
        final tan = metric.getTangentForOffset(metric.length * f);
        if (tan == null) continue;
        final glow = math.sin(math.pi * f);
        canvas.drawCircle(tan.position, 6 * glow + 1, Paint()..color = Colors.white.withValues(alpha: 0.9 * alpha * glow));
        canvas.drawCircle(tan.position, 3.6 * glow + 0.6, Paint()..color = col.withValues(alpha: alpha));
      }
      final end = metric.getTangentForOffset(metric.length - 1);
      if (end != null) _arrowHead(canvas, end.position, -end.angle, col.withValues(alpha: alpha));
    }
    if (p.label != null && grow > 0.6) {
      final mid = metric.getTangentForOffset(metric.length / 2);
      if (mid != null) _caption(canvas, p.label!, mid.position - const Offset(0, 14), alpha, t - p.shakeUntil);
    }
  }

  // ---------------------------------------------------------- instruments

  static String _clockText(double seconds) {
    final s = seconds.floor();
    return s < 60 ? '$s s' : '${s ~/ 60}:${(s % 60).toString().padLeft(2, '0')}';
  }

  /// Clocks tick (one step a second of their own time), a stopwatch counts
  /// tenths, a counter rolls, a gauge's needle swings, a bar and a
  /// thermometer fill -- each drawn standing on its base point (0, 0).
  void _paintInstrument(Canvas canvas, StageProp p, double half, double alpha, Offset dir, double t) {
    final ink = Paper.ink.withValues(alpha: alpha);
    final col = propColor(p.color, p.kind == PropKind.thermometer ? Paper.danger : const Color(0xFF4F7CAC));
    final tag = p.caption ?? (p.kind == PropKind.counter ? null : p.label);
    switch (p.kind) {
      case PropKind.clock || PropKind.stopwatch:
        final r = half * 1.05;
        final c = Offset(0, -r);
        final watch = p.kind == PropKind.stopwatch;
        if (watch) {
          canvas.drawRRect(
            RRect.fromRectAndRadius(Rect.fromCenter(center: c - Offset(0, r * 1.12), width: r * 0.34, height: r * 0.26),
                const Radius.circular(3)),
            Paint()..color = Color.lerp(col, Colors.black, 0.2)!.withValues(alpha: alpha),
          );
        }
        final face = Path()..addOval(Rect.fromCircle(center: c, radius: r));
        _extrude(canvas, face, col, dir, r * 0.2, alpha);
        canvas.drawCircle(c, r, Paint()..color = col.withValues(alpha: alpha));
        canvas.drawCircle(
          c,
          r * 0.86,
          Paint()
            ..shader = RadialGradient(
              center: const Alignment(-0.3, -0.35),
              colors: [Colors.white.withValues(alpha: alpha), const Color(0xFFF1EEE6).withValues(alpha: alpha)],
            ).createShader(Rect.fromCircle(center: c, radius: r)),
        );
        final tick = Paint()
          ..color = ink
          ..strokeCap = StrokeCap.round;
        for (var i = 0; i < 12; i++) {
          final a = i / 12 * 2 * math.pi;
          final u = Offset(math.sin(a), -math.cos(a));
          tick.strokeWidth = i % 3 == 0 ? 2.4 : 1.2;
          canvas.drawLine(c + u * r * (i % 3 == 0 ? 0.64 : 0.7), c + u * r * 0.78, tick);
        }
        final reading = p.reading;
        // the hand steps once per second of the clock's own time, with a
        // little overshoot as it lands -- a slow clock visibly ticks less
        final whole = reading.floorToDouble();
        final frac = reading - whole;
        final settle = math.max(0.0, 1 - frac * 6) * math.sin(frac * 6 * math.pi) * 0.12;
        final step = (whole + settle) / 12 * 2 * math.pi;
        final slow = reading / 144 * 2 * math.pi;
        Offset hand(double a, double len) => c + Offset(math.sin(a), -math.cos(a)) * len;
        if (!watch) {
          canvas.drawLine(c, hand(slow, r * 0.45), _stroke(ink, 3.4));
        }
        canvas.drawLine(c, hand(step, r * 0.72), _stroke(Paper.accent.withValues(alpha: alpha), 2.2));
        canvas.drawCircle(c, r * 0.07, Paint()..color = ink);
        _label(canvas, watch ? '${reading.toStringAsFixed(1)} s' : _clockText(reading), c + Offset(0, r * 0.42),
            r * 0.24, ink, alpha, bold: true, inline: true);
        if (tag != null) _caption(canvas, tag, const Offset(0, 14), alpha, t - p.shakeUntil);

      case PropKind.counter:
        final w = half * 2.6, h = half * 1.3;
        final box = RRect.fromRectAndRadius(Rect.fromLTWH(-w / 2, -h, w, h), Radius.circular(h * 0.18));
        _extrude(canvas, Path()..addRRect(box), const Color(0xFF2C3440), dir, h * 0.3, alpha);
        canvas.drawRRect(box, Paint()..color = const Color(0xFF2C3440).withValues(alpha: alpha));
        final screen = box.deflate(h * 0.12);
        canvas.drawRRect(screen, Paint()..color = const Color(0xFF12161C).withValues(alpha: alpha));
        final n = p.reading.floor();
        // the last digit rolls in
        final roll = Curves.easeOut.transform(((p.reading - n) * 5).clamp(0.0, 1.0));
        canvas.save();
        canvas.clipRRect(screen);
        final unit = p.label == null ? '' : ' ${p.label}';
        final digits = const Color(0xFF9EF0A8);
        _label(canvas, '$n$unit', Offset(0, -h / 2 + (1 - roll) * h * 0.35), h * 0.42, digits, alpha * roll, bold: true, inline: true);
        if (roll < 1) {
          _label(canvas, '${n - 1}$unit', Offset(0, -h / 2 - roll * h * 0.35), h * 0.42, digits, alpha * (1 - roll),
              bold: true, inline: true);
        }
        canvas.restore();
        if (tag != null) _caption(canvas, tag, const Offset(0, 14), alpha, t - p.shakeUntil);

      case PropKind.gauge:
        final rad = half * 1.3;
        final c = Offset(0, -half * 0.35);
        final dial = Path()
          ..moveTo(c.dx - rad, c.dy)
          ..arcTo(Rect.fromCircle(center: c, radius: rad), math.pi, math.pi, false)
          ..close();
        _extrude(canvas, dial, const Color(0xFFE8E2D6), dir, rad * 0.14, alpha);
        canvas.drawPath(dial, Paint()..color = Colors.white.withValues(alpha: alpha));
        canvas.drawPath(dial, _outline(const Color(0xFFBDB3A3), alpha));
        const bands = [Color(0xFF6D9A5C), Color(0xFFE2B33C), Color(0xFFD9481F)];
        for (var i = 0; i < 3; i++) {
          canvas.drawArc(
            Rect.fromCircle(center: c, radius: rad * 0.78),
            math.pi + i * math.pi / 3,
            math.pi / 3,
            false,
            Paint()
              ..color = bands[i].withValues(alpha: 0.85 * alpha)
              ..style = PaintingStyle.stroke
              ..strokeWidth = rad * 0.14,
          );
        }
        final a = math.pi + p.display.clamp(0.0, 1.0) * math.pi;
        final tip = c + Offset(math.cos(a), math.sin(a)) * rad * 0.82;
        canvas.drawLine(c, tip, _stroke(Paper.danger.withValues(alpha: alpha), 3));
        canvas.drawCircle(c, rad * 0.09, Paint()..color = ink);
        if (tag != null) _caption(canvas, tag, const Offset(0, 14), alpha, t - p.shakeUntil);

      case PropKind.bar || PropKind.thermometer:
        final thermo = p.kind == PropKind.thermometer;
        final w = thermo ? half * 0.42 : half * 0.95;
        final h = half * 2.6;
        final bulb = thermo ? w * 0.95 : 0.0;
        final top = -h;
        final tube = RRect.fromRectAndRadius(Rect.fromLTWH(-w / 2, top, w, h - bulb), Radius.circular(w / 2));
        final glass = Path()..addRRect(tube);
        if (thermo) glass.addOval(Rect.fromCircle(center: Offset(0, -bulb), radius: bulb));
        _extrude(canvas, glass, const Color(0xFFD5DCE4), dir, w * 0.25, alpha);
        canvas.drawPath(glass, Paint()..color = Colors.white.withValues(alpha: 0.95 * alpha));
        final level = p.display.clamp(0.0, 1.0);
        final fillTop = top + (h - bulb) * (1 - level);
        canvas.save();
        canvas.clipPath(glass);
        final fill = Path()..moveTo(-w, 0);
        for (var i = 0; i <= 12; i++) {
          final x = -w / 2 + w * i / 12;
          fill.lineTo(x, fillTop + (thermo ? 0 : math.sin(t * 4 + i * 0.9) * 1.6));
        }
        fill
          ..lineTo(w, 0)
          ..close();
        canvas.drawPath(fill, Paint()..color = col.withValues(alpha: 0.9 * alpha));
        if (thermo) canvas.drawCircle(Offset(0, -bulb), bulb * 0.8, Paint()..color = col.withValues(alpha: alpha));
        canvas.restore();
        canvas.drawPath(glass, _outline(const Color(0xFFBDB3A3), alpha));
        for (var i = 1; i < 5; i++) {
          final y = top + (h - bulb) * i / 5;
          canvas.drawLine(Offset(w / 2 + 2, y), Offset(w / 2 + 6, y), _stroke(Paper.faint.withValues(alpha: alpha), 1.2));
        }
        if (tag != null) _caption(canvas, tag, const Offset(0, 14), alpha, t - p.shakeUntil);

      default:
        break;
    }
  }

  /// Something cruising: streaks rushing past behind it (it faces right).
  void _paintSpeedLines(Canvas canvas, Offset o, double half, double speed, double t, String? color) {
    final col = propColor(color, Paper.body);
    for (var i = 0; i < 7; i++) {
      final y = o.dy - half * (0.3 + 1.5 * i / 5);
      final len = half * (1.2 + 2.6 * speed) * (0.6 + 0.4 * _hash(i + 300));
      final period = len + half * 2;
      final run = (t * (260 + 520 * speed) + _hash(i + 200) * period) % period;
      final x1 = o.dx - half * 0.8 - run;
      canvas.drawLine(
        Offset(x1, y),
        Offset(x1 - len * 0.6, y),
        Paint()
          ..strokeWidth = 3
          ..strokeCap = StrokeCap.round
          ..color = col.withValues(alpha: (0.35 + 0.5 * speed) * (1 - run / period)),
      );
    }
  }

  /// Two lanes on the floor while comparing: a line down the middle running
  /// back to the horizon, a faint tint either side, and each lane's name.
  void _paintLanes(Canvas canvas, Size size, double t) {
    final k = engine.compare.at(t);
    if (k <= 0.01) return;
    final horizon = StageEngine.horizonY * size.height;
    final vx = engine.vanishX * size.width;
    final mid = Offset(size.width / 2, size.height);
    final far = Offset(vx, horizon);
    canvas.drawPath(
      Path()
        ..moveTo(0, horizon)
        ..lineTo(far.dx, far.dy)
        ..lineTo(mid.dx, mid.dy)
        ..lineTo(0, size.height)
        ..close(),
      Paint()..color = const Color(0xFFF3C98B).withValues(alpha: 0.10 * k),
    );
    canvas.drawPath(
      Path()
        ..moveTo(size.width, horizon)
        ..lineTo(far.dx, far.dy)
        ..lineTo(mid.dx, mid.dy)
        ..lineTo(size.width, size.height)
        ..close(),
      Paint()..color = const Color(0xFF8BB8F3).withValues(alpha: 0.10 * k),
    );
    _dashed(canvas, mid, far, Paint()..color = Paper.borderStrong.withValues(alpha: 0.8 * k)..strokeWidth = 2);
    for (final (x, name) in [(0.25, engine.compareLeft), (0.75, engine.compareRight)]) {
      if (name == null) continue;
      final tp = TextPainter(
        text: TextSpan(
          text: name,
          style: TextStyle(fontSize: 14, fontWeight: FontWeight.w800, color: Paper.ink.withValues(alpha: k)),
        ),
        textDirection: TextDirection.ltr,
      )..layout(maxWidth: size.width * 0.4);
      final c = Offset(x * size.width, size.height * 0.08);
      final r = RRect.fromRectAndRadius(
        Rect.fromCenter(center: c, width: tp.width + 22, height: tp.height + 10),
        const Radius.circular(12),
      );
      canvas.drawRRect(r, Paint()..color = Colors.white.withValues(alpha: 0.9 * k));
      canvas.drawRRect(r, Paint()..color = Paper.border.withValues(alpha: k)..style = PaintingStyle.stroke);
      tp.paint(canvas, c - Offset(tp.width / 2, tp.height / 2));
    }
  }

  /// A little tag under an emoji, that flashes when it changes.
  void _caption(Canvas canvas, String text, Offset center, double alpha, double sinceChange) =>
      _later(canvas, (c) => _drawCaption(c, text, center, alpha, sinceChange));

  void _drawCaption(Canvas canvas, String text, Offset center, double alpha, double sinceChange) {
    final tp = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.w700, color: Paper.ink.withValues(alpha: alpha)),
      ),
      textDirection: TextDirection.ltr,
    )..layout(maxWidth: 150);
    final flash = sinceChange < 0 ? 1.0 : (1 - sinceChange / 0.6).clamp(0.0, 1.0);
    final r = RRect.fromRectAndRadius(
      Rect.fromCenter(center: center, width: tp.width + 14, height: tp.height + 6),
      const Radius.circular(8),
    );
    canvas.drawRRect(r, Paint()..color = Color.lerp(Colors.white, const Color(0xFFFFE9A8), flash)!.withValues(alpha: 0.95 * alpha));
    canvas.drawRRect(
      r,
      Paint()
        ..color = Paper.border.withValues(alpha: alpha)
        ..style = PaintingStyle.stroke,
    );
    tp.paint(canvas, center - Offset(tp.width / 2, tp.height / 2));
  }

  /// A ball lit from the upper left: a round gradient and a glint.
  void _sphere(Canvas canvas, Offset c, double r, Color col, double alpha) {
    final rect = Rect.fromCircle(center: c, radius: r);
    canvas.drawCircle(
      c,
      r,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(-0.35, -0.4),
          radius: 1.0,
          colors: [
            Color.lerp(col, Colors.white, 0.45)!.withValues(alpha: alpha),
            col.withValues(alpha: alpha),
            Color.lerp(col, Colors.black, 0.35)!.withValues(alpha: alpha),
          ],
          stops: const [0, 0.55, 1],
        ).createShader(rect),
    );
    canvas.drawOval(Rect.fromCenter(center: c + Offset(-r * 0.35, -r * 0.42), width: r * 0.5, height: r * 0.32),
        Paint()..color = Colors.white.withValues(alpha: 0.6 * alpha));
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

  /// A word on or beside a thing. Drawn last, ringed by a halo in the
  /// opposite tone so it reads over whatever is behind it. `inline`: a
  /// reading shown IN an instrument (a counter's screen, a clock face),
  /// drawn in place, under the instrument's own clip.
  void _label(Canvas canvas, String text, Offset center, double fontSize, Color color, double alpha,
      {bool bold = false, bool box = false, bool inline = false}) {
    TextPainter painter(TextStyle style) => TextPainter(
          text: TextSpan(text: text, style: style),
          textDirection: TextDirection.ltr,
          textAlign: TextAlign.center,
        )..layout(maxWidth: 160);
    final style = TextStyle(
      fontSize: fontSize.clamp(9.0, 22.0),
      fontWeight: bold || box ? FontWeight.w700 : FontWeight.w500,
    );
    final tp = painter(style.copyWith(color: color.withValues(alpha: alpha)));
    final at = center - Offset(tp.width / 2, tp.height / 2);
    if (inline) {
      tp.paint(canvas, at);
      return;
    }
    final light = color.computeLuminance() > 0.5;
    final halo = painter(style.copyWith(
      foreground: Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 3.2
        ..strokeJoin = StrokeJoin.round
        ..color = (light ? Paper.ink : Paper.surface).withValues(alpha: alpha * (light ? 0.55 : 0.92)),
    ));
    _later(canvas, (c) {
      halo.paint(c, at);
      tp.paint(c, at);
    });
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
