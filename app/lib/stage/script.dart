/// The stage script: the ONE vocabulary the character engine understands.
///
/// Nothing in the engine knows about any topic. A skit about force, a joke
/// about recursion, a tour of the water cycle -- all of them are just lists
/// of these actions. Today a hand-written demo skit uses them (skits.dart);
/// the end goal is an LLM emitting the same JSON for whatever the chat is
/// about, so every action here parses from plain JSON (`StageAction.fromJson`)
/// and nothing can run on the device that isn't in this list.
///
/// Coordinates are fractions of the stage: x 0 (left) .. 1 (right), y 0 (top)
/// .. 1 (bottom). The ground sits at [kGroundY]; the blob's position is the
/// point where it touches the ground.
library;

import 'dart:ui' show Offset;

const double kGroundY = 0.62;

/// How the blob (or a prop) looks while it acts. The face, brows, mouth and
/// extras (sweat, blush, sparkles) all come from this one value.
enum Mood { neutral, happy, excited, confused, strain, surprised, sad, proud, thinking }

Mood moodFromName(String? name) =>
    Mood.values.firstWhere((m) => m.name == name, orElse: () => Mood.neutral);

/// Things the blob can conjure onto the stage.
///
/// Coverage comes from three layers, so a script is never stuck for "the
/// thing it needs":
///  * `emoji` -- ANY object the platform's emoji font has (🍎 🚗 🌍 ⚡ 🧲 🦠 🏛️
///    ...): the open-ended one, and what a generator should reach for first;
///  * drawing primitives -- `circle`, `rect`, `triangle`, `line`, `arrow`,
///    `path` (a polyline through `points`), `wave` (a travelling sine wave),
///    `text` -- for diagrams, graphs and labels;
///  * a few hand-drawn props with more personality -- `box`, `ball`, `star`,
///    `heart`, `cloud`.
enum PropKind {
  box, ball, arrow, text, star, heart, cloud, //
  emoji, circle, rect, triangle, line, path, wave,
  // the graph kit: axes -> plot (a formula) -> dot (a point on it) -> tangent
  axes, plot, dot, tangent,
  // a typeset formula (LaTeX)
  math,
  // a flowing connection between two props (`on` -> `to`)
  link,
  // the learner's own attached picture, held up on the stage (picture.dart)
  photo,
  // instruments: they SHOW a quantity changing (the engine animates them)
  clock, stopwatch, counter, gauge, bar, thermometer,
  // 3D solids (solids.dart), placed in the stage's world with depth
  cube, sphere, cylinder, cone, pyramid, prism, torus, planet, atom,
}

bool isSolidKind(PropKind k) => k.index >= PropKind.cube.index;

bool isInstrumentKind(PropKind k) => k.index >= PropKind.clock.index && k.index <= PropKind.thermometer.index;

bool isGraphKind(PropKind k) =>
    k == PropKind.axes || k == PropKind.plot || k == PropKind.dot || k == PropKind.tangent;

/// Null for a kind this build doesn't know (see SpawnAction.fromJson: it
/// becomes a text label, so the idea still reaches the screen).
PropKind? propKindFromName(String? name) =>
    PropKind.values.where((k) => k.name == name).firstOrNull;

/// Particle effects: short bursts, or emitters that run for `ms`.
enum EffectKind { confetti, sparks, smoke, fire, rain, snow, bubbles, hearts, zzz, splash, stars }

EffectKind effectKindFromName(String? name) =>
    EffectKind.values.firstWhere((k) => k.name == name, orElse: () => EffectKind.sparks);

/// How something travels from A to B.
enum MoveStyle {
  /// Slime-scoot: small quick hops (the blob's normal walk).
  hop,

  /// Straight glide (a pushed box, a sliding ball).
  slide,

  /// One big arc (jumping onto something, a thrown ball).
  leap,

  /// Accelerating straight down-ish (dropped, falling under gravity).
  fall,
}

MoveStyle moveStyleFromName(String? name) =>
    MoveStyle.values.firstWhere((s) => s.name == name, orElse: () => MoveStyle.hop);

/// A choice offered beneath the blob. `id` is whatever the caller needs back
/// (a chat option id, or a branch key in a skit).
class StageChoice {
  const StageChoice({required this.id, required this.text});
  final String id;
  final String text;

  factory StageChoice.fromJson(Map<String, dynamic> j) =>
      StageChoice(id: '${j['id']}', text: '${j['text'] ?? ''}');
}

sealed class StageAction {
  const StageAction();

  /// Parse one action. Unknown `do` values become a no-op [WaitAction] of 0
  /// ms rather than an error: a script from a newer generator must degrade,
  /// not crash the stage.
  static StageAction fromJson(Map<String, dynamic> j) {
    double d(String k, double fallback) => (j[k] as num?)?.toDouble() ?? fallback;
    int ms(double fallback) => (j['ms'] as num?)?.toInt() ?? fallback.toInt();
    String? s(String k) => j[k] as String?;
    List<StageAction> list(String k) => parseScript(j[k] as List? ?? const []);

    switch (j['do']) {
      case 'spawn':
        final kind = propKindFromName(s('kind'));
        return SpawnAction(
          id: s('id') ?? 'prop',
          kind: kind ?? PropKind.text,
          x: d('x', 0.5),
          y: d('y', kGroundY),
          x2: (j['x2'] as num?)?.toDouble(),
          y2: (j['y2'] as num?)?.toDouble(),
          label: kind == null ? (s('label') ?? s('kind')) : s('label'),
          caption: s('caption'),
          to: s('to'),
          enter: s('enter'),
          rate: (j['rate'] as num?)?.toDouble(),
          value: (j['value'] as num?)?.toDouble(),
          track: s('track'),
          size: d('size', 1),
          color: s('color'),
          w: (j['w'] as num?)?.toDouble(),
          h: (j['h'] as num?)?.toDouble(),
          points: [
            for (final pt in (j['points'] as List? ?? const []))
              if (pt is List && pt.length >= 2) Offset((pt[0] as num).toDouble(), (pt[1] as num).toDouble()),
          ],
          amp: d('amp', 0.04),
          cycles: d('cycles', 3),
          fn: s('fn'),
          on: s('on'),
          at: (j['at'] as num?)?.toDouble(),
          xmin: d('xmin', -5),
          xmax: d('xmax', 5),
          ymin: d('ymin', -5),
          ymax: d('ymax', 5),
          tex: s('tex'),
          z: d('z', 0),
          lift: d('lift', 0),
          spin: d('spin', 0),
          yaw: d('yaw', 0.08),
          pitch: d('pitch', 0),
        );
      case 'world':
        return WorldAction(threeD: s('mode') == '3d');
      case 'turn':
        return TurnAction(target: s('target') ?? '', yaw: d('yaw', 1), pitch: d('pitch', 0), ms: ms(1500));
      case 'orbit':
        return OrbitAction(
          target: s('target') ?? '',
          around: s('around') ?? '',
          radius: d('radius', 0.15),
          turns: d('turns', 1),
          ms: ms(3000),
        );
      case 'slide':
        return SlideAction(target: s('target') ?? '', at: d('at', 0), ms: ms(1400));
      case 'move':
        return MoveAction(
          target: s('target') ?? 'blob',
          x: d('x', 0.5),
          y: (j['y'] as num?)?.toDouble(),
          ms: ms(700),
          style: moveStyleFromName(s('style')),
        );
      case 'approach':
        return ApproachAction(s('target') ?? '');
      case 'push':
        return PushAction(target: s('target') ?? '', dx: d('dx', 0.2), ms: ms(1400));
      case 'emote':
        return EmoteAction(moodFromName(s('mood')));
      case 'say':
        return SayAction(s('text') ?? '', ms: (j['ms'] as num?)?.toInt());
      case 'look':
        return LookAction(target: s('target'), x: (j['x'] as num?)?.toDouble());
      case 'jump':
        return JumpAction(times: (j['times'] as num?)?.toInt() ?? 1);
      case 'shake':
        return ShakeAction(target: s('target') ?? 'blob', ms: ms(500));
      case 'remove':
        return RemoveAction(s('id') ?? '');
      case 'scale':
        return ScaleAction(target: s('target') ?? '', to: d('to', 1.5), ms: ms(500));
      case 'spin':
        return SpinAction(target: s('target') ?? '', turns: d('turns', 1), ms: ms(800));
      case 'recolor':
        return RecolorAction(target: s('target') ?? '', color: s('color'));
      case 'relabel':
        return RelabelAction(target: s('target') ?? '', label: s('label') ?? '');
      case 'carry':
        return CarryAction(s('target') ?? '');
      case 'drop':
        return DropAction(s('target') ?? '');
      case 'throw':
        return ThrowAction(target: s('target') ?? '', x: d('x', 0.8), ms: ms(700));
      case 'effect':
        return EffectAction(
          kind: effectKindFromName(s('kind')),
          x: (j['x'] as num?)?.toDouble(),
          y: (j['y'] as num?)?.toDouble(),
          ms: ms(0),
        );
      case 'wear':
        return WearAction(s('label'));
      case 'note':
        final text = s('text');
        return text == null || text.trim().isEmpty ? const WaitAction(0) : NoteAction(text.trim());
      case 'wait':
        return WaitAction(ms(500));
      case 'scene':
        return const SceneAction();
      case 'set':
        return SetAction(
          target: s('target') ?? '',
          value: (j['value'] as num?)?.toDouble(),
          rate: (j['rate'] as num?)?.toDouble(),
          ms: ms(800),
        );
      case 'cruise':
        return CruiseAction(target: s('target') ?? '', speed: d('speed', 0.8), ms: ms(3000));
      case 'compare':
        return CompareAction(left: s('left'), right: s('right'));
      case 'plan':
        return PlanAction({
          for (final e in (j['cast'] as Map? ?? const {}).entries)
            if (e.value is String) '${e.key}': e.value as String,
        });
      case 'together':
        return TogetherAction(list('actions'));
      case 'ask':
        final branches = <String, List<StageAction>>{};
        final then = j['then'] as Map? ?? const {};
        for (final e in then.entries) {
          branches['${e.key}'] = parseScript(e.value as List? ?? const []);
        }
        return AskAction(
          question: s('question') ?? '',
          choices: [
            for (final c in (j['choices'] as List? ?? const []))
              StageChoice.fromJson((c as Map).cast<String, dynamic>()),
          ],
          then: branches,
          answer: j['answer'] == null ? null : '${j['answer']}',
        );
      default:
        return const WaitAction(0);
    }
  }
}

List<StageAction> parseScript(List<dynamic> json) => [
      for (final a in json)
        if (a is Map) StageAction.fromJson(a.cast<String, dynamic>()),
    ];

/// Conjure a prop, with a pop. Sizes: [size] scales any prop; [w]/[h] give
/// a `rect`/`circle`/`triangle` its size as fractions of the stage HEIGHT
/// (so shapes keep their proportions on any panel). For an `arrow`/`line`,
/// (x, y) is the tail and (x2, y2) the head; a `wave` runs from x to x2 at
/// height y; a `path` goes through [points] (stage fractions).
class SpawnAction extends StageAction {
  const SpawnAction({
    required this.id,
    required this.kind,
    required this.x,
    required this.y,
    this.x2,
    this.y2,
    this.label,
    this.caption,
    this.to,
    this.enter,
    this.rate,
    this.value,
    this.track,
    this.size = 1,
    this.color,
    this.w,
    this.h,
    this.points = const [],
    this.amp = 0.04,
    this.cycles = 3,
    this.fn,
    this.on,
    this.at,
    this.xmin = -5,
    this.xmax = 5,
    this.ymin = -5,
    this.ymax = 5,
    this.tex,
    this.z = 0,
    this.lift = 0,
    this.spin = 0,
    this.yaw = 0.08,
    this.pitch = 0,
  });
  final String id;
  final PropKind kind;
  final double x, y;

  /// 3D solids: depth into the stage (0 front .. 1 far), height above the
  /// floor, a steady spin (turns per second), and a starting yaw/pitch (turns).
  final double z, lift, spin, yaw, pitch;

  /// Graph kit. `fn`: a plot's formula of x; `on`: the prop this one sits on
  /// (a plot on axes, a dot on a plot, a tangent on a dot); `at`: a dot's x;
  /// xmin..ymax: the axes' ranges. `tex`: a typeset (LaTeX) formula or label.
  final String? fn, on;
  final double? at;
  final double xmin, xmax, ymin, ymax;
  final String? tex;
  final double? x2, y2;
  final String? label;

  /// Words under an emoji ("t = 0" under a clock): an emoji's label is the
  /// glyph itself.
  final String? caption;

  /// Link: the prop it flows to (from `on`).
  final String? to;

  /// How it arrives: pop | drop | rise | swoop (null = the app picks).
  final String? enter;

  /// Instruments: ticks/counts per second, a gauge's reading (0..1), and a
  /// moving thing it is bound to (see StageEngine's instruments).
  final double? rate, value;
  final String? track;
  final double size;

  /// A palette name ("accent", "olive", "warn", "ink", "blue") -- never a raw
  /// hex, so a generated script can't make the stage unreadable.
  final String? color;
  final double? w, h;
  final List<Offset> points;

  /// Wave only: amplitude (fraction of stage height) and how many cycles fit.
  final double amp, cycles;
}

/// Switch the stage between a flat backdrop and a 3D space with a floor.
class WorldAction extends StageAction {
  const WorldAction({required this.threeD});
  final bool threeD;
}

/// Turn a 3D solid by [yaw]/[pitch] full rotations.
class TurnAction extends StageAction {
  const TurnAction({required this.target, this.yaw = 1, this.pitch = 0, this.ms = 1500});
  final String target;
  final double yaw, pitch;
  final int ms;
}

/// Send a solid around another [turns] times at [radius] (passing behind it).
class OrbitAction extends StageAction {
  const OrbitAction({required this.target, required this.around, this.radius = 0.15, this.turns = 1, this.ms = 3000});
  final String target, around;
  final double radius, turns;
  final int ms;
}

/// Slide a graph dot along its curve to x = [at].
class SlideAction extends StageAction {
  const SlideAction({required this.target, required this.at, this.ms = 1400});
  final String target;
  final double at;
  final int ms;
}

/// Travel to x (and y, for props that fly). `target` is "blob" or a prop id.
class MoveAction extends StageAction {
  const MoveAction({
    required this.target,
    required this.x,
    this.y,
    this.ms = 700,
    this.style = MoveStyle.hop,
  });
  final String target;
  final double x;
  final double? y;
  final int ms;
  final MoveStyle style;
}

/// The blob hops over to stand right beside a prop (on the side it's
/// coming from) -- so a script never has to know how wide anything is.
class ApproachAction extends StageAction {
  const ApproachAction(this.target);
  final String target;
}

/// The blob walks up to a prop and shoves it `dx` along the ground, straining
/// the whole way. The comedy beat (wind-up, sweat, the prop finally giving)
/// is the engine's, not the script's.
class PushAction extends StageAction {
  const PushAction({required this.target, this.dx = 0.2, this.ms = 1400});
  final String target;
  final double dx;
  final int ms;
}

class EmoteAction extends StageAction {
  const EmoteAction(this.mood);
  final Mood mood;
}

/// A speech bubble. Holds for `ms`, or long enough to read `text`.
class SayAction extends StageAction {
  const SayAction(this.text, {this.ms});
  final String text;
  final int? ms;
}

/// Turn the eyes toward a prop, or an x on the stage; null/null looks ahead.
class LookAction extends StageAction {
  const LookAction({this.target, this.x});
  final String? target;
  final double? x;
}

class JumpAction extends StageAction {
  const JumpAction({this.times = 1});
  final int times;
}

class ShakeAction extends StageAction {
  const ShakeAction({required this.target, this.ms = 500});
  final String target;
  final int ms;
}

/// Poof a prop away.
class RemoveAction extends StageAction {
  const RemoveAction(this.id);
  final String id;
}

class WaitAction extends StageAction {
  const WaitAction(this.ms);
  final int ms;
}

/// The story moves on: what's on stage steps back into the room, dimmed but
/// still there; anything used again comes forward.
class SceneAction extends StageAction {
  const SceneAction();
}

/// An instrument's reading and/or rate eases to new values.
class SetAction extends StageAction {
  const SetAction({required this.target, this.value, this.rate, required this.ms});
  final String target;
  final double? value, rate;
  final int ms;
}

/// A thing travels at a steady [speed] (0..0.99) for [ms], in place: speed
/// lines stream past it, and whatever tracks it responds.
class CruiseAction extends StageAction {
  const CruiseAction({required this.target, required this.speed, required this.ms});
  final String target;
  final double speed;
  final int ms;
}

/// Two lanes for a side-by-side comparison (both null: the lanes go).
class CompareAction extends StageAction {
  const CompareAction({this.left, this.right});
  final String? left, right;
}

/// The script's plan: what each id stands for (used as its caption when it
/// has none).
class PlanAction extends StageAction {
  const PlanAction(this.cast);
  final Map<String, String> cast;
}

/// Run several actions at once; finishes when the slowest does.
class TogetherAction extends StageAction {
  const TogetherAction(this.actions);
  final List<StageAction> actions;
}

/// The blob asks: "?" over its head, the question beside it, the choices as
/// bubbles beneath it. The script waits for an answer, then runs the matching
/// `then` branch (if any).
class AskAction extends StageAction {
  const AskAction({required this.question, required this.choices, this.then = const {}, this.answer});
  final String question;
  final List<StageChoice> choices;
  final Map<String, List<StageAction>> then;

  /// The right choice's id, when the director marked one -- a wrong pick is
  /// shown the right answer before the slime's reaction.
  final String? answer;
}

/// Grow or shrink a prop to [to] × its spawned size.
class ScaleAction extends StageAction {
  const ScaleAction({required this.target, this.to = 1.5, this.ms = 500});
  final String target;
  final double to;
  final int ms;
}

class SpinAction extends StageAction {
  const SpinAction({required this.target, this.turns = 1, this.ms = 800});
  final String target;
  final double turns;
  final int ms;
}

class RecolorAction extends StageAction {
  const RecolorAction({required this.target, this.color});
  final String target;
  final String? color;
}

/// Change a prop's label in place (a counter ticking, a value updating).
class RelabelAction extends StageAction {
  const RelabelAction({required this.target, required this.label});
  final String target;
  final String label;
}

/// The blob walks to a prop and lifts it onto its head; it rides along until
/// [DropAction] or [ThrowAction].
class CarryAction extends StageAction {
  const CarryAction(this.target);
  final String target;
}

class DropAction extends StageAction {
  const DropAction(this.target);
  final String target;
}

/// Lob a carried (or any) prop in an arc to land at x.
class ThrowAction extends StageAction {
  const ThrowAction({required this.target, this.x = 0.8, this.ms = 700});
  final String target;
  final double x;
  final int ms;
}

/// A particle effect at (x, y) (default: on the blob). `ms` > 0 keeps an
/// emitter running that long (rain, fire, smoke); 0 is one burst.
class EffectAction extends StageAction {
  const EffectAction({required this.kind, this.x, this.y, this.ms = 0});
  final EffectKind kind;
  final double? x, y;
  final int ms;
}

/// Put an emoji on the blob's head (🎓 👑 🔥 🎩 ...); null takes it off.
class WearAction extends StageAction {
  const WearAction(this.label);
  final String? label;
}

/// A takeaway worth keeping: pinned under the stage ("Keep in mind").
class NoteAction extends StageAction {
  const NoteAction(this.text);
  final String text;
}
