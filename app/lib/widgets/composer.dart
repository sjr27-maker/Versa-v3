import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../composer_draft.dart';
import '../theme.dart';

/// The message box. Enter sends; Shift+Enter starts a new line.
class Composer extends StatefulWidget {
  const Composer({super.key, required this.enabled, required this.onSend, this.hint});

  /// Whether sending is allowed right now (typing always is).
  final bool enabled;
  final void Function(String text) onSend;
  final String? hint;

  @override
  State<Composer> createState() => _ComposerState();
}

class _ComposerState extends State<Composer> {
  final _controller = TextEditingController();
  final _focus = FocusNode();

  @override
  void initState() {
    super.initState();
    _takeDraft();
    _controller.addListener(() => setState(() {}));
    composerDraft.addListener(_takeDraft);
  }

  void _takeDraft() {
    final draft = composerDraft.value;
    if (draft == null) return;
    composerDraft.value = null;
    _controller.value = TextEditingValue(
      text: draft,
      selection: TextSelection.collapsed(offset: draft.length),
    );
    if (_focus.context != null) _focus.requestFocus(); // a fresh box autofocuses on its own
  }

  @override
  void dispose() {
    composerDraft.removeListener(_takeDraft);
    _controller.dispose();
    _focus.dispose();
    super.dispose();
  }

  bool get _canSend => widget.enabled && _controller.text.trim().isNotEmpty;

  void _submit() {
    if (!_canSend) return;
    final text = _controller.text;
    _controller.clear();
    widget.onSend(text);
    _focus.requestFocus();
  }

  KeyEventResult _onKey(FocusNode node, KeyEvent event) {
    if (event is KeyDownEvent &&
        event.logicalKey == LogicalKeyboardKey.enter &&
        !HardwareKeyboard.instance.isShiftPressed) {
      _submit();
      return KeyEventResult.handled;
    }
    return KeyEventResult.ignored;
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.ink, width: 1.5),
        borderRadius: BorderRadius.circular(14),
        boxShadow: const [BoxShadow(color: Color(0x0D000000), blurRadius: 16, offset: Offset(0, 4))],
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Expanded(
            child: Focus(
              onKeyEvent: _onKey,
              child: TextField(
                key: const ValueKey('composer-field'),
                controller: _controller,
                focusNode: _focus,
                autofocus: true,
                minLines: 1,
                maxLines: 6,
                style: sans(14.5),
                cursorColor: Paper.accent,
                decoration: InputDecoration(
                  hintText: widget.hint ?? 'Ask anything…',
                  hintStyle: sans(14.5, color: Paper.faint),
                  border: InputBorder.none,
                  isDense: true,
                  contentPadding: const EdgeInsets.symmetric(vertical: 10),
                ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          Material(
            color: _canSend ? Paper.accent : Paper.borderStrong,
            borderRadius: BorderRadius.circular(9),
            child: InkWell(
              key: const ValueKey('send-button'),
              borderRadius: BorderRadius.circular(9),
              onTap: _canSend ? _submit : null,
              child: const SizedBox(
                width: 38,
                height: 38,
                child: Icon(Icons.arrow_forward_rounded, color: Colors.white, size: 20),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
