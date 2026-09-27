import 'dart:async';

import 'package:flutter/material.dart';

import '../chat_controller.dart';
import '../models.dart';
import '../stage/engine.dart';
import '../stage/script.dart';
import '../stage/stage_view.dart';
import '../theme.dart';

/// The stage: the slime character that acts out the conversation (usable
/// in any mode via the "Animations" knob -- see AppState.showStagePanel).
///
/// While it's open, the chat's ambiguity options live HERE instead of in the
/// chat: the blob asks the question with a "?" over its head and the options
/// sit beneath it as reply bubbles (sandbox_screen.dart hides the options turn
/// from the message list for as long as the stage is showing). A click goes
/// through the same `ChatController.pickOption` the inline chips used.
///
/// Toggled on/off by the knob; toggled between full and minimized by
/// [onCollapse] independent of that, so someone can glance the panel away
/// without turning the feature off -- and a minimized stage hands the options
/// back to the chat.
class StagePanel extends StatefulWidget {
  const StagePanel({super.key, required this.onCollapse, this.compact = false, this.chat, this.engine});

  final VoidCallback onCollapse;

  /// True on a phone: laid out as a short strip above the chat instead of
  /// a tall column beside it (see sandbox_screen.dart's compact layout).
  final bool compact;

  /// The chat this stage performs for. Null = the stage runs on its own
  /// (demo skit only).
  final ChatController? chat;

  /// Tests pass their own engine to drive its clock by hand (a test binding
  /// doesn't run the stage's ticker). Null: the panel makes and owns one.
  @visibleForTesting
  final StageEngine? engine;

  @override
  State<StagePanel> createState() => _StagePanelState();
}

class _StagePanelState extends State<StagePanel> {
  late final StageEngine _engine = widget.engine ?? StageEngine();
  late final bool _ownsEngine = widget.engine == null;
  ChatStatus? _lastStatus;

  /// The answered turn the stage is acting out (its quick check belongs to it).
  int? _turn;

  /// The performance arriving now, and the last complete one -- what the
  /// replay button plays again.
  List<Map<String, dynamic>> _recording = [];
  List<Map<String, dynamic>> _lastPerformance = const [];

  /// "Keep in mind": the takeaways the slime pinned, for this chat, oldest
  /// first. They stay after the skit moves on.
  final List<String> _notes = [];

  /// Takeaways on their way down: each pops out beside the slime as a box
  /// and glides into "Keep in mind", where it lands in [_notes].
  final List<_Flight> _flights = [];
  int _nextFlight = 0;
  final _panelKey = GlobalKey();
  final _stageKey = GlobalKey();
  final _notesKey = GlobalKey();

  StreamSubscription<ServerEvent>? _stageSub;

  @override
  void initState() {
    super.initState();
    _engine.onNote = _launchNote;
    _engine.onChecked = (question, choices, picked, answer) {
      final turn = _turn;
      if (turn == null) return;
      widget.chat?.recordStageCheck(
        turnIndex: turn,
        question: question,
        choices: [for (final c in choices) (c.id, c.text)],
        picked: picked,
        answer: answer,
      );
    };
    _attach(widget.chat);
  }

  @override
  void didUpdateWidget(covariant StagePanel old) {
    super.didUpdateWidget(old);
    if (old.chat != widget.chat) {
      _detach(old.chat);
      _engine.withdrawQuestion();
      _lastStatus = null;
      _notes.clear(); // another chat, other takeaways
      _attach(widget.chat);
    }
  }

  /// While this panel shows, the chat asks the server to perform each turn
  /// here; the performance streams in on `stageEvents`.
  void _attach(ChatController? chat) {
    if (chat == null) return;
    chat.addListener(_syncWithChat);
    chat.stageEnabled = true;
    _stageSub = chat.stageEvents.listen(_onStageEvent);
    _syncWithChat();
  }

  void _detach(ChatController? chat) {
    if (chat == null) return;
    chat.removeListener(_syncWithChat);
    chat.stageEnabled = false;
    _stageSub?.cancel();
    _stageSub = null;
  }

  void _onStageEvent(ServerEvent event) {
    switch (event) {
      case StageStart(:final turnIndex):
        // One animation of the whole explanation, starting with the answer.
        _turn = turnIndex;
        _recording = [];
        _engine.beginLive();
        // the answer just started: perk up at once, before the director's
        // first beats arrive
        for (final beat in const [
          {'do': 'emote', 'mood': 'excited'},
          {'do': 'jump'},
        ]) {
          _engine.enqueue(StageAction.fromJson(beat));
        }
      case StageActionEvent(:final action):
        _recording.add(action);
        _engine.enqueue(StageAction.fromJson(action));
      case StageEnd():
        _engine.endLive();
        if (_recording.isNotEmpty) {
          setState(() {
            _lastPerformance = List.unmodifiable(_recording);
          });
        }
      case RecalledEvent(:final retracted):
        _engine.recalled(retracted: retracted);
      default:
        break;
    }
  }

  /// A takeaway: a box pops out next to the slime's head, then floats down
  /// into "Keep in mind" (or straight in, if the layout isn't there yet).
  void _launchNote(String text) {
    if (!mounted || _notes.contains(text) || _flights.any((f) => f.text == text)) return;
    final panel = _panelKey.currentContext?.findRenderObject() as RenderBox?;
    final stage = _stageKey.currentContext?.findRenderObject() as RenderBox?;
    if (panel == null || stage == null || !panel.hasSize || !stage.hasSize) {
      setState(() => _notes.add(text));
      return;
    }
    final stageTopLeft = stage.localToGlobal(Offset.zero, ancestor: panel);
    final head = _engine.blobTop;
    final from = stageTopLeft + Offset(head.dx * stage.size.width, head.dy * stage.size.height - 40);
    final list = _notesKey.currentContext?.findRenderObject() as RenderBox?;
    final to = list != null && list.hasSize
        ? list.localToGlobal(Offset.zero, ancestor: panel) + Offset(16, list.size.height - 44)
        : Offset(16, panel.size.height - 60);
    setState(() => _flights.add(_Flight(_nextFlight++, text, from, to)));
  }

  void _landed(_Flight flight) {
    if (!mounted) return;
    setState(() {
      _flights.remove(flight);
      if (!_notes.contains(flight.text)) _notes.add(flight.text);
    });
  }

  @override
  void dispose() {
    _detach(widget.chat);
    _engine.stop();
    _engine.onNote = null;
    _engine.onChecked = null;
    if (_ownsEngine) _engine.dispose();
    super.dispose();
  }

  /// The latest tutor turn, if it's an options offer still awaiting a pick.
  ChatMessage? _openOffer(ChatController chat) {
    for (var i = chat.messages.length - 1; i >= 0; i--) {
      final m = chat.messages[i];
      if (m.role != Role.tutor) continue;
      if (m.pending) continue;
      return m.hasOptions && m.optionsOpen && !m.optionsResolved ? m : null;
    }
    return null;
  }

  void _syncWithChat() {
    final chat = widget.chat;
    if (chat == null) return;

    final offer = _openOffer(chat);
    if (offer != null) {
      _engine.ask(StageQuestion(
        key: 'msg-${offer.id}',
        text: offer.text.isEmpty ? 'Which did you mean?' : offer.text,
        choices: [for (final o in offer.options) StageChoice(id: o.id, text: o.text)],
      ));
    } else if (_engine.question?.key.startsWith('msg-') ?? false) {
      _engine.withdrawQuestion();
    }

    // Mood follows the turn: thinking while it waits, eyes on the chat while
    // the answer is written (it doesn't pretend to be the one talking), a
    // pleased little bounce when it lands.
    final status = chat.status;
    _engine.talking = false;
    _engine.watchingChat = status == ChatStatus.thinking || status == ChatStatus.streaming;
    if (status != _lastStatus && !_engine.running && !_engine.asking) {
      switch (status) {
        case ChatStatus.thinking:
          _engine.mood = Mood.thinking;
        case ChatStatus.streaming:
          _engine.mood = Mood.happy;
        case ChatStatus.ready when _lastStatus == ChatStatus.streaming:
          _engine.mood = Mood.neutral;
          _engine.impulse(0.25);
        case ChatStatus.disconnected:
          _engine.mood = Mood.sad;
        default:
          break;
      }
    }
    _lastStatus = status;
  }

  void _onChoice(StageChoice choice) {
    final q = _engine.question;
    final chat = widget.chat;
    if (q == null) return;
    if (q.key.startsWith('msg-') && chat != null) {
      final id = int.tryParse(q.key.substring(4));
      final message = chat.messages.where((m) => m.id == id).firstOrNull;
      final option = message?.options.where((o) => o.id == choice.id).firstOrNull;
      if (message == null || option == null) return;
      _engine.answer(choice.id);
      chat.pickOption(message, option);
    } else {
      _engine.answer(choice.id);
    }
  }

  bool get _canChoose {
    final q = _engine.question;
    if (q == null) return false;
    if (q.key.startsWith('msg-')) return widget.chat?.canSend ?? false;
    return true;
  }

  bool get _canReplay => _lastPerformance.isNotEmpty;

  /// Play the last answer's performance again from the top -- or, while a
  /// skit is playing, stop it. Notes already pinned aren't pinned twice.
  void _replayOrStop() {
    if (_engine.running) {
      _engine.reset();
      return;
    }
    if (!_canReplay) return;
    _engine.beginLive();
    for (final action in _lastPerformance) {
      _engine.enqueue(StageAction.fromJson(action));
    }
    _engine.endLive();
  }

  @override
  Widget build(BuildContext context) {
    final compact = widget.compact;
    final header = Padding(
      padding: EdgeInsets.fromLTRB(16, compact ? 8 : 16, 10, 0),
      child: Row(
        children: [
          Text('STAGE', style: mono(10)),
          const Spacer(),
          ListenableBuilder(
            listenable: _engine,
            builder: (context, _) => IconButton(
              key: const ValueKey('stage-replay'),
              tooltip: _engine.running
                  ? 'Stop the animation'
                  : (_canReplay ? 'Replay the animation' : 'Nothing to replay yet'),
              visualDensity: VisualDensity.compact,
              // the chat's own question (its options) keeps the stage
              onPressed: (_engine.asking && !_engine.running) || (!_engine.running && !_canReplay)
                  ? null
                  : _replayOrStop,
              icon: Icon(
                _engine.running ? Icons.stop_rounded : Icons.replay_rounded,
                size: 18,
                color: Paper.faint,
              ),
            ),
          ),
          IconButton(
            key: const ValueKey('stage-panel-collapse'),
            tooltip: 'Minimize stage',
            visualDensity: VisualDensity.compact,
            onPressed: widget.onCollapse,
            icon: Icon(
              compact ? Icons.keyboard_arrow_up_rounded : Icons.chevron_left_rounded,
              size: 18,
              color: Paper.faint,
            ),
          ),
        ],
      ),
    );

    final stage = KeyedSubtree(key: _stageKey, child: ListenableBuilder(
      // The engine too: a skit's own ask makes choices clickable without the
      // chat changing at all.
      listenable: Listenable.merge([_engine, ?widget.chat]),
      builder: (context, _) => StageView(engine: _engine, onChoice: _canChoose ? _onChoice : null),
    ));

    final panel = Container(
      key: const ValueKey('stage-panel'),
      decoration: BoxDecoration(
        color: Paper.sliver,
        border: Border(
          right: compact ? BorderSide.none : const BorderSide(color: Paper.border),
          bottom: compact ? const BorderSide(color: Paper.border) : BorderSide.none,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          header,
          compact ? SizedBox(height: 280, child: stage) : Expanded(child: stage),
          if (_notes.isNotEmpty || _flights.isNotEmpty)
            KeyedSubtree(key: _notesKey, child: _KeepInMind(notes: _notes, compact: compact)),
        ],
      ),
    );
    return Stack(
      key: _panelKey,
      children: [
        panel,
        for (final f in _flights)
          _FlyingNote(key: ValueKey('flying-note-${f.id}'), flight: f, onLanded: () => _landed(f)),
      ],
    );
  }
}

/// The takeaways the slime pinned, below the stage, where they stay.
class _KeepInMind extends StatelessWidget {
  const _KeepInMind({required this.notes, required this.compact});
  final List<String> notes;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    return Container(
      key: const ValueKey('keep-in-mind'),
      constraints: BoxConstraints(maxHeight: compact ? 120 : 220),
      decoration: const BoxDecoration(
        color: Paper.card,
        border: Border(top: BorderSide(color: Paper.border)),
      ),
      child: ListView(
        shrinkWrap: true,
        reverse: true, // newest in view
        padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
        children: [
          for (final (i, n) in notes.indexed.toList().reversed)
            TweenAnimationBuilder<double>(
              key: ValueKey('note-$i'),
              tween: Tween(begin: 0, end: 1),
              duration: const Duration(milliseconds: 120),
              builder: (context, t, child) => Opacity(opacity: t, child: child),
              child: Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _NoteBox(text: n),
              ),
            ),
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Text('KEEP IN MIND', style: mono(9.5, color: Paper.accentDark)),
          ),
        ],
      ),
    );
  }
}

/// One takeaway, as a box.
class _NoteBox extends StatelessWidget {
  const _NoteBox({required this.text});
  final String text;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(10, 8, 12, 8),
        decoration: BoxDecoration(
          color: Paper.card,
          border: Border.all(color: Paper.accentLine),
          borderRadius: BorderRadius.circular(10),
          boxShadow: const [BoxShadow(color: Color(0x14000000), blurRadius: 6, offset: Offset(0, 2))],
        ),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Padding(
            padding: EdgeInsets.only(top: 2),
            child: Icon(Icons.push_pin_rounded, size: 14, color: Paper.accent),
          ),
          const SizedBox(width: 8),
          Expanded(child: Text(text, style: sans(13, color: Paper.ink, height: 1.4))),
        ]),
      );
}

class _Flight {
  _Flight(this.id, this.text, this.from, this.to);
  final int id;
  final String text;
  final Offset from, to;
}

/// A takeaway's trip: it pops out beside the slime, hangs there a beat so it
/// can be read, then glides down into "Keep in mind".
class _FlyingNote extends StatefulWidget {
  const _FlyingNote({super.key, required this.flight, required this.onLanded});
  final _Flight flight;
  final VoidCallback onLanded;

  @override
  State<_FlyingNote> createState() => _FlyingNoteState();
}

class _FlyingNoteState extends State<_FlyingNote> with SingleTickerProviderStateMixin {
  late final AnimationController _c =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 2600))
        ..forward().whenComplete(widget.onLanded);

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  static double _seg(double t, double a, double b) => ((t - a) / (b - a)).clamp(0.0, 1.0);

  /// 0-.18 zoom in past full size, .18-.32 settle, .32-.55 hover (readable),
  /// .55-.85 shrink while it falls, .85-1 zoom back up into its place.
  double _scale(double t) {
    if (t < 0.18) return 0.2 + 0.98 * Curves.easeOutCubic.transform(_seg(t, 0, 0.18));
    if (t < 0.32) return 1.18 - 0.18 * Curves.easeInOut.transform(_seg(t, 0.18, 0.32));
    if (t < 0.55) return 1.0;
    if (t < 0.85) return 1.0 - 0.3 * Curves.easeIn.transform(_seg(t, 0.55, 0.85));
    return 0.7 + 0.3 * Curves.elasticOut.transform(_seg(t, 0.85, 1.0));
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _c,
      builder: (context, child) {
        final t = _c.value;
        final fall = Curves.easeInOutCubic.transform(_seg(t, 0.55, 0.85));
        final hover = t >= 0.32 && t < 0.55 ? -3 * (1 - (2 * _seg(t, 0.32, 0.55) - 1).abs()) : 0.0;
        final at = Offset.lerp(widget.flight.from, widget.flight.to, fall)! + Offset(0, hover);
        final lift = t < 0.55 ? 1.0 : 1.0 - fall; // a deeper shadow while it floats
        return Positioned(
          left: at.dx,
          top: at.dy,
          child: Opacity(
            opacity: _seg(t, 0, 0.1),
            child: Transform.scale(
              scale: _scale(t),
              alignment: Alignment.topLeft,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(10),
                  boxShadow: [
                    BoxShadow(
                      color: Color.fromRGBO(0, 0, 0, 0.08 + 0.12 * lift),
                      blurRadius: 6 + 14 * lift,
                      offset: Offset(0, 2 + 6 * lift),
                    ),
                  ],
                ),
                child: child,
              ),
            ),
          ),
        );
      },
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 280),
        child: Material(color: Colors.transparent, child: _NoteBox(text: widget.flight.text)),
      ),
    );
  }
}
