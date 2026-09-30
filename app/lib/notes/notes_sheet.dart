import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:share_plus/share_plus.dart';

import '../api.dart';
import '../billing/sparks.dart';
import '../theme.dart';
import '../widgets/rich_text.dart';
import 'notes_api.dart';

/// Opens the Notes sheet for one chat. Opening it makes nothing: notes are
/// written only when the learner taps "Generate notes" (src/versa/notes.py).
Future<void> showNotesSheet(BuildContext context, {required NotesApi api, required String sessionId}) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    useSafeArea: true,
    backgroundColor: Paper.sliver,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
    builder: (_) => DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.9,
      minChildSize: 0.4,
      maxChildSize: 0.95,
      builder: (context, scroll) => NotesPanel(api: api, sessionId: sessionId, scrollController: scroll),
    ),
  );
}

/// Writing the notes can take a little while; if the sheet is closed and
/// opened again meanwhile, it picks up the same request instead of starting
/// (and paying for) a second one.
final Map<String, Future<ChatNotes>> _writing = {};

enum _Phase { loading, loadFailed, ready, writing, writeFailed }

/// The Notes sheet's content: nothing yet / "Generate notes" / writing /
/// the notes, with "Save or share PDF" and, once more was studied, "Update".
class NotesPanel extends StatefulWidget {
  const NotesPanel({
    super.key,
    required this.api,
    required this.sessionId,
    this.scrollController,
    this.sharePdf,
    this.downloadPdf,
  });

  final NotesApi api;
  final String sessionId;
  final ScrollController? scrollController;

  /// How a finished PDF leaves the app; the share sheet by default (on the
  /// web it downloads). Tests pass their own.
  final Future<void> Function(ChatNotes notes, List<int> bytes, Rect? origin)? sharePdf;

  /// How a finished PDF is saved to the device; the system "save as" (on
  /// the web, a download) by default. True when it was saved. Tests pass
  /// their own.
  final Future<bool> Function(ChatNotes notes, List<int> bytes)? downloadPdf;

  @override
  State<NotesPanel> createState() => _NotesPanelState();
}

class _NotesPanelState extends State<NotesPanel> {
  _Phase _phase = _Phase.loading;
  NotesStatus? _status;
  String? _error;
  /// Which PDF action is running: 'download', 'share' or null.
  String? _pdfBusy;
  String? _pdfMessage;
  bool _pdfSaved = false;
  (String, List<int>)? _pdfCache; // notes id -> bytes, so the PDF is fetched once

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _phase = _Phase.loading;
      _error = null;
    });
    try {
      final status = await widget.api.status(widget.sessionId);
      if (!mounted) return;
      _status = status;
      final pending = _writing[widget.sessionId];
      if (pending != null) {
        _follow(pending);
      } else {
        setState(() => _phase = _Phase.ready);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = _Phase.loadFailed;
        _error = '$e';
      });
    }
  }

  void _generate() {
    if (_phase == _Phase.writing) return; // one request at a time
    final sessionId = widget.sessionId;
    final future = _writing[sessionId] ??= widget.api.generate(sessionId);
    future.whenComplete(() {
      _writing.remove(sessionId);
    }).ignore();
    _follow(future);
  }

  Future<void> _follow(Future<ChatNotes> future) async {
    setState(() {
      _phase = _Phase.writing;
      _error = null;
      _pdfMessage = null;
    });
    try {
      final notes = await future;
      if (!mounted) return;
      final answers = _status?.answers ?? notes.answers;
      setState(() {
        _status = NotesStatus(
          notes: notes,
          answers: answers,
          upToDate: notes.answers >= answers,
          cost: _status?.cost ?? 0,
        );
        _phase = _Phase.ready;
      });
    } on OutOfSparksException {
      // the Sparks sheet opens by itself (PaywallHub)
      if (!mounted) return;
      setState(() {
        _phase = _Phase.writeFailed;
        _error = "You're out of Sparks for now -- notes need ${_status?.cost ?? 2}.";
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _phase = _Phase.writeFailed;
        _error = e is ApiException ? e.message : "Couldn't write the notes -- check your connection and try again.";
      });
    }
  }

  Future<List<int>> _pdfBytes(ChatNotes notes) async {
    final cached = _pdfCache;
    if (cached != null && cached.$1 == notes.id) return cached.$2;
    final bytes = await widget.api.pdf(notes);
    _pdfCache = (notes.id, bytes);
    return bytes;
  }

  Future<void> _download(ChatNotes notes) async {
    if (_pdfBusy != null) return;
    setState(() {
      _pdfBusy = 'download';
      _pdfMessage = null;
    });
    try {
      final bytes = await _pdfBytes(notes);
      final saved = await (widget.downloadPdf ?? _saveToDevice)(notes, bytes);
      if (mounted && saved) {
        setState(() {
          _pdfSaved = true;
          _pdfMessage = 'Saved ${notes.fileName}';
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _pdfSaved = false;
          _pdfMessage = e is ApiException ? e.message : "Couldn't save the PDF -- try again.";
        });
      }
    } finally {
      if (mounted) setState(() => _pdfBusy = null);
    }
  }

  Future<void> _sharePdf(ChatNotes notes, BuildContext buttonContext) async {
    if (_pdfBusy != null) return;
    final box = buttonContext.findRenderObject() as RenderBox?;
    final origin = box == null ? null : box.localToGlobal(Offset.zero) & box.size;
    setState(() {
      _pdfBusy = 'share';
      _pdfMessage = null;
    });
    try {
      final bytes = await _pdfBytes(notes);
      await (widget.sharePdf ?? _share)(notes, bytes, origin);
    } catch (e) {
      if (mounted) {
        setState(() {
          _pdfSaved = false;
          _pdfMessage = e is ApiException ? e.message : "Couldn't share the PDF -- try again.";
        });
      }
    } finally {
      if (mounted) setState(() => _pdfBusy = null);
    }
  }

  /// The system "save as" on a phone (the learner picks where: Downloads,
  /// Drive...), a download in the browser.
  static Future<bool> _saveToDevice(ChatNotes notes, List<int> bytes) async {
    final uri = await FilePicker.saveFile(
      fileName: notes.fileName,
      bytes: Uint8List.fromList(bytes),
      mimeType: 'application/pdf',
      type: FileType.custom,
      allowedExtensions: const ['pdf'],
      dialogTitle: 'Save your notes',
    );
    return uri != null;
  }

  static Future<void> _share(ChatNotes notes, List<int> bytes, Rect? origin) async {
    final file = XFile.fromData(Uint8List.fromList(bytes), mimeType: 'application/pdf', name: notes.fileName);
    await SharePlus.instance.share(ShareParams(
      files: [file],
      fileNameOverrides: [notes.fileName],
      title: notes.title,
      subject: '${notes.title} -- Versa notes',
      sharePositionOrigin: origin,
    ));
  }

  @override
  Widget build(BuildContext context) {
    final notes = _status?.notes;
    return Column(
      children: [
        const SizedBox(height: 10),
        Container(
          width: 40,
          height: 4,
          decoration: BoxDecoration(color: Paper.borderStrong, borderRadius: BorderRadius.circular(2)),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 14, 8, 6),
          child: Row(children: [
            Icon(Icons.sticky_note_2_outlined, size: 18, color: Paper.accent),
            const SizedBox(width: 8),
            Text('REVISION NOTES', style: mono(10, color: Paper.accent)),
            const Spacer(),
            IconButton(
              key: const ValueKey('notes-close'),
              tooltip: 'Close',
              onPressed: () => Navigator.of(context).maybePop(),
              icon: Icon(Icons.close_rounded, size: 20, color: Paper.faint),
            ),
          ]),
        ),
        Expanded(
          child: AnimatedSwitcher(
            duration: const Duration(milliseconds: 200),
            child: KeyedSubtree(key: ValueKey('${_phase.name}-${notes?.id}'), child: _body(context)),
          ),
        ),
        if (notes != null && (_phase == _Phase.ready || _phase == _Phase.writeFailed)) _bottomBar(notes),
      ],
    );
  }

  Widget _body(BuildContext context) {
    switch (_phase) {
      case _Phase.loading:
        return Center(child: CircularProgressIndicator(strokeWidth: 2.5, color: Paper.accent));
      case _Phase.loadFailed:
        return _Message(
          icon: Icons.cloud_off_rounded,
          title: "Couldn't open the notes",
          body: _error ?? '',
          action: TextButton(key: const ValueKey('notes-retry-load'), onPressed: _load, child: const Text('Try again')),
        );
      case _Phase.writing:
        return _Writing(update: _status?.notes != null);
      case _Phase.ready:
      case _Phase.writeFailed:
        final status = _status!;
        if (status.notes == null) return _intro(status);
        return _NotesView(
          notes: status.notes!,
          scrollController: widget.scrollController,
          header: [
            if (_phase == _Phase.writeFailed) _ErrorLine(_error ?? ''),
            if (!status.upToDate) _UpdateBanner(cost: status.cost, onUpdate: _generate),
          ],
        );
    }
  }

  Widget _intro(NotesStatus status) {
    if (status.answers == 0) {
      return const _Message(
        icon: Icons.forum_outlined,
        title: 'Nothing to make notes from yet',
        body: 'Ask Versa something first. When you are done studying, come back here for revision notes of the chat.',
      );
    }
    return ListView(
      controller: widget.scrollController,
      padding: const EdgeInsets.fromLTRB(24, 12, 24, 24),
      children: [
        Text('Notes of this chat', style: serif(24)),
        const SizedBox(height: 10),
        Text(
          'Versa reads the whole chat so far and writes revision notes for you: the topics you studied, '
          'the key points of each, formulas and examples, and a short summary. You can save them as a PDF.',
          style: sans(14, color: Paper.body, height: 1.5),
        ),
        const SizedBox(height: 16),
        _Fact(icon: Icons.chat_bubble_outline_rounded, text: '${status.answers} answer${status.answers == 1 ? '' : 's'} so far'),
        if (status.cost > 0) _Fact(icon: Icons.bolt_rounded, text: 'Costs ${status.cost} Sparks'),
        if (_phase == _Phase.writeFailed) ...[const SizedBox(height: 12), _ErrorLine(_error ?? '')],
        const SizedBox(height: 22),
        FilledButton.icon(
          key: const ValueKey('notes-generate'),
          onPressed: _generate,
          icon: const Icon(Icons.auto_awesome_rounded, size: 18),
          label: Text(_phase == _Phase.writeFailed ? 'Try again' : 'Generate notes'),
          style: FilledButton.styleFrom(
            backgroundColor: Paper.accent,
            foregroundColor: Colors.white,
            minimumSize: const Size.fromHeight(50),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
            textStyle: sans(15, weight: FontWeight.w600),
          ),
        ),
      ],
    );
  }

  Widget _bottomBar(ChatNotes notes) {
    return Container(
      decoration: BoxDecoration(color: Paper.surface, border: Border(top: BorderSide(color: Paper.border))),
      padding: const EdgeInsets.fromLTRB(20, 10, 20, 14),
      child: SafeArea(
        top: false,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          if (_pdfMessage != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: _pdfSaved ? _DoneLine(_pdfMessage!) : _ErrorLine(_pdfMessage!),
            ),
          Row(children: [
            Expanded(
              child: FilledButton.icon(
                key: const ValueKey('notes-download'),
                onPressed: _pdfBusy != null ? null : () => _download(notes),
                icon: _pdfBusy == 'download'
                    ? SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Paper.page))
                    : const Icon(Icons.download_rounded, size: 18),
                label: const Text('Download PDF'),
                style: FilledButton.styleFrom(
                  backgroundColor: Paper.ink,
                  foregroundColor: Paper.page,
                  minimumSize: const Size.fromHeight(48),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                  textStyle: sans(14.5, weight: FontWeight.w600),
                ),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Builder(
                builder: (buttonContext) => OutlinedButton.icon(
                  key: const ValueKey('notes-share'),
                  onPressed: _pdfBusy != null ? null : () => _sharePdf(notes, buttonContext),
                  icon: _pdfBusy == 'share'
                      ? SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Paper.ink))
                      : const Icon(Icons.share_rounded, size: 18),
                  label: const Text('Share'),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: Paper.ink,
                    side: BorderSide(color: Paper.borderStrong),
                    minimumSize: const Size.fromHeight(48),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                    textStyle: sans(14.5, weight: FontWeight.w600),
                  ),
                ),
              ),
            ),
          ]),
        ]),
      ),
    );
  }
}

// ------------------------------------------------------------------ the notes

class _NotesView extends StatelessWidget {
  const _NotesView({required this.notes, this.scrollController, this.header = const []});

  final ChatNotes notes;
  final ScrollController? scrollController;
  final List<Widget> header;

  @override
  Widget build(BuildContext context) {
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    final made = notes.createdAt.toLocal();
    final text = sans(14.5, color: Paper.body, height: 1.55);
    return ListView(
      key: const ValueKey('notes-view'),
      controller: scrollController,
      padding: const EdgeInsets.fromLTRB(20, 4, 20, 24),
      children: [
        ...header,
        Text(notes.title, key: const ValueKey('notes-title'), style: serif(26, height: 1.2)),
        const SizedBox(height: 6),
        Text(
          'Covers ${notes.answers} answer${notes.answers == 1 ? '' : 's'} · ${made.day} ${months[made.month - 1]} ${made.year}',
          style: mono(10),
        ),
        const SizedBox(height: 16),
        _Box(
          fill: Paper.card,
          line: Paper.border,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('TOPICS COVERED', style: mono(10, color: Paper.accent)),
            const SizedBox(height: 8),
            for (final (i, t) in notes.topics.indexed)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 3),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  SizedBox(width: 22, child: Text('${i + 1}', style: sans(14, color: Paper.accent, weight: FontWeight.w700))),
                  Expanded(child: RichMessageText(t.name, selectable: false, style: sans(14.5, height: 1.4))),
                ]),
              ),
          ]),
        ),
        for (final (i, topic) in notes.topics.indexed) ...[
          const SizedBox(height: 22),
          _TopicView(index: i + 1, topic: topic, text: text),
        ],
        const SizedBox(height: 24),
        _Box(
          fill: Paper.card,
          line: Paper.border,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('SUMMARY', style: mono(10, color: Paper.accent)),
            const SizedBox(height: 8),
            RichMessageText(notes.summary, style: text),
          ]),
        ),
        if (notes.stoppedAt != null) ...[
          const SizedBox(height: 18),
          Text('WHERE YOU STOPPED', style: mono(10, color: Paper.accent)),
          const SizedBox(height: 6),
          RichMessageText(notes.stoppedAt!, style: text),
        ],
      ],
    );
  }
}

class _TopicView extends StatelessWidget {
  const _TopicView({required this.index, required this.topic, required this.text});

  final int index;
  final NoteTopic topic;
  final TextStyle text;

  @override
  Widget build(BuildContext context) {
    return Column(
      key: ValueKey('notes-topic-$index'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('$index.  ', style: serif(19, color: Paper.accent)),
          Expanded(child: RichMessageText(topic.name, selectable: false, style: serif(19))),
        ]),
        const SizedBox(height: 8),
        for (final p in topic.points)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Padding(
                padding: const EdgeInsets.only(top: 9, right: 10, left: 2),
                child: Container(width: 5, height: 5, decoration: BoxDecoration(color: Paper.accent, shape: BoxShape.circle)),
              ),
              Expanded(child: RichMessageText(p, style: text)),
            ]),
          ),
        if (topic.formulas.isNotEmpty) ...[
          const SizedBox(height: 6),
          _Box(
            fill: Paper.accentSoft,
            line: Paper.accentLine,
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('KEY FORMULAS', style: mono(10, color: Paper.accent)),
              const SizedBox(height: 4),
              for (final f in topic.formulas) RichMessageText('\$\$$f\$\$', style: text),
            ]),
          ),
        ],
        if (topic.example != null) ...[
          const SizedBox(height: 8),
          _Box(
            fill: Paper.card,
            line: Paper.border,
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('EXAMPLE', style: mono(10, color: Paper.accent)),
              const SizedBox(height: 6),
              RichMessageText(topic.example!, style: sans(14, color: Paper.body, height: 1.5)),
            ]),
          ),
        ],
      ],
    );
  }
}

// ------------------------------------------------------------------ pieces

class _Box extends StatelessWidget {
  const _Box({required this.child, required this.fill, required this.line});
  final Widget child;
  final Color fill;
  final Color line;

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 14),
        decoration: BoxDecoration(
          color: fill,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: line),
        ),
        child: child,
      );
}

class _Writing extends StatelessWidget {
  const _Writing({required this.update});
  final bool update;

  @override
  Widget build(BuildContext context) => Center(
        key: const ValueKey('notes-writing'),
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            CircularProgressIndicator(strokeWidth: 2.5, color: Paper.accent),
            const SizedBox(height: 18),
            Text(update ? 'Updating your notes…' : 'Writing your notes…', style: serif(20)),
            const SizedBox(height: 6),
            Text(
              'Reading the whole chat. You can close this -- the notes will be here when you come back.',
              textAlign: TextAlign.center,
              style: sans(13, color: Paper.body, height: 1.45),
            ),
          ]),
        ),
      );
}

class _UpdateBanner extends StatelessWidget {
  const _UpdateBanner({required this.cost, required this.onUpdate});
  final int cost;
  final VoidCallback onUpdate;

  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('notes-out-of-date'),
        margin: const EdgeInsets.only(bottom: 14),
        padding: const EdgeInsets.fromLTRB(14, 10, 8, 10),
        decoration: BoxDecoration(
          color: Paper.warnSoft,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(children: [
          Expanded(
            child: Text("You've studied more since these notes.", style: sans(13, color: Paper.ink, height: 1.35)),
          ),
          TextButton(
            key: const ValueKey('notes-update'),
            onPressed: onUpdate,
            child: Text(cost > 0 ? 'Update · $cost ⚡' : 'Update'),
          ),
        ]),
      );
}

class _DoneLine extends StatelessWidget {
  const _DoneLine(this.message);
  final String message;

  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('notes-saved'),
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(color: Paper.oliveSoft, borderRadius: BorderRadius.circular(10)),
        child: Row(children: [
          Icon(Icons.check_circle_rounded, size: 16, color: Paper.olive),
          const SizedBox(width: 8),
          Expanded(child: Text(message, style: sans(13, color: Paper.ink, height: 1.35))),
        ]),
      );
}

class _ErrorLine extends StatelessWidget {
  const _ErrorLine(this.message);
  final String message;

  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('notes-error'),
        width: double.infinity,
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(color: Paper.dangerSoft, borderRadius: BorderRadius.circular(10)),
        child: Text(message, style: sans(13, color: Paper.danger, height: 1.35)),
      );
}

class _Fact extends StatelessWidget {
  const _Fact({required this.icon, required this.text});
  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Row(children: [
          Icon(icon, size: 16, color: Paper.faint),
          const SizedBox(width: 8),
          Text(text, style: sans(13.5, color: Paper.body)),
        ]),
      );
}

class _Message extends StatelessWidget {
  const _Message({required this.icon, required this.title, required this.body, this.action});
  final IconData icon;
  final String title;
  final String body;
  final Widget? action;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(32),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 34, color: Paper.faint),
            const SizedBox(height: 12),
            Text(title, textAlign: TextAlign.center, style: serif(20)),
            const SizedBox(height: 6),
            Text(body, textAlign: TextAlign.center, style: sans(13.5, color: Paper.body, height: 1.45)),
            if (action != null) ...[const SizedBox(height: 10), action!],
          ]),
        ),
      );
}
