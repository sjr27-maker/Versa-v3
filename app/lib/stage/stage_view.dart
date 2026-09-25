import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

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
  const StageView({super.key, required this.engine, this.onChoice, this.animate});

  final StageEngine engine;

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
      return ClipRect(
        child: Stack(
          children: [
            Positioned.fill(
              child: CustomPaint(key: const ValueKey('stage-canvas'), painter: StagePainter(widget.engine)),
            ),
            ListenableBuilder(
              listenable: widget.engine,
              builder: (context, _) => Stack(
                children: [
                  ..._speech(size),
                  ..._choices(size),
                ],
              ),
            ),
          ],
        ),
      );
    });
  }

  // ------------------------------------------------------------ speech

  List<Widget> _speech(Size size) {
    final e = widget.engine;
    final text = e.bubbleText;
    if (text == null || text.isEmpty) return const [];
    final pos = e.blobPos;
    final r = e.blobRadius;
    final headX = pos.dx * size.width;
    final headTop = e.blobTop.dy * size.height;
    // Beside the head (the "?" owns the space right above it), on whichever
    // side has more room.
    final onRight = headX < size.width * 0.55;
    const maxW = 280.0;
    final bottom = size.height - headTop - r * 0.3;
    final bubble = _Bubble(
      key: ValueKey('stage-bubble${e.asking ? '-question' : ''}'),
      text: text,
      question: e.asking,
      tailOnLeft: onRight,
    );
    return [
      if (onRight)
        Positioned(
          left: (headX + r * 0.9).clamp(8.0, size.width - 120),
          bottom: bottom.clamp(8.0, size.height - 40),
          child: ConstrainedBox(
            constraints: BoxConstraints(maxWidth: (size.width - headX - r * 0.9 - 12).clamp(110.0, maxW)),
            child: bubble,
          ),
        )
      else
        Positioned(
          right: (size.width - headX + r * 0.9).clamp(8.0, size.width - 120),
          bottom: bottom.clamp(8.0, size.height - 40),
          child: ConstrainedBox(
            constraints: BoxConstraints(maxWidth: (headX - r * 0.9 - 12).clamp(110.0, maxW)),
            child: bubble,
          ),
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
