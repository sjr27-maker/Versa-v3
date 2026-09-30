import 'package:flutter/material.dart';

import '../picture.dart';
import '../theme.dart';
import '../widgets/message_view.dart' show PictureNote;
import '../widgets/rich_text.dart';
import 'room_controller.dart';
import 'room_models.dart';

/// Each person keeps one colour for their name and avatar, like a group chat.
const _nameColors = [
  Color(0xFF2E7D6B), Color(0xFF6A4FB3), Color(0xFFB0476B), Color(0xFF2F6DB5),
  Color(0xFF9A6B1F), Color(0xFF3F8A3A), Color(0xFFB4532A), Color(0xFF55708A),
];

Color colorForName(String name) {
  if (name == 'Versa') return Paper.accent;
  var h = 0;
  for (final c in name.toLowerCase().codeUnits) {
    h = (h * 31 + c) & 0x7fffffff;
  }
  return _nameColors[h % _nameColors.length];
}

String clockTime(DateTime t) =>
    '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';

String dayLabel(DateTime t, {DateTime? now}) {
  final today = DateUtils.dateOnly(now ?? DateTime.now());
  final day = DateUtils.dateOnly(t);
  final diff = today.difference(day).inDays;
  if (diff == 0) return 'Today';
  if (diff == 1) return 'Yesterday';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return '${t.day} ${months[t.month - 1]} ${t.year}';
}

/// A round initial, the person's colour (Versa's is the accent).
class RoomAvatar extends StatelessWidget {
  const RoomAvatar({super.key, required this.name, this.size = 32, this.online});
  final String name;
  final double size;
  final bool? online;

  @override
  Widget build(BuildContext context) {
    final color = colorForName(name);
    final initial = name.isEmpty ? '?' : name.characters.first.toUpperCase();
    return SizedBox(
      width: size,
      height: size,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Container(
            width: size,
            height: size,
            alignment: Alignment.center,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
            child: Text(initial,
                style: TextStyle(color: Colors.white, fontSize: size * 0.44, fontWeight: FontWeight.w700)),
          ),
          if (online == true)
            Positioned(
              right: -1,
              bottom: -1,
              child: Container(
                width: size * 0.32,
                height: size * 0.32,
                decoration: BoxDecoration(
                  color: const Color(0xFF3DB45A),
                  shape: BoxShape.circle,
                  border: Border.all(color: Paper.surface, width: 2),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// The message list: newest at the bottom, grouped like a chat app (a name
/// shows once per run of messages from the same person, day separators).
class RoomMessageList extends StatelessWidget {
  const RoomMessageList({super.key, required this.messages, required this.meId, this.footer, this.controller});

  final List<RoomMessage> messages;
  final String meId;

  /// Lets this person tap their quiz task's answer right in the chat.
  final RoomController? controller;

  /// Shown under the newest message (e.g. "Versa is typing…").
  final Widget? footer;

  @override
  Widget build(BuildContext context) {
    final items = <Widget>[];
    for (var i = 0; i < messages.length; i++) {
      final m = messages[i];
      final prev = i > 0 ? messages[i - 1] : null;
      final newDay = prev == null || !DateUtils.isSameDay(prev.createdAt, m.createdAt);
      if (newDay) items.add(_DayChip(label: dayLabel(m.createdAt)));
      final sameRun = !newDay &&
          !prev.isSystem &&
          prev.sender == m.sender &&
          prev.memberId == m.memberId &&
          m.createdAt.difference(prev.createdAt).inMinutes < 5 &&
          !const {'task', 'progress'}.contains(m.kind) &&
          !const {'task', 'progress'}.contains(prev.kind);
      items.add(RoomMessageTile(message: m, meId: meId, showName: !sameRun, controller: controller));
    }
    if (footer != null) items.add(footer!);
    // Reversed so the list sits at the bottom and new messages stay in view.
    final reversed = items.reversed.toList();
    return ListView.builder(
      key: const ValueKey('room-messages'),
      reverse: true,
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      itemCount: reversed.length,
      itemBuilder: (context, i) => reversed[i],
    );
  }
}

class _DayChip extends StatelessWidget {
  const _DayChip({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) => Center(
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 10),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          decoration: BoxDecoration(
            color: Paper.card,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: Paper.border),
          ),
          child: Text(label, style: sans(11.5, color: Paper.muted, weight: FontWeight.w500)),
        ),
      );
}

/// One message, drawn by what it is.
class RoomMessageTile extends StatelessWidget {
  const RoomMessageTile({super.key, required this.message, required this.meId, this.showName = true, this.controller});

  final RoomMessage message;
  final String meId;
  final bool showName;
  final RoomController? controller;

  @override
  Widget build(BuildContext context) {
    final m = message;
    final key = ValueKey('room-msg-${m.seq}');
    if (m.isSystem) return KeyedSubtree(key: key, child: _SystemLine(text: m.text));
    if (m.kind == 'progress' && m.meta.containsKey('race_set_id')) {
      final winner = m.meta['winner'] as String?;
      final me = m.meta['winner_id'] == meId;
      return KeyedSubtree(
        key: key,
        child: _SystemLine(
          text: winner == null
              ? 'Nobody got that race -- it was "${m.meta['right_answer'] ?? ''}"'
              : '${me ? 'You' : winner} won the race! (+3) -- "${m.meta['right_answer'] ?? ''}"',
          icon: Icons.emoji_events_rounded,
          color: winner == null ? Paper.muted : Paper.accentDark,
        ),
      );
    }
    if (m.kind == 'question' && m.meta['race'] == true) {
      return KeyedSubtree(key: key, child: _RaceCard(message: m, meId: meId, controller: controller));
    }
    if (m.kind == 'progress' && m.meta['part'] != null) {
      return KeyedSubtree(
        key: key,
        child: _SystemLine(
          text: 'Part ${m.meta['part']} covered: ${m.text}',
          icon: Icons.flag_rounded,
          color: Paper.olive,
        ),
      );
    }
    if (m.kind == 'progress') {
      return KeyedSubtree(
        key: key,
        child: _SystemLine(
          text: '${m.toMemberId == meId ? 'You' : m.toName ?? 'Someone'} finished a task: ${m.text}',
          icon: Icons.check_circle_rounded,
          color: Paper.olive,
        ),
      );
    }
    if (m.kind == 'task') {
      return KeyedSubtree(key: key, child: _TaskCard(message: m, meId: meId, controller: controller));
    }
    final mine = m.isMine(meId);
    return Padding(
      key: key,
      padding: EdgeInsets.only(top: showName ? 8 : 2),
      child: Row(
        mainAxisAlignment: mine ? MainAxisAlignment.end : MainAxisAlignment.start,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (!mine)
            SizedBox(
              width: 38,
              child: showName ? RoomAvatar(name: m.senderName, size: 30) : null,
            ),
          Flexible(child: _Bubble(message: m, mine: mine, meId: meId, showName: showName && !mine)),
          if (mine) const SizedBox(width: 4),
        ],
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.message, required this.mine, required this.meId, required this.showName});

  final RoomMessage message;
  final bool mine;
  final String meId;
  final bool showName;

  @override
  Widget build(BuildContext context) {
    final m = message;
    final content = m.kind == 'content';
    final question = m.kind == 'question';
    final bg = mine
        ? Paper.accentSoft
        : (m.fromVersa && (content || question) ? Paper.surface : Paper.card);
    final border = mine ? Paper.accentLine : (m.fromVersa ? Paper.accentLine : Paper.border);
    final radius = BorderRadius.only(
      topLeft: Radius.circular(!mine && showName ? 4 : 14),
      topRight: Radius.circular(mine ? 4 : 14),
      bottomLeft: const Radius.circular(14),
      bottomRight: const Radius.circular(14),
    );

    final header = <Widget>[];
    if (showName) {
      header.add(Text(m.senderName,
          style: sans(12.5, color: colorForName(m.senderName), weight: FontWeight.w700)));
    }
    final to = _addressLine(m);
    if (to != null) header.add(to);
    if (content) header.add(_Label(icon: Icons.menu_book_rounded, text: 'Explanation'));
    if (question) header.add(_Label(icon: Icons.help_outline_rounded, text: 'Question'));
    if (m.kind == 'pick') {
      header.add(Text('↳ answered "${m.meta['prompt'] ?? ''}"',
          style: sans(11.5, color: Paper.muted), maxLines: 2, overflow: TextOverflow.ellipsis));
      if (m.meta['race'] == true) {
        final right = m.meta['correct'] == true;
        header.add(_Label(
          icon: right ? Icons.emoji_events_rounded : Icons.close_rounded,
          text: right ? 'First right answer!' : 'Not that one',
        ));
      }
      if (m.meta['quiz'] == true) {
        final right = m.meta['correct'] == true;
        header.add(_Label(
          icon: right ? Icons.check_circle_rounded : Icons.lightbulb_outline_rounded,
          text: right ? 'Right!' : 'Not quite -- it was "${m.meta['right_answer'] ?? ''}"',
        ));
      }
    }

    return ConstrainedBox(
      constraints: BoxConstraints(maxWidth: content ? 640 : 520),
      child: Container(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 6),
        decoration: BoxDecoration(color: bg, border: Border.all(color: border), borderRadius: radius),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            for (final h in header) Padding(padding: const EdgeInsets.only(bottom: 3), child: h),
            ...() {
              // a picture travels in a room as what it showed (picture.dart)
              final (words, reading) = splitPicture(m.text);
              return [
                if (reading != null) ...[
                  PictureNote(reading: reading),
                  if (words.trim().isNotEmpty) const SizedBox(height: 6),
                ],
                if (words.trim().isNotEmpty)
                  RichMessageText(
                    words.trim(),
                    style: sans(content ? 14.5 : 14,
                        height: 1.45, weight: m.kind == 'pick' ? FontWeight.w600 : FontWeight.w400),
                  ),
              ];
            }(),
            if (question && m.optionTexts.isNotEmpty) ...[
              const SizedBox(height: 6),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  for (final o in m.optionTexts)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                      decoration: BoxDecoration(
                        color: Paper.sliver,
                        border: Border.all(color: Paper.border),
                        borderRadius: BorderRadius.circular(100),
                      ),
                      child: Text(o, style: sans(12, color: Paper.body)),
                    ),
                ],
              ),
              const SizedBox(height: 4),
              Text(
                m.toMemberId == null || m.toMemberId == meId ? 'Answer in "For you" above' : 'For ${m.toName}',
                style: sans(11, color: Paper.faint),
              ),
            ],
            Align(
              alignment: Alignment.bottomRight,
              child: Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(clockTime(m.createdAt), style: sans(10.5, color: Paper.faint)),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// "→ Ben", or "Only you can see this" for a private message.
  Widget? _addressLine(RoomMessage m) {
    if (m.private && m.fromVersa) {
      return Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(Icons.lock_rounded, size: 12, color: Paper.warn),
        const SizedBox(width: 4),
        Text(m.toMemberId == meId ? 'Only you can see this' : 'Private to ${m.toName}',
            style: sans(11.5, color: Paper.warn, weight: FontWeight.w600)),
      ]);
    }
    if (m.fromVersa && m.toMemberId != null) {
      final you = m.toMemberId == meId;
      return Text('→ ${you ? 'you' : m.toName}',
          style: sans(11.5, color: you ? Paper.accent : Paper.muted, weight: FontWeight.w600));
    }
    return null;
  }
}

class _Label extends StatelessWidget {
  const _Label({required this.icon, required this.text});
  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) => Row(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, size: 13, color: Paper.accent),
        const SizedBox(width: 4),
        Text(text.toUpperCase(), style: mono(9.5, color: Paper.accent, weight: FontWeight.w700)),
      ]);
}

class _SystemLine extends StatelessWidget {
  const _SystemLine({required this.text, this.icon, this.color});
  final String text;
  final IconData? icon;
  final Color? color;

  @override
  Widget build(BuildContext context) => Center(
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 6),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
          constraints: const BoxConstraints(maxWidth: 560),
          decoration: BoxDecoration(
            color: color == null ? Paper.sliver : Paper.oliveSoft,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(color: color == null ? Paper.border : Paper.oliveLine),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            if (icon != null) ...[Icon(icon, size: 14, color: color), const SizedBox(width: 6)],
            Flexible(
              child: Text(text,
                  textAlign: TextAlign.center, style: sans(12, color: color ?? Paper.muted, weight: FontWeight.w500)),
            ),
          ]),
        ),
      );
}

class _TaskCard extends StatelessWidget {
  const _TaskCard({required this.message, required this.meId, this.controller});
  final RoomMessage message;
  final String meId;
  final RoomController? controller;

  @override
  Widget build(BuildContext context) {
    final m = message;
    final you = m.toMemberId == meId;
    final who = you ? 'you' : (m.toName ?? 'someone');
    return Center(
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 8),
        constraints: const BoxConstraints(maxWidth: 520),
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
        decoration: BoxDecoration(
          color: you ? Paper.accentSoft : Paper.card,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: you ? Paper.accent : Paper.border, width: you ? 1.4 : 1),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(children: [
              Icon(Icons.push_pin_rounded, size: 14, color: Paper.accent),
              const SizedBox(width: 6),
              Expanded(
                child: Text('New task for $who · ${m.meta['task_kind'] ?? 'learn'}',
                    style: sans(12, color: Paper.accentDark, weight: FontWeight.w700)),
              ),
              Text(clockTime(m.createdAt), style: sans(10.5, color: Paper.faint)),
            ]),
            const SizedBox(height: 5),
            RichMessageText(m.text, selectable: false, style: sans(13.5, height: 1.45)),
            if (m.optionTexts.isNotEmpty) ...[
              const SizedBox(height: 8),
              _TaskChoices(message: m, you: you, controller: controller),
            ],
          ],
        ),
      ),
    );
  }
}

/// "RACE · first right answer wins".
class RaceChip extends StatelessWidget {
  const RaceChip({super.key});

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.only(left: 6),
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
        decoration: BoxDecoration(color: Paper.accent, borderRadius: BorderRadius.circular(100)),
        child: Text('RACE', style: mono(8.5, weight: FontWeight.w700, color: Colors.white)),
      );
}

/// A race: one question for everyone, first right tap wins. Tappable while
/// it is open for this person; once decided, who won and the answer.
class _RaceCard extends StatelessWidget {
  const _RaceCard({required this.message, required this.meId, this.controller});
  final RoomMessage message;
  final String meId;
  final RoomController? controller;

  @override
  Widget build(BuildContext context) {
    final m = message;
    final room = controller;
    final set = room?.board.openSet(m.meta['option_set_id']);
    final live = set != null && room!.isLive && !room.pickedSetIds.contains(set.setId);
    final result = room?.messages
        .where((r) => r.kind == 'progress' && r.meta['race_set_id'] == m.meta['option_set_id'])
        .firstOrNull;
    return Center(
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 8),
        constraints: const BoxConstraints(maxWidth: 520),
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 12),
        decoration: BoxDecoration(
          color: Paper.accentSoft,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Paper.accent, width: 1.6),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, mainAxisSize: MainAxisSize.min, children: [
          Row(children: [
            Icon(Icons.bolt_rounded, size: 16, color: Paper.accent),
            const SizedBox(width: 4),
            Expanded(
              child: Text('Race for everyone · first right answer wins',
                  style: sans(12, color: Paper.accentDark, weight: FontWeight.w700)),
            ),
            Text(clockTime(m.createdAt), style: sans(10.5, color: Paper.faint)),
          ]),
          const SizedBox(height: 6),
          RichMessageText(m.text, selectable: false, style: sans(14, height: 1.45, weight: FontWeight.w600)),
          const SizedBox(height: 8),
          Wrap(spacing: 6, runSpacing: 6, children: [
            if (set != null)
              for (final o in set.options)
                FilledButton.tonal(
                  key: ValueKey('race-choice-${o.id}'),
                  onPressed: live ? () => room.pick(set, o) : null,
                  style: FilledButton.styleFrom(
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
                    visualDensity: VisualDensity.compact,
                    textStyle: sans(12.5, weight: FontWeight.w600),
                  ),
                  child: RichMessageText(o.text, selectable: false, style: sans(12.5, weight: FontWeight.w600)),
                )
            else
              for (final o in m.optionTexts)
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
                  decoration: BoxDecoration(
                    color: o == result?.meta['right_answer'] ? Paper.oliveSoft : Paper.card,
                    borderRadius: BorderRadius.circular(100),
                    border: Border.all(color: o == result?.meta['right_answer'] ? Paper.olive : Paper.border),
                  ),
                  child: Text(o, style: sans(12, color: Paper.body)),
                ),
          ]),
          if (set == null && result == null) ...[
            const SizedBox(height: 6),
            Text('Your answer is in -- waiting for the others…', style: sans(11, color: Paper.faint)),
          ],
        ]),
      ),
    );
  }
}

/// A quiz task's choices. For the person it's for, while it is open, each
/// is a tap that answers it (the same as on the stage or in For you); for
/// everyone else, and once answered, they are just shown.
class _TaskChoices extends StatelessWidget {
  const _TaskChoices({required this.message, required this.you, this.controller});
  final RoomMessage message;
  final bool you;
  final RoomController? controller;

  @override
  Widget build(BuildContext context) {
    final room = controller;
    final set = you && room != null
        ? room.board.options.where((s) => s.setId == message.meta['option_set_id']).firstOrNull
        : null;
    final live = set != null && room!.isLive && !room.pickedSetIds.contains(set.setId);
    return Wrap(
      spacing: 6,
      runSpacing: 6,
      children: [
        if (set != null)
          for (final o in set.options)
            OutlinedButton(
              key: ValueKey('task-choice-${o.id}'),
              onPressed: live ? () => room.pick(set, o) : null,
              style: OutlinedButton.styleFrom(
                foregroundColor: Paper.accentDark,
                side: BorderSide(color: Paper.accent),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                visualDensity: VisualDensity.compact,
                textStyle: sans(12.5, weight: FontWeight.w600),
              ),
              child: RichMessageText(o.text, selectable: false, style: sans(12.5, weight: FontWeight.w600)),
            )
        else
          for (final o in message.optionTexts)
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 3),
              decoration: BoxDecoration(
                color: Paper.card,
                borderRadius: BorderRadius.circular(100),
                border: Border.all(color: Paper.border),
              ),
              child: Text(o, style: sans(12, color: Paper.muted)),
            ),
      ],
    );
  }
}

/// "Versa is typing…" / "Ben is typing…" as a small bubble under the chat.
class RoomTypingBubble extends StatelessWidget {
  const RoomTypingBubble({super.key, required this.names});
  final List<String> names;

  @override
  Widget build(BuildContext context) {
    final text = names.length == 1
        ? '${names.first} is typing…'
        : '${names.take(names.length - 1).join(', ')} and ${names.last} are typing…';
    return Padding(
      key: const ValueKey('room-typing'),
      padding: const EdgeInsets.only(top: 8, left: 38),
      child: Align(
        alignment: Alignment.centerLeft,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
          decoration: BoxDecoration(
            color: Paper.card,
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: Paper.border),
          ),
          child: Text(text, style: sans(12.5, color: Paper.muted).copyWith(fontStyle: FontStyle.italic)),
        ),
      ),
    );
  }
}
