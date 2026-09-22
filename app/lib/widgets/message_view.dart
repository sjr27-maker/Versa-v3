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
  });

  final ChatMessage message;
  final bool showTiming;
  final bool canPickOption;
  final void Function(ChatOption option) onPickOption;

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
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
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
                if (showTiming && message.timing != null) ...[
                  const SizedBox(height: 8),
                  Text(_timingLabel(message), key: const ValueKey('timing'), style: mono(11)),
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
