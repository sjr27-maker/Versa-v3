import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../composer_draft.dart';
import '../picture.dart';
import '../theme.dart';

/// The message box. Enter sends; Shift+Enter starts a new line. With
/// [uploadPicture], a picture can go with the message (or on its own): it is
/// uploaded -- and read by the server -- the moment it is picked, so it is
/// usually ready by the time the words are.
class Composer extends StatefulWidget {
  const Composer({
    super.key,
    required this.enabled,
    required this.onSend,
    this.hint,
    this.onChanged,
    this.uploadPicture,
  });

  /// Whether sending is allowed right now (typing always is).
  final bool enabled;
  final void Function(String text, AttachedPicture? picture) onSend;
  final String? hint;

  /// Where a picked picture goes; null = no picture button.
  final PictureUploader? uploadPicture;

  /// Every edit to the text (a room uses it to say "typing…").
  final VoidCallback? onChanged;

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

  /// The picture being read (shown with a spinner), the one ready to go,
  /// and why the last one couldn't be read.
  Uint8List? _reading;
  AttachedPicture? _picture;
  String? _pictureProblem;
  int _pick = 0; // a newer pick (or a removal) wins over a slower upload

  bool get _canSend => widget.enabled && _reading == null && (_controller.text.trim().isNotEmpty || _picture != null);

  void _submit() {
    if (!_canSend) return;
    final text = _controller.text;
    final picture = _picture;
    _controller.clear();
    setState(() => _picture = null);
    widget.onSend(text, picture);
    _focus.requestFocus();
  }

  Future<void> _attach() async {
    final upload = widget.uploadPicture;
    if (upload == null) return;
    final picked = await choosePicture(context);
    if (picked == null || !mounted) return;
    final mine = ++_pick;
    setState(() {
      _reading = picked.bytes;
      _picture = null;
      _pictureProblem = null;
    });
    try {
      final picture = await upload(picked.bytes, picked.name);
      if (!mounted || mine != _pick) return;
      setState(() {
        _picture = picture;
        _reading = null;
      });
    } catch (e) {
      if (!mounted || mine != _pick) return;
      setState(() {
        _reading = null;
        _pictureProblem = '$e';
      });
    }
  }

  void _dropPicture() => setState(() {
    _pick++;
    _picture = null;
    _reading = null;
    _pictureProblem = null;
  });

  Widget _pictureRow() {
    final bytes = _picture?.bytes ?? _reading;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8, top: 2),
      child: Row(
        children: [
          if (bytes != null)
            Stack(
              key: const ValueKey('composer-picture'),
              children: [
                ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: Image.memory(bytes, width: 64, height: 64, fit: BoxFit.cover, gaplessPlayback: true),
                ),
                if (_reading != null)
                  Positioned.fill(
                    child: Container(
                      decoration: BoxDecoration(
                        color: Paper.card.withValues(alpha: 0.55),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      alignment: Alignment.center,
                      child: SizedBox(
                        width: 20,
                        height: 20,
                        child: CircularProgressIndicator(strokeWidth: 2, color: Paper.accent),
                      ),
                    ),
                  ),
              ],
            ),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              _pictureProblem ?? (_reading != null ? 'Reading your picture…' : 'Picture attached'),
              style: sans(12, color: _pictureProblem != null ? Paper.danger : Paper.muted),
            ),
          ),
          IconButton(
            key: const ValueKey('composer-picture-remove'),
            tooltip: 'Remove picture',
            visualDensity: VisualDensity.compact,
            onPressed: _dropPicture,
            icon: Icon(Icons.close_rounded, size: 18, color: Paper.faint),
          ),
        ],
      ),
    );
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
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (_picture != null || _reading != null || _pictureProblem != null) _pictureRow(),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Expanded(
                child: Focus(
                  onKeyEvent: _onKey,
                  child: TextField(
                    key: const ValueKey('composer-field'),
                    controller: _controller,
                    focusNode: _focus,
                    onChanged: widget.onChanged == null ? null : (_) => widget.onChanged!(),
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
              if (widget.uploadPicture != null)
                IconButton(
                  key: const ValueKey('composer-attach'),
                  tooltip: canTakePhoto() ? 'Take or add a photo' : 'Add a picture',
                  onPressed: _reading == null ? _attach : null,
                  icon: Icon(canTakePhoto() ? Icons.photo_camera_outlined : Icons.add_photo_alternate_outlined,
                      color: Paper.faint, size: 22),
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
        ],
      ),
    );
  }
}
