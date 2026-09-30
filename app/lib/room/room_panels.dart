import 'dart:async';

import 'package:flutter/material.dart';

import '../stage/engine.dart';
import '../stage/script.dart';
import '../stage/stage_view.dart';
import '../theme.dart';
import '../widgets/rich_text.dart';
import 'room_chat.dart';
import 'room_controller.dart';
import 'room_models.dart';

/// A titled box in the room's top area.
class RoomPanelFrame extends StatelessWidget {
  const RoomPanelFrame({super.key, required this.title, required this.child, this.trailing, this.padded = true});

  final String title;
  final Widget child;
  final Widget? trailing;
  final bool padded;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(12),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 9, 8, 6),
            child: Row(children: [
              Expanded(child: Text(title.toUpperCase(), style: mono(10, weight: FontWeight.w700))),
              ?trailing,
            ]),
          ),
          Expanded(
            child: padded ? Padding(padding: const EdgeInsets.fromLTRB(12, 0, 12, 10), child: child) : child,
          ),
        ],
      ),
    );
  }
}

/// The slime, acting out Versa's explanations for the whole room at once
/// (the server streams the same performance to every device).
class RoomStage extends StatefulWidget {
  const RoomStage({super.key, required this.controller});
  final RoomController controller;

  @override
  State<RoomStage> createState() => _RoomStageState();
}

class _RoomStageState extends State<RoomStage> {
  final _engine = StageEngine();
  StreamSubscription<RoomEvent>? _sub;

  /// The quiz task whose question is up on the stage (its option set id).
  String? _askedSet;

  /// Messages already seen, so the slime reacts to each graded tap once.
  int _seenSeq = 0;

  @override
  void initState() {
    super.initState();
    _sub = widget.controller.stageEvents.listen(_onEvent);
    widget.controller.addListener(_onRoom);
    _engine.addListener(_maybeAsk);
    final messages = widget.controller.messages;
    _seenSeq = messages.isEmpty ? 0 : messages.last.seq;
  }

  /// A race, or else this person's quiz task, goes up on the stage as the
  /// slime's question -- but only once whatever it is acting out has
  /// finished, so a task never cuts an explanation short. Answered anywhere
  /// else (For you, the chat), or won by someone, it comes down.
  RoomOptionSet? get _asking => widget.controller.board.openRace ?? widget.controller.board.myQuiz;

  void _maybeAsk() {
    final room = widget.controller;
    final quiz = _asking;
    if (quiz == null || room.pickedSetIds.contains(quiz.setId)) {
      if (_askedSet != null && _engine.question?.key == 'task-$_askedSet') _engine.withdrawQuestion();
      _askedSet = null;
      return;
    }
    if (_askedSet == quiz.setId || _engine.running || _engine.asking) return;
    _askedSet = quiz.setId;
    _engine.ask(StageQuestion(
      key: 'task-${quiz.setId}',
      text: quiz.race ? 'RACE! ${quiz.prompt}' : quiz.prompt,
      choices: [for (final o in quiz.options) StageChoice(id: o.id, text: o.text)],
    ));
  }

  void _onChoice(StageChoice choice) {
    final quiz = _asking;
    if (quiz == null || _engine.question?.key != 'task-${quiz.setId}') return;
    final option = quiz.options.where((o) => o.id == choice.id).firstOrNull;
    if (option == null) return;
    _engine.answer(choice.id);
    widget.controller.pick(quiz, option);
  }

  /// The server graded one of MY taps on a quiz task: the slime cheers or
  /// kindly shows the right answer -- unless it is busy acting something out.
  /// A race's result is announced for everyone.
  void _reactToGrades() {
    final room = widget.controller;
    for (final m in room.messages) {
      if (m.seq <= _seenSeq) continue;
      _seenSeq = m.seq;
      if (_engine.running) continue;
      if (m.kind == 'progress' && m.meta.containsKey('race_set_id')) {
        final winner = m.meta['winner'] as String?;
        final me = winner != null && m.meta['winner_id'] == room.memberId;
        _engine.play(parseScript([
          {'do': 'emote', 'mood': winner == null ? 'surprised' : 'excited'},
          if (winner != null) {'do': 'effect', 'kind': 'confetti', 'x': 0.5, 'y': 0.3},
          {
            'do': 'say',
            'text': winner == null
                ? "Nobody! It was \u201c${m.meta['right_answer'] ?? ''}\u201d."
                : (me ? 'You got it FIRST! +3' : '$winner got it first!'),
          },
          {'do': 'jump'},
        ]));
        continue;
      }
      if (m.kind == 'pick' && m.isMine(room.memberId) && m.meta['race'] == true && m.meta['correct'] != true) {
        _engine.play(parseScript(const [
          {'do': 'emote', 'mood': 'surprised'},
          {'do': 'say', 'text': 'Not that one -- the race is still on!'},
        ]));
        continue;
      }
      if (m.kind != 'pick' || !m.isMine(room.memberId) || m.meta['quiz'] != true) continue;
      final right = m.meta['correct'] == true;
      _engine.play(parseScript(right
          ? const [
              {'do': 'emote', 'mood': 'proud'},
              {'do': 'effect', 'kind': 'sparks', 'x': 0.5, 'y': 0.35},
              {'do': 'say', 'text': "Yes! That's it."},
              {'do': 'jump'},
            ]
          : [
              {'do': 'emote', 'mood': 'thinking'},
              {'do': 'say', 'text': "Not quite -- it's \u201c${m.meta['right_answer'] ?? ''}\u201d."},
              {'do': 'emote', 'mood': 'happy'},
            ]));
    }
  }

  void _onEvent(RoomEvent e) {
    switch (e) {
      case RoomStageStart():
        _engine.beginLive();
      case RoomStageAction(:final action):
        _engine.enqueue(StageAction.fromJson(action));
      case RoomStageEnd():
        _engine.endLive();
      default:
        break;
    }
  }

  bool _wasTyping = false;

  void _onRoom() {
    _reactToGrades();
    _maybeAsk();
    final typing = widget.controller.versaTyping;
    if (typing == _wasTyping || _engine.running) {
      _wasTyping = typing;
      return;
    }
    _wasTyping = typing;
    _engine.mood = typing ? Mood.thinking : Mood.neutral;
    if (!typing) _engine.impulse(0.25);
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onRoom);
    _engine.removeListener(_maybeAsk);
    _sub?.cancel();
    _engine.stop();
    _engine.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      key: const ValueKey('room-stage'),
      color: Paper.sliver,
      child: ListenableBuilder(
        listenable: _engine,
        builder: (context, _) => StageView(
          engine: _engine,
          onChoice: (_engine.question?.key.startsWith('task-') ?? false) && widget.controller.isLive
              ? _onChoice
              : null,
        ),
      ),
    );
  }
}

/// Top-left beside the stage: everyone's current task and Versa's latest
/// questions to the group.
class RoomBoardPanel extends StatelessWidget {
  const RoomBoardPanel({super.key, required this.controller});
  final RoomController controller;

  @override
  Widget build(BuildContext context) {
    final board = controller.board;
    final meId = controller.memberId;
    final questions = [
      for (final m in controller.messages.reversed)
        if (m.fromVersa && m.kind == 'question' && (!m.private || m.toMemberId == meId)) m,
    ].take(3).toList();
    return ListView(
      key: const ValueKey('room-board'),
      padding: EdgeInsets.zero,
      children: [
        if (board.parts.isNotEmpty) ...[
          _PartsProgress(parts: board.parts),
          const SizedBox(height: 12),
        ],
        if (board.scores.any((s) => s.points > 0)) ...[
          _Scoreboard(scores: board.scores, meName: board.members.where((m) => m.id == meId).firstOrNull?.name),
          const SizedBox(height: 12),
        ],
        Text('Tasks', style: sans(12.5, weight: FontWeight.w700)),
        const SizedBox(height: 6),
        if (board.members.isEmpty) Text('Nobody here yet.', style: sans(12.5, color: Paper.muted)),
        for (final member in board.members) _MemberTaskRow(member: member, board: board, isMe: member.id == meId),
        const SizedBox(height: 12),
        Text('Questions', style: sans(12.5, weight: FontWeight.w700)),
        const SizedBox(height: 6),
        if (questions.isEmpty)
          Text('Versa\'s questions to the group show up here.', style: sans(12.5, color: Paper.muted)),
        for (final q in questions)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Padding(
                padding: EdgeInsets.only(top: 2),
                child: Icon(Icons.help_outline_rounded, size: 14, color: Paper.accent),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  '${q.toMemberId == null ? '' : '→ ${q.toMemberId == meId ? 'you' : q.toName}: '}${q.text}',
                  style: sans(12.5, height: 1.4),
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ]),
          ),
      ],
    );
  }
}

/// Friendly rivalry: points from races won (3) and tasks answered right (1).
class _Scoreboard extends StatelessWidget {
  const _Scoreboard({required this.scores, this.meName});
  final List<RoomScore> scores;
  final String? meName;

  @override
  Widget build(BuildContext context) {
    return Column(
      key: const ValueKey('room-scores'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Scoreboard', style: sans(12.5, weight: FontWeight.w700)),
        const SizedBox(height: 6),
        for (final (i, s) in scores.indexed)
          Padding(
            padding: const EdgeInsets.only(bottom: 3),
            child: Row(children: [
              SizedBox(
                width: 22,
                child: i == 0 && s.points > 0
                    ? Icon(Icons.emoji_events_rounded, size: 15, color: const Color(0xFFE2B33C))
                    : Text('${i + 1}', style: mono(10, color: Paper.faint)),
              ),
              Expanded(
                child: Text(s.name == meName ? '${s.name} (you)' : s.name,
                    style: sans(12.5, weight: s.name == meName ? FontWeight.w700 : FontWeight.w500)),
              ),
              if (s.wins > 0) ...[
                Icon(Icons.bolt_rounded, size: 13, color: Paper.accent),
                Text('${s.wins}', style: sans(11, color: Paper.muted)),
                const SizedBox(width: 8),
              ],
              TweenAnimationBuilder<int>(
                tween: IntTween(end: s.points),
                duration: const Duration(milliseconds: 500),
                builder: (context, v, _) =>
                    Text('$v', key: ValueKey('score-${s.name}'), style: sans(13, weight: FontWeight.w700)),
              ),
            ]),
          ),
      ],
    );
  }
}

/// Where the group is in the topic: each part covered, the one they're on,
/// and what's ahead -- a part is done when Versa has taught all it holds.
class _PartsProgress extends StatelessWidget {
  const _PartsProgress({required this.parts});
  final List<RoomPartProgress> parts;

  @override
  Widget build(BuildContext context) {
    final done = parts.where((p) => p.done).length;
    return Column(
      key: const ValueKey('room-parts'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(children: [
          Text('Topic', style: sans(12.5, weight: FontWeight.w700)),
          const Spacer(),
          Text('$done/${parts.length} parts', style: sans(11, color: done > 0 ? Paper.olive : Paper.faint)),
        ]),
        const SizedBox(height: 6),
        ClipRRect(
          borderRadius: BorderRadius.circular(3),
          child: TweenAnimationBuilder<double>(
            tween: Tween(end: parts.isEmpty ? 0 : done / parts.length),
            duration: const Duration(milliseconds: 600),
            curve: Curves.easeOutCubic,
            builder: (context, value, _) => LinearProgressIndicator(
              value: value,
              minHeight: 5,
              color: Paper.olive,
              backgroundColor: Paper.border,
            ),
          ),
        ),
        const SizedBox(height: 6),
        for (final p in parts)
          Padding(
            padding: const EdgeInsets.only(bottom: 2),
            child: Row(children: [
              Icon(
                p.done
                    ? Icons.check_circle_rounded
                    : (p.current ? Icons.play_circle_fill_rounded : Icons.radio_button_unchecked_rounded),
                size: 13,
                color: p.done ? Paper.olive : (p.current ? Paper.accent : Paper.faint),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(p.title,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: sans(11.5,
                        color: p.current ? Paper.ink : Paper.muted,
                        weight: p.current ? FontWeight.w600 : FontWeight.w400)),
              ),
            ]),
          ),
      ],
    );
  }
}

class _MemberTaskRow extends StatelessWidget {
  const _MemberTaskRow({required this.member, required this.board, required this.isMe});
  final RoomMemberInfo member;
  final RoomBoard board;
  final bool isMe;

  @override
  Widget build(BuildContext context) {
    final tasks = board.tasksOf(member.id);
    final current = board.currentTaskOf(member.id);
    final done = tasks.where((t) => t.done).length;
    return Padding(
      key: ValueKey('board-member-${member.name}'),
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          RoomAvatar(name: member.name, size: 24, online: member.online),
          const SizedBox(width: 8),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Flexible(
                  child: Text(isMe ? '${member.name} (you)' : member.name,
                      overflow: TextOverflow.ellipsis, style: sans(12.5, weight: FontWeight.w600)),
                ),
                if (tasks.isNotEmpty) ...[
                  const SizedBox(width: 6),
                  Text('$done/${tasks.length} done', style: sans(11, color: done > 0 ? Paper.olive : Paper.faint)),
                ],
              ]),
              Text(
                current?.description ?? (tasks.isEmpty ? 'No task yet' : 'All tasks done'),
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: sans(12, color: current == null ? Paper.faint : Paper.body, height: 1.35),
              ),
            ]),
          ),
        ],
      ),
    );
  }
}

/// Top-right: only for this person -- their task and the options Versa is
/// waiting for them to click.
class ForYouPanel extends StatelessWidget {
  const ForYouPanel({super.key, required this.controller});
  final RoomController controller;

  @override
  Widget build(BuildContext context) {
    final board = controller.board;
    final meId = controller.memberId;
    final current = board.currentTaskOf(meId);
    final mine = board.tasksOf(meId);
    final queued = mine.where((t) => !t.done).length - (current == null ? 0 : 1);
    final done = mine.where((t) => t.done).toList();
    final quiz = board.myQuiz;
    return ListView(
      key: const ValueKey('room-for-you'),
      padding: EdgeInsets.zero,
      children: [
        if (current != null)
          Container(
            key: const ValueKey('for-you-task'),
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: Paper.accentSoft,
              border: Border.all(color: Paper.accentLine),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Icon(Icons.push_pin_rounded, size: 14, color: Paper.accent),
                const SizedBox(width: 6),
                Text('YOUR TASK · ${current.kind.toUpperCase()}',
                    style: mono(9.5, color: Paper.accentDark, weight: FontWeight.w700)),
              ]),
              const SizedBox(height: 5),
              RichMessageText(current.description, selectable: false, style: sans(13.5, height: 1.45)),
              if (quiz != null) ...[
                const SizedBox(height: 8),
                Text('Tap your answer', style: sans(11, color: Paper.muted)),
                const SizedBox(height: 5),
                _Choices(set: quiz, controller: controller),
              ],
              if (queued > 0) ...[
                const SizedBox(height: 4),
                Text('$queued more after this', style: sans(11, color: Paper.muted)),
              ],
            ]),
          )
        else
          Text(
            mine.isEmpty ? 'Versa will give you a task in a moment.' : 'You\'ve done all your tasks. Nice!',
            style: sans(12.5, color: Paper.muted),
          ),
        if (done.isNotEmpty) ...[
          const SizedBox(height: 6),
          for (final t in done.reversed.take(2))
            Padding(
              padding: const EdgeInsets.only(bottom: 2),
              child: Row(children: [
                Icon(Icons.check_circle_rounded, size: 13, color: Paper.olive),
                const SizedBox(width: 5),
                Expanded(
                  child: Text(t.description,
                      maxLines: 1, overflow: TextOverflow.ellipsis, style: sans(11.5, color: Paper.muted)),
                ),
              ]),
            ),
        ],
        for (final set in board.otherOptions) ...[
          const SizedBox(height: 12),
          _OptionSetCard(set: set, controller: controller),
        ],
      ],
    );
  }
}

class _OptionSetCard extends StatelessWidget {
  const _OptionSetCard({required this.set, required this.controller});
  final RoomOptionSet set;
  final RoomController controller;

  @override
  Widget build(BuildContext context) {
    final waiting = controller.pickedSetIds.contains(set.setId);
    final enabled = controller.isLive && !waiting;
    return Column(
      key: ValueKey('option-set-${set.setId}'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(children: [
          Expanded(child: RichMessageText(set.prompt, selectable: false, style: sans(13, weight: FontWeight.w600, height: 1.4))),
          if (set.race)
            const RaceChip()
          else if (set.forEveryone)
            Container(
              margin: const EdgeInsets.only(left: 6),
              padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
              decoration: BoxDecoration(color: Paper.sliver, borderRadius: BorderRadius.circular(100)),
              child: Text('EVERYONE', style: mono(8.5, weight: FontWeight.w700)),
            ),
        ]),
        const SizedBox(height: 6),
        _Choices(set: set, controller: controller, enabled: enabled),
      ],
    );
  }
}

/// A set's choices as tappable pills (a quiz task's, or any other options).
class _Choices extends StatelessWidget {
  const _Choices({required this.set, required this.controller, this.enabled});
  final RoomOptionSet set;
  final RoomController controller;
  final bool? enabled;

  @override
  Widget build(BuildContext context) {
    final on = enabled ?? (controller.isLive && !controller.pickedSetIds.contains(set.setId));
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        for (final o in set.options)
          OutlinedButton(
            key: ValueKey('room-option-${o.id}'),
            onPressed: on ? () => controller.pick(set, o) : null,
            style: OutlinedButton.styleFrom(
              foregroundColor: Paper.accentDark,
              side: BorderSide(color: Paper.accent),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              visualDensity: VisualDensity.compact,
              textStyle: sans(12.5, weight: FontWeight.w600),
            ),
            child: RichMessageText(o.text, selectable: false, style: sans(12.5, weight: FontWeight.w600)),
          ),
      ],
    );
  }
}
