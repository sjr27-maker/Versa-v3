import 'package:flutter/material.dart';

import '../models.dart';
import '../picture.dart';
import '../theme.dart';
import 'directions_compass.dart';
import 'rich_text.dart';
import 'typing_dots.dart';

/// One message in the conversation: the person's bubble, or the tutor's reply
/// (with its clickable readings and timing caption).
class MessageView extends StatelessWidget {
  const MessageView({
    super.key,
    required this.message,
    required this.showTiming,
    required this.canPickOption,
    required this.onPickOption,
    this.onUndoClaimUpdate,
    this.onViewClaimUpdate,
    this.onPickDirection,
    this.onMoreDirections,
    this.compassOnStage = false,
    this.directionsStyle = 'fork',
  });

  /// 'fork': the directions are links the answer ends with; 'strip': cards.
  final String directionsStyle;

  /// Takes a "where this could go" card; null hides the strip.
  final void Function(DirectionCard card)? onPickDirection;

  /// "Other directions" under the directions; null hides it.
  final VoidCallback? onMoreDirections;

  /// The stage is open and shows the compass itself: not repeated here.
  final bool compassOnStage;

  final ChatMessage message;
  final bool showTiming;
  final bool canPickOption;
  final void Function(ChatOption option) onPickOption;

  /// The sandbox-chat claim-update flow's inline note actions -- null (no
  /// note shown) unless `message.claimUpdate` is set.
  final void Function(ClaimUpdate update)? onUndoClaimUpdate;
  final void Function(ClaimUpdate update)? onViewClaimUpdate;

  @override
  Widget build(BuildContext context) {
    return message.role == Role.user ? _userBubble() : _tutorReply();
  }

  Widget _userBubble() {
    return Align(
      alignment: Alignment.centerRight,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 560),
        child: Container(
          key: ValueKey('msg-${message.id}'),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: Paper.accentSoft,
            border: Border.all(color: Paper.accentLine),
            borderRadius: const BorderRadius.only(
              topLeft: Radius.circular(14),
              topRight: Radius.circular(14),
              bottomLeft: Radius.circular(14),
              bottomRight: Radius.circular(4),
            ),
          ),
          child: _userContent(),
        ),
      ),
    );
  }

  /// The words, and the picture they sent: the picture itself when it was
  /// picked on this device, else (a chat from history) what it showed.
  Widget _userContent() {
    final (words, reading) = splitPicture(message.text);
    final picture = message.picture;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.end,
      mainAxisSize: MainAxisSize.min,
      children: [
        if (picture != null)
          ClipRRect(
            key: ValueKey('msg-picture-${message.id}'),
            borderRadius: BorderRadius.circular(10),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 260, maxHeight: 220),
              child: Image.memory(picture, fit: BoxFit.contain, gaplessPlayback: true),
            ),
          )
        else if (reading != null)
          PictureNote(reading: reading),
        if (picture != null || reading != null) if (words.trim().isNotEmpty) const SizedBox(height: 8),
        if (words.trim().isNotEmpty) RichMessageText(words.trim(), style: sans(14.5, height: 1.5)),
      ],
    );
  }

  Widget _tutorReply() {
    final failed = message.isError;
    final continuation = message.continuationOf;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // A continuation (a fork link taken) reads as the same explanation
        // carrying on: no avatar of its own, same indent.
        if (continuation != null)
          const SizedBox(width: 30)
        else
          Container(
            width: 30,
            height: 30,
            alignment: Alignment.center,
            decoration: BoxDecoration(color: Paper.accent, shape: BoxShape.circle),
            child: Text('V', style: sans(13, color: Colors.white, weight: FontWeight.w700)),
          ),
        const SizedBox(width: 12),
        Expanded(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 720),
            child: Column(
              key: ValueKey('msg-${message.id}'),
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (continuation != null) ...[
                  Text('→ $continuation',
                      key: ValueKey('continuation-${message.id}'),
                      style: sans(12.5, color: Paper.accentDark, weight: FontWeight.w600)),
                  const SizedBox(height: 6),
                ],
                if (message.guess case final g?) ...[
                  _WhyNote(
                    key: const ValueKey('guess-note'),
                    headline: '${g.headline} · ${g.record}',
                    icon: g.hit ? Icons.check_circle_outline : Icons.auto_awesome_outlined,
                    because: g.because,
                    strong: g.hit,
                  ),
                  const SizedBox(height: 8),
                ],
                if (message.adapted case final a?) ...[
                  _WhyNote(
                    key: const ValueKey('adapted-note'),
                    headline: a.headline,
                    icon: Icons.route_outlined,
                    because: a.because,
                  ),
                  const SizedBox(height: 8),
                ],
                if (message.recalled) ...[
                  Text('Oh wait, I remember what you meant.',
                      key: const ValueKey('recalled'),
                      style: sans(12.5, color: Paper.muted).copyWith(fontStyle: FontStyle.italic)),
                  const SizedBox(height: 6),
                ],
                if (message.pending)
                  const Padding(padding: EdgeInsets.only(top: 4), child: TypingDots())
                else if (message.text.isNotEmpty)
                  failed
                      ? Container(
                          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                          decoration: BoxDecoration(
                            color: Paper.dangerSoft,
                            border: Border.all(color: Paper.danger.withValues(alpha: 0.3)),
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: SelectableText(message.text,
                              style: sans(14, color: Paper.danger, height: 1.5)),
                        )
                      : RichMessageText(message.text, style: sans(14.5, height: 1.65)),
                if (message.rewriting) ...[
                  const SizedBox(height: 6),
                  Row(
                    key: const ValueKey('rewriting'),
                    children: [
                      SizedBox(
                        width: 10,
                        height: 10,
                        child: CircularProgressIndicator(strokeWidth: 1.5, color: Paper.faint),
                      ),
                      const SizedBox(width: 6),
                      Text('rewriting…', style: mono(11)),
                    ],
                  ),
                ],
                if (message.hasOptions) ...[
                  const SizedBox(height: 12),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      for (final o in message.options)
                        _OptionChip(
                          option: o,
                          chosen: message.chosenOptionId == o.id,
                          dimmed: !message.optionsOpen && message.chosenOptionId != o.id,
                          enabled: canPickOption && message.optionsOpen && !message.optionsResolved,
                          onTap: () => onPickOption(o),
                        ),
                    ],
                  ),
                ],
                if (message.directions.isNotEmpty && onPickDirection != null) ...[
                  if (directionsStyle == 'compass') ...[
                    if (!compassOnStage) ...[
                      const SizedBox(height: 14),
                      DirectionsCompass(
                          cards: message.directions, enabled: canPickOption, onPick: onPickDirection!),
                    ],
                    _moreLink(),
                  ] else if (directionsStyle == 'fork') ...[
                    const SizedBox(height: 10),
                    _DirectionsFork(cards: message.directions, enabled: canPickOption, onPick: onPickDirection!),
                    _moreLink(),
                  ] else ...[
                    const SizedBox(height: 14),
                    _DirectionsStrip(cards: message.directions, enabled: canPickOption, onPick: onPickDirection!),
                    _moreLink(),
                  ],
                ],
                if (showTiming && message.timing != null) ...[
                  const SizedBox(height: 8),
                  Text(_timingLabel(message), key: const ValueKey('timing'), style: mono(11)),
                ],
                if (message.claimUpdate != null) ...[
                  const SizedBox(height: 8),
                  _ClaimUpdateNote(
                    update: message.claimUpdate!,
                    onUndo: onUndoClaimUpdate,
                    onView: onViewClaimUpdate,
                  ),
                ],
              ],
            ),
          ),
        ),
      ],
    );
  }

  /// "↻ other directions": a quiet link under the hand. Hidden once the
  /// server has nothing more to deal; dimmed while the next hand is coming.
  Widget _moreLink() {
    if (onMoreDirections == null || message.directionsSetId == null || message.directionsExhausted) {
      return const SizedBox.shrink();
    }
    final waiting = message.moreDirectionsPending;
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: TextButton.icon(
        key: const ValueKey('more-directions'),
        onPressed: waiting || !canPickOption ? null : onMoreDirections,
        style: TextButton.styleFrom(
          minimumSize: Size.zero,
          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
        ),
        icon: Icon(Icons.refresh_rounded, size: 14, color: Paper.muted),
        label: Text(waiting ? 'dealing\u2026' : 'other directions', style: sans(12, color: Paper.muted)),
      ),
    );
  }

  static String _seconds(int ms) => '${(ms / 1000).toStringAsFixed(1)} s';

  static String _timingLabel(ChatMessage m) {
    final t = m.timing!;
    final seen = m.hasOptions ? 'options shown' : 'first words';
    final done = m.hasOptions ? '' : ' · complete ${_seconds(t.totalMs)}';
    return '$seen ${_seconds(t.firstOutputMs)}$done';
  }
}

/// A one-line note Versa adds to a reply about how it is learning the
/// learner -- whether it guessed the direction they took ("Versa guessed
/// you'd pick this · 7 of your last 10"), or that the answer was shaped to
/// their usual way in -- with what it rested on behind a tap.
class _WhyNote extends StatefulWidget {
  const _WhyNote({
    super.key,
    required this.headline,
    required this.icon,
    required this.because,
    this.strong = false,
  });
  final String headline;
  final IconData icon;
  final List<String> because;
  final bool strong;

  @override
  State<_WhyNote> createState() => _WhyNoteState();
}

class _WhyNoteState extends State<_WhyNote> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final color = widget.strong ? Paper.accentDark : Paper.muted;
    final canOpen = widget.because.isNotEmpty;
    return InkWell(
      borderRadius: BorderRadius.circular(8),
      onTap: canOpen ? () => setState(() => _open = !_open) : null,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: widget.strong ? Paper.accentSoft : Colors.transparent,
          border: Border.all(color: widget.strong ? Paper.accentLine : Paper.border),
          borderRadius: BorderRadius.circular(8),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(widget.icon, size: 14, color: color),
                const SizedBox(width: 6),
                Flexible(child: Text(widget.headline, style: sans(11.5, color: color))),
                if (canOpen) ...[
                  const SizedBox(width: 4),
                  Icon(_open ? Icons.expand_less : Icons.expand_more, size: 14, color: color),
                ],
              ],
            ),
            if (_open) ...[
              const SizedBox(height: 6),
              for (final line in widget.because)
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text('• $line', style: sans(11.5, color: Paper.muted, height: 1.4)),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

class _ClaimUpdateNote extends StatelessWidget {
  const _ClaimUpdateNote({required this.update, this.onUndo, this.onView});
  final ClaimUpdate update;
  final void Function(ClaimUpdate update)? onUndo;
  final void Function(ClaimUpdate update)? onView;

  @override
  Widget build(BuildContext context) {
    return Container(
      key: const ValueKey('claim-update-note'),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: Paper.accentSoft,
        border: Border.all(color: Paper.accentLine),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Row(
        children: [
          Icon(Icons.psychology_alt_outlined, size: 14, color: Paper.accentDark),
          const SizedBox(width: 6),
          Expanded(child: Text(update.noteText, style: sans(11.5, color: Paper.accentDark))),
          if (onView != null)
            TextButton(
              onPressed: () => onView!(update),
              style: TextButton.styleFrom(minimumSize: Size.zero, padding: const EdgeInsets.symmetric(horizontal: 6)),
              child: const Text('View', style: TextStyle(fontSize: 11.5)),
            ),
          if (onUndo != null && update.reviewId != null)
            TextButton(
              onPressed: () => onUndo!(update),
              style: TextButton.styleFrom(minimumSize: Size.zero, padding: const EdgeInsets.symmetric(horizontal: 6)),
              child: const Text('Undo', style: TextStyle(fontSize: 11.5)),
            ),
        ],
      ),
    );
  }
}

class _OptionChip extends StatefulWidget {
  const _OptionChip({
    required this.option,
    required this.chosen,
    required this.dimmed,
    required this.enabled,
    required this.onTap,
  });

  final ChatOption option;
  final bool chosen;
  final bool dimmed;
  final bool enabled;
  final VoidCallback onTap;

  @override
  State<_OptionChip> createState() => _OptionChipState();
}

class _OptionChipState extends State<_OptionChip> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final active = widget.enabled && _hover;
    final Color fill = widget.chosen
        ? Paper.accent
        : active
            ? Paper.accentSoft
            : Paper.card;
    final Color line = widget.chosen || active ? Paper.accent : Paper.borderStrong;
    final Color ink = widget.chosen ? Colors.white : (widget.dimmed ? Paper.faint : Paper.ink);
    return MouseRegion(
      cursor: widget.enabled ? SystemMouseCursors.click : SystemMouseCursors.basic,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        key: ValueKey('option-${widget.option.id}'),
        onTap: widget.enabled ? widget.onTap : null,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 120),
          constraints: const BoxConstraints(maxWidth: 520),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(
            color: fill,
            border: Border.all(color: line),
            borderRadius: BorderRadius.circular(12),
          ),
          child: Text(widget.option.text, style: sans(13.5, color: ink, height: 1.4)),
        ),
      ),
    );
  }
}

/// "Where this could go": the directions the learner could take next, one
/// tap each, shown in the order the server shuffled them into. Quiet on
/// purpose -- easy to ignore, never in the way of the answer.
class _DirectionsStrip extends StatelessWidget {
  const _DirectionsStrip({required this.cards, required this.enabled, required this.onPick});
  final List<DirectionCard> cards;
  final bool enabled;
  final void Function(DirectionCard card) onPick;

  @override
  Widget build(BuildContext context) {
    return Column(
      key: const ValueKey('directions'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('WHERE THIS COULD GO', style: mono(9.5)),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final c in cards)
              ActionChip(
                key: ValueKey('direction-${c.id}'),
                label: Text(c.text, style: sans(13, color: Paper.ink)),
                avatar: Icon(Icons.north_east_rounded, size: 14, color: Paper.accent),
                onPressed: enabled ? () => onPick(c) : null,
                backgroundColor: Paper.card,
                side: BorderSide(color: Paper.border),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
              ),
          ],
        ),
      ],
    );
  }
}

/// The fork: the answer ends with "Continue with ->" and the directions as
/// inline links; taking one carries the same explanation on.
class _DirectionsFork extends StatelessWidget {
  const _DirectionsFork({required this.cards, required this.enabled, required this.onPick});
  final List<DirectionCard> cards;
  final bool enabled;
  final void Function(DirectionCard card) onPick;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      key: const ValueKey('directions-fork'),
      crossAxisAlignment: WrapCrossAlignment.center,
      runSpacing: 4,
      children: [
        Padding(
          padding: const EdgeInsets.only(right: 6),
          child: Text('Continue with \u2192', style: sans(13.5, color: Paper.muted, weight: FontWeight.w600)),
        ),
        for (final (i, c) in cards.indexed) ...[
          if (i > 0) Text('  \u00b7  ', style: sans(13.5, color: Paper.faint)),
          MouseRegion(
            cursor: enabled ? SystemMouseCursors.click : SystemMouseCursors.basic,
            child: GestureDetector(
              key: ValueKey('direction-${c.id}'),
              onTap: enabled ? () => onPick(c) : null,
              child: Text(
                c.text,
                style: sans(13.5, color: enabled ? Paper.accentDark : Paper.faint, weight: FontWeight.w500).copyWith(
                  decoration: TextDecoration.underline,
                  decorationColor: Paper.accentLine,
                  decorationThickness: 2,
                ),
              ),
            ),
          ),
        ],
      ],
    );
  }
}

/// A picture known only by what it showed (a chat from history, or a room
/// message): a small chip that opens to the reading.
class PictureNote extends StatefulWidget {
  const PictureNote({super.key, required this.reading});
  final String reading;

  @override
  State<PictureNote> createState() => _PictureNoteState();
}

class _PictureNoteState extends State<PictureNote> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        InkWell(
          key: const ValueKey('picture-note'),
          borderRadius: BorderRadius.circular(100),
          onTap: () => setState(() => _open = !_open),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
            decoration: BoxDecoration(
              color: Paper.card,
              border: Border.all(color: Paper.border),
              borderRadius: BorderRadius.circular(100),
            ),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(Icons.image_outlined, size: 15, color: Paper.muted),
              const SizedBox(width: 6),
              Text(_open ? 'Picture · hide' : 'Picture · what it showed', style: sans(12, color: Paper.muted)),
            ]),
          ),
        ),
        if (_open) ...[
          const SizedBox(height: 6),
          RichMessageText(widget.reading, style: sans(12.5, color: Paper.body, height: 1.5)),
        ],
      ],
    );
  }
}

