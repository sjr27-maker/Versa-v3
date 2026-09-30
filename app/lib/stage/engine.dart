import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/animation.dart';
import 'package:flutter/foundation.dart';

import 'formula.dart';
import 'script.dart';

/// Where something is going: a from→to over [dur] seconds starting at
/// [start], in one [MoveStyle]. `at(t)` is pure, so the painter can sample
/// it any frame without the engine stepping anything.
class Track {
  Track(this.from, this.to, this.start, this.dur, this.style, {this.arc = 0.2});
  Track.still(Offset p)
      : from = p,
        to = p,
        start = 0,
        dur = 0,
        style = MoveStyle.slide,
        arc = 0;

  final Offset from, to;
  final double start, dur;
  final MoveStyle style;

  /// Leap height as a fraction of stage height.
  final double arc;

  double progress(double t) => dur <= 0 ? 1 : ((t - start) / dur).clamp(0.0, 1.0);
  bool done(double t) => progress(t) >= 1;

  int get hops => math.max(1, ((to - from).distance / 0.07).round());

  /// How far off the ground (fraction of stage height) at time t.
  double lift(double t) {
    final p = progress(t);
    if (p >= 1 || p <= 0) return 0;
    return switch (style) {
      MoveStyle.slide => 0,
      MoveStyle.hop => (math.sin(math.pi * p * hops)).abs() * 0.035,
      MoveStyle.leap => math.sin(math.pi * p) * arc,
      MoveStyle.fall => 0,
    };
  }

  /// Ground position (no lift) at time t.
  Offset base(double t) {
    final p = progress(t);
    final eased = switch (style) {
      MoveStyle.slide => Curves.easeInOut.transform(p),
      MoveStyle.hop => p,
      MoveStyle.leap => Curves.easeInOutSine.transform(p),
      MoveStyle.fall => p * p,
    };
    return Offset.lerp(from, to, eased)!;
  }

  Offset at(double t) => base(t) - Offset(0, lift(t));

  /// Squash (-) / stretch (+) implied by the motion itself: stretched in the
  /// air, squashed at each touchdown of a hop.
  double motionSquash(double t) {
    final p = progress(t);
    if (p >= 1 || p <= 0) return 0;
    if (style == MoveStyle.hop) {
      final s = (math.sin(math.pi * p * hops)).abs();
      return 0.10 * s - 0.14 * math.pow(1 - s, 6);
    }
    if (style == MoveStyle.leap) return 0.12 * math.sin(math.pi * p);
    return 0;
  }
}

/// A number easing from one value to another -- a prop's scale or spin.
class Ramp {
  Ramp(this.from, this.to, this.start, this.dur, {this.curve = Curves.easeOutBack});
  Ramp.still(double v)
      : from = v,
        to = v,
        start = 0,
        dur = 0,
        curve = Curves.linear;
  final double from, to, start, dur;
  final Curve curve;

  double at(double t) {
    if (dur <= 0) return to;
    final p = ((t - start) / dur).clamp(0.0, 1.0);
    return from + (to - from) * curve.transform(p);
  }
}

class StageProp {
  StageProp({
    required this.id,
    required this.kind,
    required this.pos,
    required this.bornAt,
    this.headDelta,
    this.label,
    this.caption,
    this.to,
    this.enter,
    this.track,
    double rate = 1,
    double value = 0.3,
    this.size = 1,
    this.color,
    this.w,
    this.h,
    this.points = const [],
    this.amp = 0.04,
    this.cycles = 3,
    this.fn,
    this.on,
    double at = 0,
    this.xmin = -5,
    this.xmax = 5,
    this.ymin = -5,
    this.ymax = 5,
    this.tex,
    this.z = 0,
    this.lift = 0,
    this.spinRate = 0,
    double yaw = 0,
    double pitch = 0,
  })  : homeZ = z,
        rate = Ramp.still(rate),
        value = Ramp.still(value),
        display = value,
        at = Ramp.still(at),
        yaw = Ramp.still(yaw),
        pitch = Ramp.still(pitch);
  final String id;
  final PropKind kind;
  Track pos;

  /// Graph kit (see SpawnAction): a plot's formula, what this sits on, a
  /// dot's x (it slides), the axes' ranges, and a typeset label.
  final String? fn, on;
  late final Formula? formula = parseFormula(fn);
  Ramp at;
  final double xmin, xmax, ymin, ymax;
  final String? tex;

  /// 3D solids: depth (0 front .. 1 far), height above the floor, steady spin
  /// (turns/s), and yaw/pitch in turns (ramped by TurnAction).
  double z;
  final double lift, spinRate;

  /// Stepped back by a SceneAction: depth and brightness ease to [z]'s new
  /// value / dimmer, and back when the prop is used again. [homeZ] is where
  /// it was put.
  Ramp? depth;
  final double homeZ;
  Ramp dim = Ramp.still(1);
  bool get receded => dim.to < 1;
  Ramp yaw, pitch;

  /// Orbiting another prop (OrbitAction): which, how far, and the angle in turns.
  String? around;
  double orbitRadius = 0;
  Ramp orbitPhase = Ramp.still(0);
  final double bornAt;

  /// Arrow/line/wave: the far end relative to (x, y) (it travels with it).
  final Offset? headDelta;
  String? label;

  /// Words under an emoji (its label is the glyph).
  String? caption;

  /// Link: where it flows to. Entrance style (null = picked from the id).
  final String? to, enter;

  /// Instruments: what it is bound to, its rate (ticks/counts per second)
  /// and set reading (0..1); [reading] is the elapsed time or count so far,
  /// [display] the needle/fill shown right now.
  final String? track;
  Ramp rate, value;
  double reading = 0, display;

  /// Cruising speed (CruiseAction), and how fast it is going right now
  /// (0..0.99, from its cruise or its actual motion).
  Ramp cruise = Ramp.still(0);
  double speedNow = 0;
  final double size;
  String? color;
  final double? w, h;

  /// Path only: points relative to (x, y).
  final List<Offset> points;
  final double amp, cycles;

  Ramp scale = Ramp.still(1);

  /// In turns (1 = a full rotation).
  Ramp spin = Ramp.still(0);

  /// Riding on the blob's head (see CarryAction).
  bool carried = false;
  double? diedAt;
  double shakeUntil = 0;
}

/// One particle of an effect.
class Particle {
  Particle({
    required this.kind,
    required this.pos,
    required this.vel,
    required this.born,
    required this.life,
    required this.size,
    this.gravity = 0,
    this.hue = 0,
  });
  final EffectKind kind;
  Offset pos, vel; // stage fractions, per second
  final double born, life, size, gravity;

  /// 0..1: which of the effect's colours this particle takes.
  final double hue;
}

class _Emitter {
  _Emitter(this.kind, this.at, this.until);
  final EffectKind kind;
  final Offset? at; // null = follows the blob
  final double until;
  double carry = 0;
}

/// What the blob is currently asking. [choices] render as bubbles beneath it.
class StageQuestion {
  const StageQuestion({required this.key, required this.text, required this.choices});

  /// Identity of the thing asked (a chat message id, or a skit step), so a
  /// caller can tell "same question still up" from "a new one".
  final String key;
  final String text;
  final List<StageChoice> choices;
}

class _Sleeper {
  _Sleeper(this.until, this.done);
  final double until;
  final Completer<void> done;
}

/// The character engine: plays [StageAction] scripts on a blob and its props.
///
/// Time is the engine's own clock, advanced only by [tick] (the view drives
/// it from a Ticker; tests drive it by hand), so a skit's timing is exact and
/// pauses when the stage isn't on screen.
class StageEngine extends ChangeNotifier {
  StageEngine({int seed = 7}) : _rng = math.Random(seed);

  bool _disposed = false;

  /// The picture the learner sent with the message this performance is
  /// about (picture.dart): what a `photo` prop shows. Null: the prop is an
  /// empty frame.
  Uint8List? photo;

  @override
  void dispose() {
    stop();
    _disposed = true;
    super.dispose();
  }

  /// A performance's runner can outlive the panel that owned the engine
  /// (the stage closed mid-skit); its last update then goes nowhere.
  @override
  void notifyListeners() {
    if (!_disposed) super.notifyListeners();
  }

  final math.Random _rng;

  /// Seconds since the engine started.
  double time = 0;

  /// The stage's size in pixels (set by the view on layout) -- needed only to
  /// turn pixel-sized things (the blob's width, a box's width) into stage
  /// fractions, e.g. where to stand to push a box.
  Size size = const Size(600, 400);

  // ------------------------------------------------------------- the blob
  Track blob = Track.still(const Offset(0.3, kGroundY));
  Mood mood = Mood.neutral;

  /// Mouth flapping. Nothing sets it today: while the chat writes, the slime
  /// watches the chat instead ([watchingChat]).
  bool talking = false;

  /// The chat is working (thinking or writing the answer): between skits the
  /// slime keeps its eyes on it, like reading along.
  bool watchingChat = false;

  /// Leaning into something (-1 left .. 1 right); eased toward [_leanTarget].
  double lean = 0;
  double _leanTarget = 0;

  /// Spring-driven squash on top of the motion's own: + stretch, - squash.
  double _squash = 0, _squashV = 0;

  String? _lookTarget;
  double? _lookX;
  double _glanceUntil = 0;
  double? _glanceX;

  double _nextBlink = 2.5;
  double _blinkStart = -1;

  String? speech;
  double _speechUntil = 0;

  StageQuestion? question;
  Completer<String?>? _answer;

  final Map<String, StageProp> props = {};
  final List<Particle> particles = [];
  final List<_Emitter> _emitters = [];

  /// An emoji on the blob's head (WearAction), or null.
  String? hat;

  // ------------------------------------------------ pace + the chat's answer

  /// How fast skits play: < 1 slower, > 1 faster. Scales every wait, move
  /// and speech bubble. Fixed at a little slower than the original pace (the
  /// user's call, 2026-09-27); tests set 1 to keep their timings exact.
  double speed = 0.8;

  /// A takeaway to pin below the stage.
  void Function(String text)? onNote;

  /// The student answered a skit's quick check: (question, choices, the id
  /// picked, the id marked right or null) -- the panel keeps it.
  void Function(String question, List<StageChoice> choices, String picked, String? answer)? onChecked;

  /// When a finished performance tidies up: the props fade, nothing is left
  /// frozen mid-reaction (the "stuck" stage, 2026-09-27).
  double? _settleAt;
  static const settleAfter = 3.5;

  bool get running => _running > 0;
  int _running = 0;
  int _gen = 0;
  final List<_Sleeper> _sleepers = [];

  // Live performance (see [beginLive]): actions arriving over the wire.
  final List<StageAction> _live = [];

  /// Played at the front of the next live performance ([recalled]).
  List<StageAction> _intro = const [];

  /// The recall gag is playing and this turn's performance should JOIN it
  /// (queue after it) rather than reset the stage -- until [_awaitDeadline].
  bool _awaitingPerformance = false;
  double _awaitDeadline = 0;
  bool _liveOpen = false;
  Completer<void>? _liveWake;
  double _nextIdle = 4;

  // ------------------------------------------------------------ geometry

  /// Blob radius in pixels: scales with the stage, within sane bounds.
  double get blobRadius => (size.height * 0.11).clamp(22.0, 64.0);

  /// Half-width of a prop in pixels.
  double propHalf(StageProp p) => switch (p.kind) {
        PropKind.box => (size.height * 0.075).clamp(16.0, 48.0) * p.size,
        PropKind.ball => (size.height * 0.05).clamp(12.0, 34.0) * p.size,
        PropKind.clock || PropKind.stopwatch || PropKind.counter || PropKind.gauge || PropKind.bar ||
            PropKind.thermometer =>
          (size.height * 0.075).clamp(18.0, 50.0) * p.size,
        _ => (size.height * 0.05).clamp(12.0, 34.0) * p.size,
      };

  double _pxToX(double px) => size.width <= 0 ? 0 : px / size.width;
  double _pxToY(double px) => size.height <= 0 ? 0 : px / size.height;

  /// The blob's height in pixels right now (the painter draws it this tall).
  double get blobHeight => blobRadius * 1.9 * (1 + blobSquash + math.sin(time * 2.6) * 0.025);

  /// Where the top of the blob's head is, in stage fractions.
  Offset get blobTop => blobPos - Offset(0, _pxToY(blobHeight));

  /// A prop's base point right now -- on the blob's head while carried, on
  /// its curve for a graph dot.
  Offset propAt(StageProp p) {
    if (p.kind == PropKind.dot || p.kind == PropKind.tangent) {
      final d = dotPoint(p.kind == PropKind.dot ? p : parentOf(p));
      if (d != null) return d;
    }
    if (p.carried) return blobTop + Offset(0, _pxToY(4));
    final host = hostOf(p);
    if (host != null) {
      final b = propBounds(host);
      if (b != null) return Offset(b.center.dx, b.top - _pxToY(12));
    }
    if (isSolidKind(p.kind)) return solidPlace(p).$1;
    return project(p.pos.at(time), p.z).$1;
  }

  /// How large a prop looks at its depth (1 at the front).
  /// The thing a label is attached to ("on"), for a text or formula.
  StageProp? hostOf(StageProp p) {
    if (p.on == null || (p.kind != PropKind.text && p.kind != PropKind.math)) return null;
    final host = props[p.on];
    // one level only: a label on a label is placed where it was put
    if (host == null || host.on != null && (host.kind == PropKind.text || host.kind == PropKind.math)) return null;
    return host;
  }

  double propScale(StageProp p) {
    final host = hostOf(p);
    if (host != null) return propScale(host);
    if (p.carried || isGraphKind(p.kind)) return 1;
    if (isSolidKind(p.kind)) return solidPlace(p).$2;
    return project(p.pos.at(time), p.z).$2;
  }

  // ------------------------------------------------------------- compare

  /// Two lanes, left and right, while comparing (CompareAction).
  String? compareLeft, compareRight;
  Ramp compare = Ramp.still(0);

  // ------------------------------------------------------------ 3D world

  /// 0 = the flat stage, 1 = the 3D space (eased between by WorldAction).
  /// The stage IS a room by default (2026-09-28: "by 3d space I expected it
  /// to work for everything"); "flat" is still there for a plain board.
  Ramp world = Ramp.still(1);
  double get worldT => world.at(time).clamp(0.0, 1.0);

  static const horizonY = 0.30;

  /// Perspective at depth z: 1 at the front, smaller toward the horizon.
  static double perspective(double z) => 1 / (1 + 1.8 * z.clamp(0.0, 1.0));

  /// Where the floor's lines meet: the camera drifts a little from side to
  /// side, so the room lives. The front plane (z = 0: the slime, the graphs,
  /// the formulas) never moves -- only depth does, as parallax.
  double get vanishX => 0.5 + math.sin(time * 0.21) * 0.035 * worldT;

  /// A point at depth [z] -> where it shows on the stage, and how large.
  /// Its height above the floor is kept (shrunk with distance).
  (Offset, double) project(Offset at, double z) {
    final t = worldT;
    if (z <= 0 || t <= 0) return (at, 1);
    final k = perspective(z);
    final vx = vanishX;
    final deep = Offset(vx + (at.dx - vx) * k, horizonY + (kGroundY - horizonY) * k - (kGroundY - at.dy) * k);
    return (Offset.lerp(at, deep, t)!, 1 + (k - 1) * t);
  }

  /// Where a solid stands right now -- its contact point on the floor, in stage
  /// fractions -- how large it looks (1 = its own size), and its depth (for
  /// back-to-front drawing). Flat and 3D placements are eased by [worldT].
  (Offset, double, double) solidPlace(StageProp p, {double? phase}) {
    var x = p.pos.at(time).dx, z = p.z, lift = p.lift;
    final c = p.around != null ? props[p.around] : null;
    var behind = 0.0;
    if (c != null) {
      final a = 2 * math.pi * (phase ?? p.orbitPhase.at(time));
      x = c.pos.at(time).dx + math.cos(a) * p.orbitRadius;
      z = c.z + math.sin(a) * p.orbitRadius * 1.6;
      lift = c.lift;
      behind = math.sin(a) * p.orbitRadius;
    }
    final t = worldT;
    // flat: an orbit reads as an ellipse, the far side a little higher
    final flat = Offset(x, kGroundY - lift - behind * 0.35);
    final k = perspective(z);
    final vx = vanishX;
    final deep = Offset(vx + (x - vx) * k, horizonY + (kGroundY - horizonY) * k - lift * k);
    final scale = (1 - t) * (1 - behind * 0.6) + t * k;
    return (Offset.lerp(flat, deep, t)!, scale.clamp(0.2, 2.0), t > 0 ? z : behind);
  }

  // ---------------------------------------------------------- instruments

  /// Stage fractions per second that count as "the fastest" (speed 1).
  static const topSpeed = 1.2;

  /// What each id stands for, from the script's plan.
  final Map<String, String> cast = {};

  /// Speeds first (everything a tracker might read), then instruments: a
  /// clock ticks at its rate -- slowed by sqrt(1 - v^2) when it tracks a
  /// thing moving at v (time dilation, shown not told) -- a counter counts or
  /// adds up distance, a gauge/bar/thermometer shows its setting or the
  /// tracked thing's speed.
  void _stepInstruments(double dt) {
    if (dt <= 0) return;
    for (final p in props.values) {
      final cruising = p.cruise.at(time);
      var raw = cruising;
      if (raw < 0.01) {
        final moved = (p.pos.at(time) - p.pos.at(time - dt)).distance / dt / topSpeed;
        raw = moved.isFinite ? moved : 0;
      }
      p.speedNow += (raw.clamp(0.0, 0.99) - p.speedNow) * math.min(1, dt * 6);
    }
    for (final p in props.values) {
      if (!isInstrumentKind(p.kind)) continue;
      final tracked = p.track == null ? null : props[p.track];
      final v = tracked?.speedNow ?? 0;
      final r = p.rate.at(time);
      switch (p.kind) {
        case PropKind.clock || PropKind.stopwatch:
          final slow = tracked == null ? 1.0 : math.sqrt(1 - math.min(v, 0.97) * math.min(v, 0.97));
          p.reading += r * slow * dt;
        case PropKind.counter:
          p.reading += tracked == null ? r * dt : v * topSpeed * r * 10 * dt;
        default:
          final target = tracked == null ? p.value.at(time) : v;
          p.display += (target - p.display) * math.min(1, dt * 5);
      }
    }
  }

  // --------------------------------------------------------------- camera

  /// A gentle camera (2026-09-28: "more dynamicity while retaining
  /// smoothness"): during a performance it eases in a little on the thing
  /// the slime is watching, and back out when it asks or is done. [camFocus]
  /// is in stage fractions; StageView turns both into a transform.
  double camZoom = 1;
  Offset camFocus = const Offset(0.5, 0.5);
  static const camMaxZoom = 1.09;

  void _stepCamera(double dt) {
    var zoom = 1.0;
    var focus = const Offset(0.5, 0.5);
    final p = _lookTarget == null ? null : props[_lookTarget];
    if (running && question == null && p != null && p.diedAt == null && !isGraphKind(p.kind)) {
      final b = propBounds(p);
      if (b != null && b.width < 0.45 && b.height < 0.45) {
        zoom = camMaxZoom;
        focus = b.center;
      }
    }
    // slow and critically damped: never a jolt
    final k = 1 - math.exp(-dt * 1.4);
    camZoom += (zoom - camZoom) * k;
    // the focus may only pull the view so far that the edges stay covered
    final room = (1 - 1 / camZoom) / 2;
    final clamped = Offset(
      focus.dx.clamp(0.5 - room, 0.5 + room),
      focus.dy.clamp(0.5 - room, 0.5 + room),
    );
    camFocus = Offset.lerp(camFocus, clamped, k)!;
  }

  // ------------------------------------------------------------ graph kit

  StageProp? parentOf(StageProp? p) => p?.on == null ? null : props[p!.on];

  /// The axes a graph prop ultimately sits on.
  StageProp? axesOf(StageProp? p) {
    for (var i = 0; p != null && i < 4; i++) {
      if (p.kind == PropKind.axes) return p;
      p = parentOf(p);
    }
    return null;
  }

  /// The plot under a dot or a tangent (or the plot itself).
  StageProp? plotOf(StageProp? p) {
    for (var i = 0; p != null && i < 3; i++) {
      if (p.kind == PropKind.plot) return p;
      p = parentOf(p);
    }
    return null;
  }

  /// A point in an axes' own numbers -> stage fractions. An axes' (x, y) is
  /// its bottom-left corner; w is a fraction of the stage's width, h of its
  /// height.
  Offset graphToStage(StageProp axes, double gx, double gy) {
    final base = axes.pos.at(time);
    final w = axes.w ?? 0.5, h = axes.h ?? 0.4;
    return Offset(
      base.dx + (gx - axes.xmin) / (axes.xmax - axes.xmin) * w,
      base.dy - (gy - axes.ymin) / (axes.ymax - axes.ymin) * h,
    );
  }

  /// A dot's place on its curve right now (null if it can't be drawn).
  Offset? dotPoint(StageProp? dot) {
    if (dot == null || dot.kind != PropKind.dot) return null;
    final plot = plotOf(parentOf(dot));
    final axes = axesOf(dot);
    final f = plot?.formula;
    if (axes == null || f == null) return null;
    final x = dot.at.at(time);
    final y = f(x);
    if (!y.isFinite) return null;
    return graphToStage(axes, x, y);
  }

  /// The slope under a dot or a tangent right now.
  double? slopeUnder(StageProp p) {
    final dot = p.kind == PropKind.dot ? p : parentOf(p);
    final f = plotOf(dot)?.formula;
    if (dot == null || f == null) return null;
    final s = slopeAt(f, dot.at.at(time));
    return s.isFinite ? s : null;
  }

  /// When a graph prop fades: its own removal, or anything it sits on.
  double? fadingSince(StageProp? p) {
    double? since;
    for (var i = 0; p != null && i < 4; i++) {
      final d = p.diedAt;
      if (d != null && (since == null || d < since)) since = d;
      p = parentOf(p);
    }
    return since;
  }

  // ------------------------------------------------------------- queries

  Offset get blobPos => blob.at(time);

  /// + stretch / - squash, motion and spring combined.
  double get blobSquash => (blob.motionSquash(time) + _squash).clamp(-0.35, 0.35);

  /// 0 open .. 1 shut.
  double get blink {
    if (_blinkStart < 0) return 0;
    final p = (time - _blinkStart) / 0.16;
    if (p < 0 || p > 1) return 0;
    return math.sin(math.pi * p);
  }

  /// Where the eyes point, in stage fractions (null = straight ahead).
  double? get lookX {
    if (time < _glanceUntil && _glanceX != null) return _glanceX;
    // the chat sits to the stage's right: read along with it
    if (watchingChat && !running && _lookTarget == null) return 1.0;
    if (_lookTarget != null) {
      final p = props[_lookTarget];
      if (p != null) return propAt(p).dx;
    }
    return _lookX;
  }

  bool get asking => question != null;
  String? get bubbleText => question?.text ?? (time < _speechUntil ? speech : null);

  // ---------------------------------------------------------------- clock

  void tick(double dt) {
    dt = dt.clamp(0.0, 0.05);
    time += dt;

    // Landing/impulse spring.
    const k = 260.0, c = 13.0;
    final a = -k * _squash - c * _squashV;
    _squashV += a * dt;
    _squash += _squashV * dt;

    lean += (_leanTarget - lean) * math.min(1, dt * 10);

    if (time >= _nextBlink) {
      _blinkStart = time;
      _nextBlink = time + 2.2 + _rng.nextDouble() * 3.2;
      // Occasionally a double blink.
      if (_rng.nextDouble() < 0.2) _nextBlink = time + 0.3;
    }

    if (_sleepers.isNotEmpty) {
      final due = _sleepers.where((s) => s.until <= time).toList();
      for (final s in due) {
        _sleepers.remove(s);
        s.done.complete();
      }
    }

    for (final p in props.values) {
      final d = p.depth;
      if (d != null) {
        p.z = d.at(time);
        if (time >= d.start + d.dur) p.depth = null;
      }
    }
    props.removeWhere((_, p) {
      final since = isGraphKind(p.kind) ? fadingSince(p) : p.diedAt;
      return since != null && time - since > 0.6;
    });
    _stepParticles(dt);
    _stepCamera(dt);
    _stepInstruments(dt);

    if (_awaitingPerformance && time >= _awaitDeadline) {
      // No performance followed the gag (e.g. the answer failed).
      _awaitingPerformance = false;
      endLive();
    }

    if (!running && question == null && _settleAt != null && time >= _settleAt!) {
      _settleAt = null;
      for (final p in props.values) {
        p.diedAt ??= time;
      }
      _emitters.clear();
    }

    if (!running && question == null && time >= _nextIdle) _idleFidget();

    notifyListeners();
  }

  /// Signs of life between skits: glance somewhere, or a little bounce.
  void _idleFidget() {
    _nextIdle = time + 3.5 + _rng.nextDouble() * 4;
    if (_rng.nextBool()) {
      _glanceX = _rng.nextDouble();
      _glanceUntil = time + 1.1;
    } else {
      impulse(0.14);
    }
  }

  void impulse(double amount) => _squashV += amount * 14;

  /// Waits [seconds] of skit time -- stretched or shrunk by [speed].
  Future<void> _sleep(double seconds) => _sleepRaw(seconds / speed);

  Future<void> _sleepRaw(double seconds) {
    if (seconds <= 0) return Future.value();
    final c = Completer<void>();
    _sleepers.add(_Sleeper(time + seconds, c));
    return c.future;
  }

  // -------------------------------------------------------------- control

  /// Stop whatever skit is playing (props stay where they are).
  void stop() {
    _gen++;
    for (final s in _sleepers) {
      s.done.complete();
    }
    _sleepers.clear();
    _live.clear();
    _liveOpen = false;
    _awaitingPerformance = false;
    _wakeLive();
    if (_answer != null && !_answer!.isCompleted) _answer!.complete(null);
    _answer = null;
    question = null;
    speech = null;
    _leanTarget = 0;
    notifyListeners();
  }

  /// Clear the stage: stop, poof every prop, walk home.
  void reset() {
    stop();
    _settleAt = null;
    world = Ramp.still(1);
    for (final p in props.values) {
      p.diedAt ??= time;
    }
    _emitters.clear();
    hat = null;
    compare = Ramp(compare.at(time), 0, time, 0.5);
    cast.clear();
    mood = Mood.neutral;
    _lookTarget = null;
    _lookX = null;
    notifyListeners();
  }

  /// Play a script start to finish. Starting another script (or [stop])
  /// abandons this one at its next step.
  Future<void> play(List<StageAction> script) async {
    stop();
    final gen = _gen;
    _running++;
    try {
      await _runAll(script, gen);
    } finally {
      _running--;
      if (gen == _gen) _settle();
      notifyListeners();
    }
  }

  /// A performance ended: back to a calm face at once (never frozen confused
  /// or sad), and the props fade a moment later -- unless something new
  /// starts first.
  void _settle() {
    _leanTarget = 0;
    if (const {Mood.confused, Mood.sad, Mood.strain, Mood.surprised, Mood.thinking}.contains(mood)) {
      mood = Mood.happy;
      impulse(0.15);
    }
    _settleAt = time + settleAfter;
  }

  Future<void> _runAll(List<StageAction> actions, int gen) async {
    for (final a in actions) {
      if (gen != _gen) return;
      await _perform(a, gen);
    }
  }

  // ------------------------------------------------------ live (streamed)

  /// A new performance is starting (the server's `stage_start`): clear the
  /// stage and play actions as [enqueue] delivers them, in order, each the
  /// moment the previous one finishes -- so the slime is already moving
  /// while the rest of the script is still being written.
  void beginLive() {
    if (_awaitingPerformance && _liveOpen) {
      // The "I remember" gag is on; the performance lines up behind it.
      _awaitingPerformance = false;
      return;
    }
    reset();
    final gen = _gen;
    _live.addAll(_intro);
    _intro = const [];
    _liveOpen = true;
    _running++;
    notifyListeners();
    () async {
      try {
        while (gen == _gen) {
          if (_live.isNotEmpty) {
            await _perform(_live.removeAt(0), gen);
          } else if (!_liveOpen) {
            break;
          } else {
            _liveWake = Completer<void>();
            await _liveWake!.future;
          }
        }
      } finally {
        _running--;
        if (gen == _gen) _settle();
        notifyListeners();
      }
    }();
  }

  /// One action of the live performance. A skit may end with ONE quick
  /// check (`ask`): it waits for the pick, and the slime reacts to it. (The
  /// chat's own ambiguity options still take the stage over via [ask].)
  void enqueue(StageAction action) {
    if (!_liveOpen) return;
    _live.add(action);
    _wakeLive();
  }

  /// Memory knew what the student meant (the server's "recalled"): the
  /// slime's "oh wait, I remember!" beat. It leads the performance that
  /// follows (the answer starts right after) -- or plays by itself if no
  /// performance comes.
  void recalled({required bool retracted}) {
    final lines = retracted
        ? const [
            'Oh wait! I remember what you meant!',
            'Scratch those. I KNOW this one!',
            'Hold on... we have been here before!',
          ]
        : const [
            'Déjà vu! You asked me this before.',
            'Wait, I remember this one!',
            'Oh! I know what you mean this time.',
          ];
    final line = lines[_rng.nextInt(lines.length)];
    final at = blobTop;
    final bulbY = (at.dy - 0.08).clamp(0.05, kGroundY);
    final gag = parseScript([
      {
        'do': 'together',
        'actions': [
          {'do': 'emote', 'mood': 'surprised'},
          {'do': 'shake', 'target': 'blob', 'ms': 350},
        ],
      },
      if (retracted) {'do': 'effect', 'kind': 'smoke', 'x': at.dx, 'y': kGroundY + 0.08},
      {'do': 'spawn', 'id': '_bulb', 'kind': 'emoji', 'label': '💡', 'x': at.dx, 'y': bulbY, 'size': 0.8},
      {'do': 'effect', 'kind': 'sparks', 'x': at.dx, 'y': bulbY - 0.04},
      {'do': 'emote', 'mood': 'excited'},
      {'do': 'say', 'text': line, 'ms': 1500},
      {'do': 'jump'},
      {'do': 'remove', 'id': '_bulb'},
      {'do': 'emote', 'mood': 'happy'},
    ]);
    if (_liveOpen && running && !_awaitingPerformance) {
      // The performance is already under way (2026-09-28: resetting here
      // threw away its first beats -- the plan, the spawns -- and a repeated
      // question then showed nothing): the gag plays next, the show goes on.
      _live.insertAll(0, gag);
      _wakeLive();
      return;
    }
    _intro = gag;
    _awaitingPerformance = false;
    beginLive();
    _awaitingPerformance = true;
    _awaitDeadline = time + 8;
  }

  /// No more actions are coming (`stage_end`); whatever is queued still plays.
  void endLive() {
    _liveOpen = false;
    _wakeLive();
  }

  void _wakeLive() {
    final w = _liveWake;
    _liveWake = null;
    if (w != null && !w.isCompleted) w.complete();
  }

  // ------------------------------------------------ the ask (chat-driven)

  /// Put a question up from outside a script (the chat's ambiguity options).
  /// Any playing skit stops: the conversation outranks the performance.
  void ask(StageQuestion q) {
    if (question?.key == q.key) return;
    if (running) stop();
    question = q;
    mood = Mood.confused;
    _lookTarget = null;
    _lookX = null;
    impulse(0.2);
    notifyListeners();
  }

  /// The person picked [choiceId]: take the question down, react, and hand
  /// the choice to whichever script was waiting on it (if any).
  void answer(String choiceId) {
    if (question == null) return;
    question = null;
    mood = Mood.happy;
    impulse(0.3);
    final waiting = _answer;
    _answer = null;
    if (waiting != null && !waiting.isCompleted) waiting.complete(choiceId);
    notifyListeners();
  }

  /// The question went away without an answer from the stage (typed past it
  /// in the chat, or the chat changed).
  void withdrawQuestion() {
    if (question == null) return;
    question = null;
    mood = Mood.neutral;
    notifyListeners();
  }

  // ------------------------------------------------------------- actions

  String? _targetOf(StageAction a) => switch (a) {
        MoveAction(:final target) => target,
        SetAction(:final target) => target,
        CruiseAction(:final target) => target,
        ApproachAction(:final target) => target,
        PushAction(:final target) => target,
        ShakeAction(:final target) => target,
        ScaleAction(:final target) => target,
        SpinAction(:final target) => target,
        RecolorAction(:final target) => target,
        RelabelAction(:final target) => target,
        CarryAction(:final target) => target,
        ThrowAction(:final target) => target,
        LookAction(:final target) => target,
        TurnAction(:final target) => target,
        OrbitAction(:final target) => target,
        SlideAction(:final target) => target,
        _ => null,
      };

  /// A prop that stepped back comes forward again -- with what it sits on or
  /// circles, so a curve returns with its axes.
  void _bringForward(String? id) {
    void forward(StageProp p) {
      if (!p.receded) return;
      p.dim = Ramp(p.dim.at(time), 1, time, 0.6, curve: Curves.easeOut);
      p.depth = Ramp(p.z, p.homeZ, time, 0.6, curve: Curves.easeOutCubic);
    }

    var p = id == null ? null : props[id];
    for (var i = 0; p != null && i < 4; i++) {
      forward(p);
      final host = p;
      for (final q in props.values) {
        if (hostOf(q) == host) forward(q);
      }
      p = props[p.on ?? p.around ?? ''];
    }
  }

  Future<void> _perform(StageAction a, int gen) async {
    _bringForward(_targetOf(a));
    if (a is OrbitAction) _bringForward(a.around);
    switch (a) {
      case SpawnAction():
        final tail = Offset(a.x, a.y);
        props[a.id] = StageProp(
          id: a.id,
          kind: a.kind,
          pos: Track.still(tail),
          bornAt: time,
          headDelta: switch (a.kind) {
            PropKind.arrow || PropKind.line || PropKind.wave =>
              Offset(a.x2 ?? a.x + 0.2, a.y2 ?? a.y) - tail,
            _ => null,
          },
          label: a.label,
          caption: a.caption,
          to: a.to,
          enter: a.enter,
          track: a.track,
          rate: a.rate ?? 1,
          value: a.value ?? 0.3,
          size: a.size,
          color: a.color,
          w: a.w,
          h: a.h,
          points: [for (final pt in a.points) pt - tail],
          amp: a.amp,
          cycles: a.cycles,
          fn: a.fn,
          on: a.on,
          at: a.at ?? 0,
          xmin: a.xmin,
          xmax: a.xmax,
          ymin: a.ymin,
          ymax: a.ymax,
          tex: a.tex,
          z: a.z,
          lift: a.lift,
          spinRate: a.spin,
          yaw: a.yaw,
          pitch: a.pitch,
        );
        // The blob conjures it: a hop, a sparkle where it lands, eyes on it --
        // and if it appeared where the slime stands, the slime hops aside
        // rather than hiding it (2026-09-28).
        final made = props[a.id]!;
        final named = cast[a.id];
        if (named != null) {
          if ((made.kind == PropKind.emoji || isInstrumentKind(made.kind)) && made.caption == null) {
            made.caption = named;
          } else if (isSolidKind(made.kind) && made.label == null) {
            made.label = named;
          }
        }
        _lookTarget = a.id;
        if (!isGraphKind(a.kind) || a.kind == PropKind.axes) {
          final b = propBounds(made);
          if (b != null) _burst(EffectKind.sparks, Offset(b.center.dx, b.bottom), 8);
        }
        notifyListeners();
        if (!await _stepAside(made, gen)) {
          final here = blob.base(time);
          blob = Track(here, here, time, 0.32, MoveStyle.leap, arc: 0.05);
          await _sleep(0.35);
          impulse(-0.15);
        }

      case MoveAction():
        final seconds = a.ms / 1000;
        if (a.target == 'blob') {
          final from = blob.base(time);
          blob = Track(from, Offset(a.x, a.y ?? kGroundY), time, seconds, a.style);
          await _sleep(seconds);
          impulse(-0.22);
        } else {
          final p = props[a.target];
          if (p == null) return;
          final from = p.carried ? propAt(p) : p.pos.base(time);
          p.carried = false;
          p.pos = Track(from, Offset(a.x, a.y ?? from.dy), time, seconds, a.style);
          _lookTarget = p.id; // it watches the thing move
          await _sleep(seconds);
          await _stepAside(p, gen);
        }

      case ApproachAction():
        final p = props[a.target];
        if (p == null) return;
        final propX = p.pos.base(time).dx;
        await _walkBeside(p, blob.base(time).dx <= propX ? 1.0 : -1.0);
        impulse(-0.2);

      case PushAction():
        await _push(a, gen);

      case EmoteAction():
        mood = a.mood;
        if (a.mood == Mood.surprised || a.mood == Mood.excited) impulse(0.3);
        notifyListeners();

      case SayAction():
        speech = a.text;
        // long enough to READ, whatever the script asked for
        final words = a.text.trim().split(RegExp(r'\s+')).length;
        final reading = (1.2 + words * 0.4).clamp(2.0, 8.0);
        final hold = math.max(reading, (a.ms ?? 0) / 1000);
        _speechUntil = time + hold / speed;
        notifyListeners();
        await _sleep(hold);

      case NoteAction():
        onNote?.call(a.text);
        mood = Mood.proud;
        impulse(0.2);
        _burst(EffectKind.stars, blobTop + const Offset(0, 0.02), 6);
        notifyListeners();
        await _sleep(0.9);

      case LookAction():
        _lookTarget = a.target;
        _lookX = a.x;

      case JumpAction():
        for (var i = 0; i < a.times; i++) {
          if (gen != _gen) return;
          final here = blob.base(time);
          blob = Track(here, here, time, 0.45, MoveStyle.leap, arc: 0.12);
          await _sleep(0.45);
          impulse(-0.3);
          await _sleep(0.08);
        }

      case ShakeAction():
        if (a.target == 'blob') {
          _leanTarget = 0;
          for (var i = 0; i < 4; i++) {
            if (gen != _gen) return;
            _leanTarget = i.isEven ? 0.25 : -0.25;
            await _sleep(a.ms / 4000);
          }
          _leanTarget = 0;
        } else {
          props[a.target]?.shakeUntil = time + a.ms / 1000;
          await _sleep(a.ms / 1000);
        }

      case RemoveAction():
        // a graph piece takes whatever sits on it along (a curve's point, its tangent)
        final gone = {a.id};
        for (var grew = true; grew;) {
          grew = false;
          for (final p in props.values) {
            final hangs = (p.on != null && gone.contains(p.on)) || (p.to != null && gone.contains(p.to));
            if (hangs && gone.add(p.id)) grew = true;
          }
        }
        for (final id in gone) {
          props[id]?.diedAt ??= time;
        }
        if (gone.contains(_lookTarget)) _lookTarget = null;
        await _sleep(0.3);

      case ScaleAction():
        final p = props[a.target];
        if (p == null) return;
        p.scale = Ramp(p.scale.at(time), a.to, time, a.ms / 1000);
        await _sleep(a.ms / 1000);

      case SpinAction():
        final p = props[a.target];
        if (p == null) return;
        final now = p.spin.at(time);
        p.spin = Ramp(now, now + a.turns, time, a.ms / 1000, curve: Curves.easeInOut);
        await _sleep(a.ms / 1000);

      case RecolorAction():
        props[a.target]?.color = a.color;
        notifyListeners();

      case RelabelAction():
        final p = props[a.target];
        if (p == null) return;
        // on an emoji, words are its caption ("10 min" under the clock); a
        // glyph swaps the emoji itself
        final words = RegExp(r'[A-Za-z0-9]');
        if (p.kind == PropKind.emoji && words.hasMatch(a.label) && !words.hasMatch(p.label ?? '')) {
          p.caption = a.label;
        } else {
          p.label = a.label;
        }
        p.shakeUntil = time + 0.2;
        _lookTarget = p.id;
        notifyListeners();

      case CarryAction():
        final p = props[a.target];
        if (p == null || p.carried) return;
        final propX = p.pos.base(time).dx;
        await _walkBeside(p, blob.base(time).dx <= propX ? 1.0 : -1.0);
        if (gen != _gen) return;
        // Scoop: a heave, and it pops up onto the head.
        mood = Mood.strain;
        impulse(-0.3);
        await _sleep(0.25);
        p.carried = true;
        p.z = 0;
        mood = Mood.happy;
        impulse(0.25);
        await _sleep(0.3);

      case DropAction():
        final p = props[a.target];
        if (p == null || !p.carried) return;
        final from = propAt(p);
        p.carried = false;
        p.z = 0;
        p.pos = Track(from, Offset(from.dx, kGroundY), time, 0.4, MoveStyle.fall);
        impulse(0.2);
        await _sleep(0.4);
        p.shakeUntil = time + 0.15;

      case ThrowAction():
        final p = props[a.target];
        if (p == null) return;
        final from = propAt(p);
        p.carried = false;
        impulse(-0.3);
        _lookTarget = p.id;
        p.pos = Track(from, Offset(a.x, kGroundY), time, a.ms / 1000, MoveStyle.leap, arc: 0.3);
        final spun = p.spin.at(time);
        p.spin = Ramp(spun, spun + 1, time, a.ms / 1000, curve: Curves.linear);
        await _sleep(a.ms / 1000);
        p.shakeUntil = time + 0.2;

      case EffectAction():
        final at = a.x == null ? null : Offset(a.x!, a.y ?? kGroundY - 0.1);
        if (a.ms > 0) {
          _emitters.add(_Emitter(a.kind, at, time + a.ms / 1000));
        } else {
          _burst(a.kind, at ?? blobTop + const Offset(0, 0.05), _burstCount(a.kind));
        }

      case WearAction():
        hat = a.label;
        impulse(0.2);
        notifyListeners();

      case WaitAction():
        await _sleep(a.ms / 1000);

      case SetAction():
        final p = props[a.target];
        if (p == null) return;
        final seconds = a.ms / 1000 / speed;
        if (a.value != null) p.value = Ramp(p.value.at(time), a.value!, time, seconds, curve: Curves.easeInOut);
        if (a.rate != null) p.rate = Ramp(p.rate.at(time), a.rate!, time, seconds, curve: Curves.easeInOut);
        _lookTarget = p.id;
        await _sleepRaw(seconds);

      case CruiseAction():
        final p = props[a.target];
        if (p == null) return;
        p.cruise = Ramp(p.cruise.at(time), a.speed, time, 0.6, curve: Curves.easeInOut);
        _lookTarget = p.id;
        await _sleep(a.ms / 1000);
        if (gen != _gen) return;
        p.cruise = Ramp(p.cruise.at(time), 0, time, 0.6, curve: Curves.easeInOut);
        await _sleepRaw(0.6);

      case CompareAction():
        final on = a.left != null || a.right != null;
        if (on) {
          compareLeft = a.left;
          compareRight = a.right;
        }
        compare = Ramp(compare.at(time), on ? 1 : 0, time, 0.7, curve: Curves.easeInOut);
        if (on) {
          // the narrator stands between the two
          final here = blob.base(time);
          if ((here.dx - 0.5).abs() > 0.04) {
            blob = Track(here, const Offset(0.5, kGroundY), time, 0.6, MoveStyle.leap, arc: 0.1);
          }
          _lookTarget = null;
          _lookX = null;
        }
        await _sleep(0.7);

      case PlanAction():
        cast.addAll(a.cast);

      case SceneAction():
        // moving on: what was shown steps back into the room and dims --
        // still there, so the next idea visibly follows from it -- while the
        // slime takes a breath and turns to the student
        for (final p in props.values) {
          if (p.diedAt != null || p.carried) continue;
          p.dim = Ramp(p.dim.at(time), isGraphKind(p.kind) ? 0.3 : 0.45, time, 0.9, curve: Curves.easeInOut);
          if (!isGraphKind(p.kind) && p.kind != PropKind.math) {
            p.depth = Ramp(p.z, math.min(1.0, p.z + 0.4), time, 0.9, curve: Curves.easeInOutCubic);
          }
        }
        _lookTarget = null;
        _lookX = null;
        final here = blob.base(time);
        blob = Track(here, here, time, 0.4, MoveStyle.leap, arc: 0.05);
        if (const {Mood.confused, Mood.sad, Mood.strain, Mood.surprised}.contains(mood)) mood = Mood.neutral;
        await _sleep(0.9);

      case WorldAction():
        final to = a.threeD ? 1.0 : 0.0;
        if (world.to != to) world = Ramp(worldT, to, time, 0.9 / speed, curve: Curves.easeInOutCubic);
        await _sleep(0.9);

      case TurnAction():
        final p = props[a.target];
        if (p == null) return;
        final seconds = a.ms / 1000 / speed;
        p.yaw = Ramp(p.yaw.at(time), p.yaw.at(time) + a.yaw, time, seconds, curve: Curves.easeInOut);
        p.pitch = Ramp(p.pitch.at(time), p.pitch.at(time) + a.pitch, time, seconds, curve: Curves.easeInOut);
        _lookTarget = p.id;
        await _sleepRaw(seconds);

      case OrbitAction():
        final p = props[a.target];
        if (p == null || props[a.around] == null || a.around == a.target) return;
        final seconds = a.ms / 1000 / speed;
        final from = p.around == a.around ? p.orbitPhase.at(time) : 0.0;
        p.around = a.around;
        p.orbitRadius = a.radius;
        p.orbitPhase = Ramp(from, from + a.turns, time, seconds, curve: Curves.linear);
        _lookTarget = a.around;
        await _sleepRaw(seconds);

      case SlideAction():
        final p = props[a.target];
        if (p == null || p.kind != PropKind.dot) return;
        final seconds = a.ms / 1000 / speed;
        p.at = Ramp(p.at.at(time), a.at, time, seconds, curve: Curves.easeInOut);
        _lookTarget = p.id;
        await _sleepRaw(seconds);

      case TogetherAction():
        await Future.wait([for (final x in a.actions) _perform(x, gen)]);

      case AskAction():
        _lookTarget = null;
        _lookX = null;
        await _sleep(0.35);
        if (gen != _gen) return;
        question = StageQuestion(key: 'skit-${a.hashCode}', text: a.question, choices: a.choices);
        mood = Mood.confused;
        impulse(0.2);
        _answer = Completer<String?>();
        notifyListeners();
        final picked = await _answer!.future;
        if (picked == null || gen != _gen) return;
        onChecked?.call(a.question, a.choices, picked, a.answer);
        final right = a.choices.where((c) => c.id == a.answer).firstOrNull;
        if (right != null && picked != a.answer) {
          // a wrong pick is corrected, not left hanging: show the right one
          mood = Mood.thinking;
          notifyListeners();
          await _perform(SayAction('It\'s \u201c${right.text}\u201d!'), gen);
          if (gen != _gen) return;
        }
        final branch = a.then[picked];
        if (branch != null) await _runAll(branch, gen);
    }
  }

  // ------------------------------------------------------------ particles

  int _burstCount(EffectKind k) => switch (k) {
        EffectKind.confetti => 40,
        EffectKind.sparks => 18,
        EffectKind.stars => 10,
        EffectKind.splash => 16,
        EffectKind.hearts => 6,
        EffectKind.zzz => 3,
        _ => 12,
      };

  void _burst(EffectKind k, Offset at, int n) {
    for (var i = 0; i < n; i++) {
      particles.add(_spawnParticle(k, at, i));
    }
  }

  Particle _spawnParticle(EffectKind k, Offset at, int i) {
    double r() => _rng.nextDouble();
    final a = r() * math.pi * 2;
    final spread = Offset(math.cos(a), math.sin(a));
    return switch (k) {
      EffectKind.confetti => Particle(
          kind: k, pos: at, vel: Offset((r() - 0.5) * 0.9, -0.5 - r() * 0.6),
          born: time, life: 1.6 + r(), size: 5 + r() * 4, gravity: 1.2, hue: r()),
      EffectKind.sparks => Particle(
          kind: k, pos: at, vel: spread * (0.3 + r() * 0.4), born: time, life: 0.45 + r() * 0.3, size: 7, hue: r()),
      EffectKind.stars => Particle(
          kind: k, pos: at, vel: spread * (0.15 + r() * 0.25) - const Offset(0, 0.1),
          born: time, life: 0.9 + r() * 0.4, size: 6 + r() * 5, gravity: 0.3),
      EffectKind.splash => Particle(
          kind: k, pos: at, vel: Offset((r() - 0.5) * 0.6, -0.4 - r() * 0.4),
          born: time, life: 0.8, size: 3 + r() * 3, gravity: 1.6),
      EffectKind.smoke => Particle(
          kind: k, pos: at + Offset((r() - 0.5) * 0.04, 0), vel: Offset((r() - 0.5) * 0.05, -0.12 - r() * 0.05),
          born: time, life: 1.4 + r() * 0.6, size: 8 + r() * 6),
      EffectKind.fire => Particle(
          kind: k, pos: at + Offset((r() - 0.5) * 0.05, 0), vel: Offset((r() - 0.5) * 0.04, -0.18 - r() * 0.1),
          born: time, life: 0.6 + r() * 0.3, size: 7 + r() * 6, hue: r()),
      EffectKind.rain => Particle(
          kind: k, pos: Offset(at.dx + (r() - 0.5) * 0.3, at.dy - 0.25), vel: const Offset(0.02, 0.9),
          born: time, life: 0.5, size: 8),
      EffectKind.snow => Particle(
          kind: k, pos: Offset(at.dx + (r() - 0.5) * 0.35, at.dy - 0.3), vel: Offset((r() - 0.5) * 0.04, 0.12),
          born: time, life: 2.6, size: 2.5 + r() * 2),
      EffectKind.bubbles => Particle(
          kind: k, pos: at + Offset((r() - 0.5) * 0.06, 0), vel: Offset(0, -0.12 - r() * 0.08),
          born: time, life: 1.8, size: 4 + r() * 6, hue: r()),
      EffectKind.hearts => Particle(
          kind: k, pos: at + Offset((r() - 0.5) * 0.08, 0), vel: Offset((r() - 0.5) * 0.08, -0.15 - r() * 0.08),
          born: time, life: 1.4, size: 6 + r() * 4),
      EffectKind.zzz => Particle(
          kind: k, pos: at + Offset(0.03 + i * 0.015, -i * 0.03), vel: const Offset(0.03, -0.08),
          born: time + i * 0.35, life: 1.6, size: 10 + i * 3.0),
    };
  }

  /// Per second, for a running emitter.
  double _rate(EffectKind k) => switch (k) {
        EffectKind.rain => 40,
        EffectKind.snow => 14,
        EffectKind.fire => 30,
        EffectKind.smoke => 8,
        EffectKind.bubbles => 6,
        EffectKind.hearts => 4,
        EffectKind.zzz => 1.2,
        _ => 20,
      };

  void _stepParticles(double dt) {
    _emitters.removeWhere((e) => time >= e.until);
    for (final e in _emitters) {
      e.carry += _rate(e.kind) * dt;
      while (e.carry >= 1) {
        e.carry -= 1;
        final at = e.at ?? blobTop + const Offset(0, 0.03);
        particles.add(_spawnParticle(e.kind, at, 0));
      }
    }
    for (final p in particles) {
      if (time < p.born) continue;
      p.vel += Offset(0, p.gravity * dt);
      if (p.kind == EffectKind.bubbles) {
        p.vel = Offset(math.sin((time - p.born) * 5 + p.hue * 6) * 0.03, p.vel.dy);
      }
      p.pos += p.vel * dt;
    }
    particles.removeWhere((p) => time - p.born > p.life);
  }

  // ------------------------------------------------------- keeping clear

  /// Roughly where a prop shows on the stage, in stage fractions (null for
  /// what can't be placed: a graph piece whose axes are gone, say).
  /// A photo prop's width at size 1, as a fraction of the stage width.
  static const photoWidth = 0.3;

  Rect? propBounds(StageProp p) {
    if (p.kind == PropKind.link) return null;
    final wpx = size.width <= 0 ? 1.0 : size.width, hpx = size.height <= 0 ? 1.0 : size.height;
    if (isGraphKind(p.kind)) {
      final axes = axesOf(p);
      if (axes == null) return null;
      return Rect.fromPoints(graphToStage(axes, axes.xmin, axes.ymax), graphToStage(axes, axes.xmax, axes.ymin))
          .inflate(0.02);
    }
    final at = propAt(p);
    final k = propScale(p) * p.scale.at(time);
    if (isSolidKind(p.kind)) {
      final s = blobRadius * 1.7 * p.size * k;
      return Rect.fromLTWH(at.dx - s / 2 / wpx, at.dy - s / hpx, s / wpx, s / hpx);
    }
    Rect px(double w, double h) => Rect.fromLTWH(at.dx - w * k / 2 / wpx, at.dy - h * k / hpx, w * k / wpx, h * k / hpx);
    switch (p.kind) {
      case PropKind.arrow || PropKind.line || PropKind.wave:
        final d = p.headDelta ?? const Offset(0.15, 0);
        return Rect.fromPoints(at, at + d * k).inflate(0.03);
      case PropKind.path:
        var r = Rect.fromLTWH(at.dx, at.dy, 0, 0);
        for (final q in p.points) {
          r = r.expandToInclude(Rect.fromLTWH(at.dx + q.dx * k, at.dy + q.dy * k, 0, 0));
        }
        return r.inflate(0.02);
      case PropKind.text:
        final fs = 15.0 * p.size;
        final w = math.min(160.0, (p.label ?? '').length * fs * 0.6);
        return Rect.fromCenter(center: at, width: w * k / wpx, height: fs * 1.4 * k / hpx);
      case PropKind.photo:
        final w = photoWidth * wpx * p.size;
        return px(w, w * 0.75).shift(Offset(0, w * 0.75 * k / 2 / hpx));
      case PropKind.math:
        final fs = 18.0 * p.size;
        final w = math.min(260.0, (p.tex ?? '').length * fs * 0.45);
        return Rect.fromCenter(center: at, width: w / wpx, height: fs * 2 / hpx);
      case PropKind.circle:
        final d = (p.w ?? 0.12) * hpx * p.size;
        return px(d, d);
      case PropKind.rect || PropKind.triangle:
        final w = (p.w ?? 0.15) * hpx * p.size;
        return px(w, (p.h ?? p.w ?? 0.15) * hpx * p.size);
      case PropKind.cloud:
        final half = propHalf(p);
        return px(half * 3.4, half * 1.5);
      case PropKind.counter:
        final half = propHalf(p);
        return px(half * 2.6, half * 1.4);
      case PropKind.gauge:
        final half = propHalf(p);
        return px(half * 2.8, half * 1.9);
      case PropKind.bar || PropKind.thermometer:
        final half = propHalf(p);
        return px(half * 1.1, half * 2.9);
      default:
        final half = propHalf(p);
        return px(half * 2, half * 2);
    }
  }

  /// Where the slime would cover things if it stood at [x].
  Rect _blobBoxAt(double x) {
    final w = _pxToX(blobRadius * 1.25), h = _pxToY(blobRadius * 2.1);
    return Rect.fromLTRB(x - w, kGroundY - h, x + w, kGroundY);
  }

  double _covered(Rect me, {String? except}) {
    var total = 0.0;
    for (final p in props.values) {
      if (p.diedAt != null || p.carried || p.id == except) continue;
      final b = propBounds(p);
      if (b == null) continue;
      final o = me.intersect(b);
      if (o.width > 0 && o.height > 0) total += o.width * o.height;
    }
    return total;
  }

  /// If the slime hides [p], hop to the nearest spot beside it that hides
  /// nothing (or the least). True if it moved.
  Future<bool> _stepAside(StageProp p, int gen) async {
    if (gen != _gen || p.carried) return false;
    final here = blob.base(time);
    final target = propBounds(p);
    if (target == null) return false;
    final mine = _blobBoxAt(here.dx).intersect(target);
    if (mine.width <= 0 || mine.height <= 0) return false;
    double? best;
    var bestCost = double.infinity;
    for (var x = 0.08; x <= 0.921; x += 0.02) {
      final box = _blobBoxAt(x);
      final cost = _covered(box) * 400 + (x - here.dx).abs() + (x - target.center.dx).abs() * 0.3;
      if (cost < bestCost) {
        bestCost = cost;
        best = x;
      }
    }
    if (best == null || (best - here.dx).abs() < 0.02) return false;
    final hop = ((best - here.dx).abs() * 1.6).clamp(0.4, 1.0);
    mood = mood == Mood.neutral ? Mood.surprised : mood;
    blob = Track(here, Offset(best, kGroundY), time, hop, MoveStyle.leap, arc: 0.14);
    await _sleep(hop);
    if (mood == Mood.surprised) mood = Mood.happy;
    impulse(-0.25);
    _lookTarget = p.id;
    return true;
  }

  /// Hop to stand touching [p], on its left (dir 1) or right (dir -1) side.
  Future<void> _walkBeside(StageProp p, double dir) async {
    final standX = p.pos.base(time).dx - dir * _pxToX(propHalf(p) + blobRadius * 1.3);
    final here = blob.base(time);
    final walk = ((here.dx - standX).abs() * 2.2).clamp(0.25, 1.4);
    _lookTarget = p.id;
    blob = Track(here, Offset(standX, kGroundY), time, walk, MoveStyle.hop);
    await _sleep(walk);
  }

  /// Walk up behind the prop, strain against it (it doesn't budge at first
  /// -- that's the joke), then shove it along together.
  Future<void> _push(PushAction a, int gen) async {
    final p = props[a.target];
    if (p == null) return;
    final dir = a.dx >= 0 ? 1.0 : -1.0;
    final propAt = p.pos.base(time);
    await _walkBeside(p, dir);
    if (gen != _gen) return;

    // Wind-up: lean in, strain, nothing happens.
    mood = Mood.strain;
    _leanTarget = dir * 0.3;
    p.shakeUntil = time + 0.6;
    await _sleep(0.7);
    if (gen != _gen) return;

    // It gives.
    final seconds = a.ms / 1000;
    final pushFrom = blob.base(time);
    blob = Track(pushFrom, pushFrom + Offset(a.dx, 0), time, seconds, MoveStyle.slide);
    p.pos = Track(propAt, propAt + Offset(a.dx, 0), time, seconds, MoveStyle.slide);
    await _sleep(seconds);
    _leanTarget = 0;
    if (mood == Mood.strain) mood = Mood.neutral;
    impulse(-0.25);
  }
}
