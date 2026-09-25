import 'dart:async';

import 'package:flutter/material.dart';

import '../stage/engine.dart';
import '../stage/script.dart';
import '../stage/stage_view.dart';
import '../theme.dart';
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

  @override
  void initState() {
    super.initState();
    _sub = widget.controller.stageEvents.listen(_onEvent);
    widget.controller.addListener(_onRoom);
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
      child: StageView(engine: _engine),
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
              const Padding(
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
                const Icon(Icons.push_pin_rounded, size: 14, color: Paper.accent),
                const SizedBox(width: 6),
                Text('YOUR TASK · ${current.kind.toUpperCase()}',
                    style: mono(9.5, color: Paper.accentDark, weight: FontWeight.w700)),
              ]),
              const SizedBox(height: 5),
              Text(current.description, style: sans(13.5, height: 1.45)),
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
                const Icon(Icons.check_circle_rounded, size: 13, color: Paper.olive),
                const SizedBox(width: 5),
                Expanded(
                  child: Text(t.description,
                      maxLines: 1, overflow: TextOverflow.ellipsis, style: sans(11.5, color: Paper.muted)),
                ),
              ]),
            ),
        ],
        for (final set in board.options) ...[
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
          Expanded(child: Text(set.prompt, style: sans(13, weight: FontWeight.w600, height: 1.4))),
          if (set.forEveryone)
            Container(
              margin: const EdgeInsets.only(left: 6),
              padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
              decoration: BoxDecoration(color: Paper.sliver, borderRadius: BorderRadius.circular(100)),
              child: Text('EVERYONE', style: mono(8.5, weight: FontWeight.w700)),
            ),
        ]),
        const SizedBox(height: 6),
        Wrap(
          spacing: 6,
          runSpacing: 6,
          children: [
            for (final o in set.options)
              OutlinedButton(
                key: ValueKey('room-option-${o.id}'),
                onPressed: enabled ? () => controller.pick(set, o) : null,
                style: OutlinedButton.styleFrom(
                  foregroundColor: Paper.accentDark,
                  side: const BorderSide(color: Paper.accent),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  visualDensity: VisualDensity.compact,
                  textStyle: sans(12.5, weight: FontWeight.w600),
                ),
                child: Text(o.text),
              ),
          ],
        ),
      ],
    );
  }
}
