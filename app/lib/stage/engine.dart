import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/animation.dart';
import 'package:flutter/foundation.dart';

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
    this.size = 1,
    this.color,
    this.w,
    this.h,
    this.points = const [],
    this.amp = 0.04,
    this.cycles = 3,
  });
  final String id;
  final PropKind kind;
  Track pos;
  final double bornAt;

  /// Arrow/line/wave: the far end relative to (x, y) (it travels with it).
  final Offset? headDelta;
  String? label;
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

  /// Mouth flapping (an answer is streaming in the chat).
  bool talking = false;

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
        _ => (size.height * 0.05).clamp(12.0, 34.0) * p.size,
      };

  double _pxToX(double px) => size.width <= 0 ? 0 : px / size.width;
  double _pxToY(double px) => size.height <= 0 ? 0 : px / size.height;

  /// The blob's height in pixels right now (the painter draws it this tall).
  double get blobHeight => blobRadius * 1.9 * (1 + blobSquash + math.sin(time * 2.6) * 0.025);

  /// Where the top of the blob's head is, in stage fractions.
  Offset get blobTop => blobPos - Offset(0, _pxToY(blobHeight));

  /// A prop's base point right now -- on the blob's head while carried.
  Offset propAt(StageProp p) => p.carried ? blobTop + Offset(0, _pxToY(4)) : p.pos.at(time);

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

    props.removeWhere((_, p) => p.diedAt != null && time - p.diedAt! > 0.6);
    _stepParticles(dt);

    if (_awaitingPerformance && time >= _awaitDeadline) {
      // No performance followed the gag (e.g. the answer failed).
      _awaitingPerformance = false;
      endLive();
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

  Future<void> _sleep(double seconds) {
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
    for (final p in props.values) {
      p.diedAt ??= time;
    }
    _emitters.clear();
    hat = null;
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
      if (gen == _gen) _leanTarget = 0;
      notifyListeners();
    }
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
        if (gen == _gen) {
          _leanTarget = 0;
          if (mood == Mood.strain) mood = Mood.neutral;
        }
        notifyListeners();
      }
    }();
  }

  /// One action of the live performance. An `ask` is ignored: the slime's
  /// questions come from the chat's options turn ([ask]), never a script.
  void enqueue(StageAction action) {
    if (!_liveOpen || action is AskAction) return;
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
    _intro = parseScript([
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

  Future<void> _perform(StageAction a, int gen) async {
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
          size: a.size,
          color: a.color,
          w: a.w,
          h: a.h,
          points: [for (final pt in a.points) pt - tail],
          amp: a.amp,
          cycles: a.cycles,
        );
        // The blob conjures it: a little heave, eyes on the new thing.
        impulse(0.18);
        _lookTarget = a.id;
        notifyListeners();
        await _sleep(0.35);

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
          await _sleep(seconds);
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
        final hold = (a.ms ?? (900 + a.text.length * 45).clamp(1200, 5000)) / 1000;
        _speechUntil = time + hold;
        notifyListeners();
        await _sleep(hold);

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
        props[a.id]?.diedAt = time;
        if (_lookTarget == a.id) _lookTarget = null;
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
        p.label = a.label;
        p.shakeUntil = time + 0.2;
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
        mood = Mood.happy;
        impulse(0.25);
        await _sleep(0.3);

      case DropAction():
        final p = props[a.target];
        if (p == null || !p.carried) return;
        final from = propAt(p);
        p.carried = false;
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

      case TogetherAction():
        await Future.wait([for (final x in a.actions) _perform(x, gen)]);

      case AskAction():
        question = StageQuestion(key: 'skit-${a.hashCode}', text: a.question, choices: a.choices);
        mood = Mood.confused;
        impulse(0.2);
        _answer = Completer<String?>();
        notifyListeners();
        final picked = await _answer!.future;
        if (picked == null || gen != _gen) return;
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
