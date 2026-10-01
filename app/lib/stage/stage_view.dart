import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter_math_fork/flutter_math.dart';

import '../theme.dart';
import 'engine.dart';
import 'painter.dart';
import 'script.dart';

/// The stage on screen: the painted world (ground, props, blob), plus two
/// kinds of widget on top of it -- the blob's speech bubble beside its head,
/// and, while it's asking, the choices as message bubbles beneath it.
///
/// The view drives the engine's clock from a Ticker. Under a test binding
/// the clock doesn't run by itself (a character that never stops breathing
/// would make every `pumpAndSettle` time out); tests step
/// [StageEngine.tick] by hand, or pass `animate: true`.
class StageView extends StatefulWidget {
  const StageView({super.key, required this.engine, this.onChoice, this.animate, this.quiet = false});

  final StageEngine engine;

  /// A small replay with nobody to talk to (a Home card's picture): the
  /// scene only, without the speech bubble or any choices.
  final bool quiet;

  /// Called when a choice bubble is tapped. Null = choices shown but not
  /// clickable right now (e.g. the chat is mid-turn).
  final void Function(StageChoice choice)? onChoice;

  final bool? animate;

  @override
  State<StageView> createState() => _StageViewState();
}

class _StageViewState extends State<StageView> with SingleTickerProviderStateMixin {
  Ticker? _ticker;
  Duration _last = Duration.zero;

  bool get _animate => widget.animate ?? WidgetsBinding.instance is WidgetsFlutterBinding;

  @override
  void initState() {
    super.initState();
    if (_animate) {
      _ticker = createTicker((elapsed) {
        final dt = (elapsed - _last).inMicroseconds / 1e6;
        _last = elapsed;
        widget.engine.tick(dt);
      })
        ..start();
    }
  }

  @override
  void dispose() {
    _ticker?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      final size = Size(c.maxWidth, c.maxHeight);
      widget.engine.size = size;
      final stage = Stack(
        children: [
          Positioned.fill(
            child: CustomPaint(key: const ValueKey('stage-canvas'), painter: StagePainter(widget.engine)),
          ),
          ListenableBuilder(
            listenable: widget.engine,
            builder: (context, _) => Stack(
              children: [
                ..._photos(size),
                ..._formulas(size),
                if (!widget.quiet) ..._speech(size),
                if (!widget.quiet) ..._choices(size),
              ],
            ),
          ),
        ],
      );
      // the camera: everything -- the painting and the words on it -- moves
      // together, so nothing drifts out of place
      return ClipRect(
        child: ListenableBuilder(
          listenable: widget.engine,
          builder: (context, child) {
            final e = widget.engine;
            final z = e.camZoom;
            if ((z - 1).abs() < 1e-4) return child!;
            final f = Offset(e.camFocus.dx * size.width, e.camFocus.dy * size.height);
            final m = Matrix4.identity()
              ..translateByDouble(size.width / 2 - f.dx * z, size.height / 2 - f.dy * z, 0, 1)
              ..scaleByDouble(z, z, 1, 1);
            return Transform(transform: m, child: child);
          },
          child: stage,
        ),
      );
    });
  }

  // ---------------------------------------------------------- formulas

  /// Typeset (LaTeX) text on the stage: `math` props, and the labels of the
  /// graph kit -- a curve's equation, a point's name, a tangent's slope
  /// (`{slope}` is filled in live as the point slides). Built once per
  /// distinct text, not every frame.
  final Map<String, Widget> _typeset = {};

  Widget _tex(String tex, double fontSize, Color color) {
    final key = '$fontSize|${color.toARGB32()}|$tex';
    return _typeset.putIfAbsent(key, () {
      if (_typeset.length > 200) _typeset.clear();
      return Math.tex(
        tex,
        mathStyle: MathStyle.text,
        textStyle: TextStyle(fontSize: fontSize, color: color),
        onErrorFallback: (_) => Text(tex, style: TextStyle(fontSize: fontSize, color: color)),
      );
    });
  }

  List<Widget> _formulas(Size size) {
    final e = widget.engine;
    final out = <Widget>[];
    for (final p in e.props.values) {
      final tex = p.tex;
      if (tex == null || tex.isEmpty) continue;
      final since = isGraphKind(p.kind) ? e.fadingSince(p) : p.diedAt;
      final fade = since == null ? 1.0 : 1 - ((e.time - since) / 0.45).clamp(0.0, 1.0);
      final age = e.time - p.bornAt;
      final dim = (isGraphKind(p.kind) ? e.axesOf(p) ?? p : p).dim.at(e.time);
      final opacity = (fade * dim * (age / 0.35).clamp(0.0, 1.0)).clamp(0.0, 1.0);
      if (opacity <= 0) continue;
      final color = propColor(p.color, Paper.ink);
      Offset? at;
      var shift = Offset.zero; // FractionalTranslation of the label itself
      var fontSize = 15.0;
      var text = tex;
      switch (p.kind) {
        case PropKind.math:
          at = e.propAt(p);
          shift = const Offset(-0.5, -0.5);
          fontSize = 18 * p.size;
        case PropKind.axes:
          at = e.graphToStage(p, p.xmin, p.ymax);
          shift = const Offset(0, -1.15);
          fontSize = 13;
        case PropKind.plot:
          final axes = e.axesOf(p), f = p.formula;
          if (axes == null || f == null || age < 1.0) break;
          final gx = axes.xmin + 0.82 * (axes.xmax - axes.xmin);
          final gy = f(gx).clamp(axes.ymin, axes.ymax);
          at = e.graphToStage(axes, gx, gy.isFinite ? gy : axes.ymax);
          shift = const Offset(-1.0, -1.3);
        case PropKind.dot:
          final d = e.dotPoint(p);
          if (d != null) at = d + Offset(10 / size.width, -8 / size.height);
          shift = const Offset(0, -1);
        case PropKind.tangent:
          final d = e.dotPoint(e.parentOf(p));
          final slope = e.slopeUnder(p);
          if (d == null) break;
          at = d + Offset(14 / size.width, 12 / size.height);
          if (slope != null) {
            final v = (slope * 10).round() / 10;
            text = tex.replaceAll('{slope}', (v == 0 ? 0.0 : v).toStringAsFixed(1));
          }
          fontSize = 13.5;
        default:
          break;
      }
      if (at == null) continue;
      out.add(Positioned(
        key: ValueKey('tex-${p.id}'),
        left: at.dx * size.width,
        top: at.dy * size.height,
        child: IgnorePointer(
          child: Opacity(
            opacity: opacity,
            child: FractionalTranslation(translation: shift, child: _tex(text, fontSize, color)),
          ),
        ),
      ));
    }
    return out;
  }

  // ------------------------------------------------------------ photos

  /// The learner's own picture, held up on the stage where the director put
  /// a `photo` prop: a framed print that pops in, dims with its scene and
  /// fades when removed. Arrows and labels the director adds are painted or
  /// typeset over it.
  List<Widget> _photos(Size size) {
    final e = widget.engine;
    final out = <Widget>[];
    for (final p in e.props.values) {
      if (p.kind != PropKind.photo) continue;
      final age = e.time - p.bornAt;
      final fade = p.diedAt == null ? 1.0 : 1 - ((e.time - p.diedAt!) / 0.45).clamp(0.0, 1.0);
      final pop = Curves.easeOutBack.transform((age / 0.4).clamp(0.0, 1.0));
      final opacity = (fade * p.dim.at(e.time) * (age / 0.25).clamp(0.0, 1.0)).clamp(0.0, 1.0);
      if (opacity <= 0) continue;
      final at = e.propAt(p);
      final k = e.propScale(p) * p.scale.at(e.time);
      final width = (StageEngine.photoWidth * size.width * p.size * k).clamp(40.0, size.width * 0.6);
      final bytes = e.photo;
      out.add(Positioned(
        key: ValueKey('photo-${p.id}'),
        left: at.dx * size.width,
        top: at.dy * size.height,
        child: IgnorePointer(
          child: Opacity(
            opacity: opacity,
            child: FractionalTranslation(
              translation: const Offset(-0.5, -0.5),
              child: Transform.scale(
                scale: 0.6 + 0.4 * pop,
                child: Transform.rotate(
                  angle: -0.035,
                  child: Container(
                    width: width,
                    padding: const EdgeInsets.all(5),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(6),
                      boxShadow: const [BoxShadow(color: Color(0x33000000), blurRadius: 12, offset: Offset(0, 5))],
                    ),
                    child: ClipRRect(
                      borderRadius: BorderRadius.circular(3),
                      child: bytes != null
                          ? ConstrainedBox(
                              constraints: BoxConstraints(maxHeight: width * 1.1),
                              child: Image.memory(bytes, fit: BoxFit.contain, gaplessPlayback: true),
                            )
                          : SizedBox(
                              height: width * 0.7,
                              child: const Center(
                                  child: Icon(Icons.image_outlined, size: 32, color: Color(0xFFB0A89A))),
                            ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ));
    }
    return out;
  }

  // ------------------------------------------------------------ speech

  List<Widget> _speech(Size size) {
    final e = widget.engine;
    final text = e.bubbleText;
    if (text == null || text.isEmpty) return const [];
    final place = bubblePlace(e, size);
    final bubble = _Bubble(
      key: ValueKey('stage-bubble${e.asking ? '-question' : ''}'),
      text: text,
      question: e.asking,
      tailOnLeft: place.onRight,
    );
    return [
      Positioned(
        left: place.onRight ? place.side : null,
        right: place.onRight ? null : place.side,
        bottom: place.bottom,
        child: ConstrainedBox(constraints: BoxConstraints(maxWidth: place.maxWidth), child: bubble),
      ),
    ];
  }

  // ----------------------------------------------------------- choices

  List<Widget> _choices(Size size) {
    final e = widget.engine;
    final q = e.question;
    if (q == null || q.choices.isEmpty) return const [];
    final groundPx = kGroundY * size.height;
    final x = e.blobPos.dx.clamp(0.0, 1.0);
    return [
      Positioned(
        key: const ValueKey('stage-choices'),
        top: groundPx + 12,
        bottom: 6,
        left: 12,
        right: 12,
        child: Align(
          alignment: Alignment((x * 2 - 1).clamp(-1.0, 1.0), -1),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 340),
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (var i = 0; i < q.choices.length; i++)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: _PopIn(
                        key: ValueKey('${q.key}-${q.choices[i].id}'),
                        delay: i * 90,
                        child: _ChoiceBubble(
                          key: ValueKey('stage-choice-${q.choices[i].id}'),
                          choice: q.choices[i],
                          onTap: widget.onChoice == null ? null : () => widget.onChoice!(q.choices[i]),
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
      ),
    ];
  }
}

/// The blob's speech bubble: white card, a little tail toward the head.
/// A question gets the accent border so it reads as "I'm asking you".
class _Bubble extends StatelessWidget {
  const _Bubble({super.key, required this.text, required this.question, required this.tailOnLeft});
  final String text;
  final bool question;
  final bool tailOnLeft;

  @override
  Widget build(BuildContext context) {
    return _PopIn(
      key: ValueKey(text),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(
          color: Paper.card,
          border: Border.all(color: question ? Paper.accentLine : Paper.border, width: question ? 1.5 : 1),
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(14),
            topRight: const Radius.circular(14),
            bottomLeft: Radius.circular(tailOnLeft ? 3 : 14),
            bottomRight: Radius.circular(tailOnLeft ? 14 : 3),
          ),
          boxShadow: const [BoxShadow(color: Color(0x14000000), blurRadius: 10, offset: Offset(0, 3))],
        ),
        child: Text(text, style: sans(13.5, height: 1.4, weight: question ? FontWeight.w500 : FontWeight.w400)),
      ),
    );
  }
}

/// One choice beneath the blob, styled as the reply the person could send.
class _ChoiceBubble extends StatefulWidget {
  const _ChoiceBubble({super.key, required this.choice, required this.onTap});
  final StageChoice choice;
  final VoidCallback? onTap;

  @override
  State<_ChoiceBubble> createState() => _ChoiceBubbleState();
}

class _ChoiceBubbleState extends State<_ChoiceBubble> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final enabled = widget.onTap != null;
    return MouseRegion(
      cursor: enabled ? SystemMouseCursors.click : SystemMouseCursors.basic,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 140),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          transform: Matrix4.translationValues(0, _hover && enabled ? -2 : 0, 0),
          decoration: BoxDecoration(
            color: _hover && enabled ? Paper.accentSoft : Paper.card,
            border: Border.all(color: _hover && enabled ? Paper.accent : Paper.accentLine, width: 1.5),
            borderRadius: const BorderRadius.only(
              topLeft: Radius.circular(14),
              topRight: Radius.circular(14),
              bottomLeft: Radius.circular(14),
              bottomRight: Radius.circular(4),
            ),
          ),
          child: Text(
            widget.choice.text,
            style: sans(13.5, height: 1.4, color: enabled ? Paper.ink : Paper.muted),
          ),
        ),
      ),
    );
  }
}

/// Springs its child in (scale + fade) once, after [delay] ms.
class _PopIn extends StatelessWidget {
  const _PopIn({super.key, required this.child, this.delay = 0});
  final Widget child;
  final int delay;

  @override
  Widget build(BuildContext context) {
    final total = 380 + delay;
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: 1),
      duration: Duration(milliseconds: total),
      builder: (context, v, child) {
        final t = ((v * total - delay) / 380).clamp(0.0, 1.0);
        final s = 0.6 + 0.4 * Curves.elasticOut.transform(t);
        return Opacity(opacity: Curves.easeOut.transform(t), child: Transform.scale(scale: s, child: child));
      },
      child: child,
    );
  }
}
