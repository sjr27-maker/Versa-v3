import 'dart:async';

import 'package:flutter/material.dart';

import '../chat_controller.dart';
import '../models.dart';
import '../stage/engine.dart';
import '../stage/script.dart';
import '../stage/skits.dart';
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
  const StagePanel({super.key, required this.onCollapse, this.compact = false, this.chat});

  final VoidCallback onCollapse;

  /// True on a phone: laid out as a short strip above the chat instead of
  /// a tall column beside it (see sandbox_screen.dart's compact layout).
  final bool compact;

  /// The chat this stage performs for. Null = the stage runs on its own
  /// (demo skit only).
  final ChatController? chat;

  @override
  State<StagePanel> createState() => _StagePanelState();
}

class _StagePanelState extends State<StagePanel> {
  final _engine = StageEngine();
  ChatStatus? _lastStatus;
  int _nextDemo = 0;

  StreamSubscription<ServerEvent>? _stageSub;

  @override
  void initState() {
    super.initState();
    _attach(widget.chat);
  }

  @override
  void didUpdateWidget(covariant StagePanel old) {
    super.didUpdateWidget(old);
    if (old.chat != widget.chat) {
      _detach(old.chat);
      _engine.withdrawQuestion();
      _lastStatus = null;
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
      case StageStart():
        _engine.beginLive();
      case StageActionEvent(:final action):
        _engine.enqueue(StageAction.fromJson(action));
      case StageEnd():
        _engine.endLive();
      case RecalledEvent(:final retracted):
        _engine.recalled(retracted: retracted);
      default:
        break;
    }
  }

  @override
  void dispose() {
    _detach(widget.chat);
    _engine.stop();
    _engine.dispose();
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

    // Mood follows the turn: thinking while it waits, mouth going while the
    // answer streams, a pleased little bounce when it lands.
    final status = chat.status;
    // Mouth flaps with the answer only when it isn't busy performing
    // (a performance has its own faces).
    _engine.talking = status == ChatStatus.streaming && !_engine.running;
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

  void _toggleDemo() {
    if (_engine.running) {
      _engine.reset();
    } else {
      _engine.reset();
      final skits = demoSkits;
      _engine.play(skits[_nextDemo++ % skits.length]);
    }
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
              key: const ValueKey('stage-demo'),
              tooltip: _engine.running ? 'Stop the skit' : 'Play the demo skit',
              visualDensity: VisualDensity.compact,
              onPressed: _engine.asking && !_engine.running ? null : _toggleDemo,
              icon: Icon(
                _engine.running ? Icons.stop_rounded : Icons.play_arrow_rounded,
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

    final stage = ListenableBuilder(
      // The engine too: a skit's own ask makes choices clickable without the
      // chat changing at all.
      listenable: Listenable.merge([_engine, ?widget.chat]),
      builder: (context, _) => StageView(engine: _engine, onChoice: _canChoose ? _onChoice : null),
    );

    return Container(
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
        ],
      ),
    );
  }
}
