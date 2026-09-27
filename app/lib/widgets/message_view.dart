import 'package:flutter/material.dart';

import '../models.dart';
import '../theme.dart';
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
    this.directionsStyle = 'fork',
  });

  /// 'fork': the directions are links the answer ends with; 'strip': cards.
  final String directionsStyle;

  /// Takes a "where this could go" card; null hides the strip.
  final void Function(DirectionCard card)? onPickDirection;

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
          child: SelectableText(message.text, style: sans(14.5, height: 1.5)),
        ),
      ),
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
            decoration: const BoxDecoration(color: Paper.accent, shape: BoxShape.circle),
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
                            color: const Color(0xFFFBECE8),
                            border: Border.all(color: const Color(0xFFEFC9C0)),
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: SelectableText(message.text,
                              style: sans(14, color: Paper.danger, height: 1.5)),
                        )
                      : SelectableText(message.text, style: sans(14.5, height: 1.65)),
                if (message.rewriting) ...[
                  const SizedBox(height: 6),
                  Row(
                    key: const ValueKey('rewriting'),
                    children: [
                      const SizedBox(
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
                  if (directionsStyle == 'fork') ...[
                    const SizedBox(height: 10),
                    _DirectionsFork(cards: message.directions, enabled: canPickOption, onPick: onPickDirection!),
                  ] else ...[
                    const SizedBox(height: 14),
                    _DirectionsStrip(cards: message.directions, enabled: canPickOption, onPick: onPickDirection!),
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

  static String _seconds(int ms) => '${(ms / 1000).toStringAsFixed(1)} s';

  static String _timingLabel(ChatMessage m) {
    final t = m.timing!;
    final seen = m.hasOptions ? 'options shown' : 'first words';
    final done = m.hasOptions ? '' : ' · complete ${_seconds(t.totalMs)}';
    return '$seen ${_seconds(t.firstOutputMs)}$done';
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
          const Icon(Icons.psychology_alt_outlined, size: 14, color: Paper.accentDark),
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
                avatar: const Icon(Icons.north_east_rounded, size: 14, color: Paper.accent),
                onPressed: enabled ? () => onPick(c) : null,
                backgroundColor: Paper.card,
                side: const BorderSide(color: Paper.border),
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
