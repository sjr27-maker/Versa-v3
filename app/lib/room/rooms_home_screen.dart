import 'dart:async';
import 'dart:math';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api.dart';
import '../app_state.dart';
import '../theme.dart';
import '../topic/topic_widgets.dart';
import 'room_api.dart';
import 'room_chat.dart';
import 'room_models.dart';
import 'rooms_root.dart';

typedef PickedPdf = ({String name, Uint8List bytes});

/// Opens the platform's file dialog for one PDF (web + desktop: read as
/// bytes). Replaceable so tests never open a real dialog.
Future<PickedPdf?> Function() roomPdfPicker = () async {
  final file = await FilePicker.pickFile(type: FileType.custom, allowedExtensions: const ['pdf']);
  if (file == null) return null;
  return (name: file.name, bytes: await file.readAsBytes());
};

/// Study with others: the rooms this person is in (newest activity first,
/// with unread counts), and the two ways in -- create a room, or join one.
class RoomsHomeScreen extends StatefulWidget {
  const RoomsHomeScreen({super.key});

  @override
  State<RoomsHomeScreen> createState() => _RoomsHomeScreenState();
}

class _RoomsHomeScreenState extends State<RoomsHomeScreen> {
  late RoomApi _api;
  late RoomMemberships _memberships;
  List<RoomSummary>? _rooms;
  String? _error;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    final app = context.read<AppState>();
    _api = RoomApi.of(app.api);
    _memberships = RoomMemberships(app.prefs, app.learner!.id);
    _load();
    // The list is a glance, not a live feed: a light refresh keeps unread
    // counts and last messages honest while it's showing.
    _poll = Timer.periodic(const Duration(seconds: 15), (_) => _load());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final rows = await _api.summaries(_memberships.load());
      rows.sort((a, b) => b.activityAt.compareTo(a.activityAt));
      if (!mounted) return;
      setState(() {
        _rooms = rows;
        _error = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = 'Could not load your rooms: $e');
    }
  }

  Future<void> _enter(RoomJoined joined) async {
    final m = RoomMembership(code: joined.room.code, memberId: joined.me.id, name: joined.me.name);
    await _memberships.add(m);
    if (!mounted) return;
    await pushRoom(context, m);
    _load();
  }

  Future<void> _open(RoomSummary s) async {
    final m = _memberships.load().where((x) => x.code.toLowerCase() == s.code.toLowerCase()).firstOrNull ??
        RoomMembership(code: s.code, memberId: s.memberId, name: s.name);
    await pushRoom(context, m);
    _load();
  }

  Future<void> _create() async {
    final joined = await Navigator.of(context).push<RoomJoined>(roomRoute((_) => const CreateRoomScreen()));
    if (joined != null && mounted) await _enter(joined);
  }

  Future<void> _join() async {
    final joined = await showDialog<RoomJoined>(context: context, builder: (_) => JoinRoomDialog(api: _api));
    if (joined != null && mounted) await _enter(joined);
  }

  @override
  Widget build(BuildContext context) {
    final shell = context.read<ShellState>();
    return Container(
      color: Paper.surface,
      child: RefreshIndicator(
        color: Paper.accent,
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(32, 32, 32, 60),
          children: [
            Align(
              alignment: Alignment.topLeft,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 900),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    PageHeading(
                      eyebrow: 'STUDY WITH OTHERS · EXPERIMENTAL',
                      title: 'Learn together',
                      onBack: shell.closeRooms,
                      backKey: const ValueKey('rooms-back'),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      'A group chat for a topic. Invite people with a room code; Versa joins as one '
                      'more member, gives each of you a task and steps in when it helps.',
                      style: sans(13.5, color: Paper.body, height: 1.5),
                    ),
                    const SizedBox(height: 18),
                    Wrap(spacing: 10, runSpacing: 10, children: [
                      FilledButton.icon(
                        key: const ValueKey('rooms-create'),
                        onPressed: _create,
                        style: FilledButton.styleFrom(backgroundColor: Paper.accent),
                        icon: const Icon(Icons.add_rounded, size: 18),
                        label: const Text('Create a room'),
                      ),
                      OutlinedButton.icon(
                        key: const ValueKey('rooms-join'),
                        onPressed: _join,
                        style: OutlinedButton.styleFrom(
                          foregroundColor: Paper.accentDark,
                          side: const BorderSide(color: Paper.accent),
                        ),
                        icon: const Icon(Icons.login_rounded, size: 18),
                        label: const Text('Join a room'),
                      ),
                    ]),
                    const SizedBox(height: 28),
                    Text('Your rooms', style: serif(21)),
                    const SizedBox(height: 10),
                    _list(),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _list() {
    if (_error != null && _rooms == null) return RetryLine(message: _error!, onRetry: _load);
    final rooms = _rooms;
    if (rooms == null) {
      return const Padding(
        padding: EdgeInsets.all(24),
        child: Center(child: CircularProgressIndicator(color: Paper.accent)),
      );
    }
    if (rooms.isEmpty) {
      return Container(
        padding: const EdgeInsets.all(20),
        decoration: BoxDecoration(
          color: Paper.card,
          border: Border.all(color: Paper.border),
          borderRadius: BorderRadius.circular(14),
        ),
        child: Text('No rooms yet. Create one and share its code, or join a friend\'s.',
            style: sans(13.5, color: Paper.muted)),
      );
    }
    return Container(
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(14),
      ),
      clipBehavior: Clip.antiAlias,
      child: Column(children: [
        for (var i = 0; i < rooms.length; i++) ...[
          if (i > 0) const Divider(height: 1, indent: 74),
          _RoomRow(summary: rooms[i], onTap: () => _open(rooms[i])),
        ],
      ]),
    );
  }
}

class _RoomRow extends StatelessWidget {
  const _RoomRow({required this.summary, required this.onTap});
  final RoomSummary summary;
  final VoidCallback onTap;

  String _preview() {
    final m = summary.lastMessage;
    if (m == null) return 'No messages yet';
    final who = m.isSystem
        ? ''
        : (m.memberId == summary.memberId && !m.fromVersa ? 'You: ' : '${m.senderName}: ');
    final text = switch (m.kind) {
      'task' => '📌 Task for ${m.toMemberId == summary.memberId ? 'you' : m.toName}: ${m.text}',
      'progress' => '✅ ${m.toName} finished a task',
      'question' => '❓ ${m.text}',
      _ => m.text,
    };
    return '$who$text';
  }

  String _when() {
    final t = summary.activityAt;
    return DateUtils.isSameDay(t, DateTime.now()) ? clockTime(t) : dayLabel(t);
  }

  @override
  Widget build(BuildContext context) {
    final unread = summary.unread;
    return InkWell(
      key: ValueKey('room-row-${summary.code}'),
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(children: [
          Container(
            width: 46,
            height: 46,
            alignment: Alignment.center,
            decoration: const BoxDecoration(color: Paper.accentSoft, shape: BoxShape.circle),
            child: const Icon(Icons.groups_rounded, color: Paper.accent),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                Expanded(
                  child: Text(summary.title,
                      maxLines: 1, overflow: TextOverflow.ellipsis, style: sans(15, weight: FontWeight.w700)),
                ),
                Text(_when(), style: sans(11.5, color: unread > 0 ? Paper.accent : Paper.faint)),
              ]),
              const SizedBox(height: 3),
              Row(children: [
                Expanded(
                  child: Text(_preview(),
                      maxLines: 1, overflow: TextOverflow.ellipsis, style: sans(13, color: Paper.muted)),
                ),
                if (unread > 0)
                  Container(
                    key: ValueKey('room-unread-${summary.code}'),
                    margin: const EdgeInsets.only(left: 8),
                    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                    constraints: const BoxConstraints(minWidth: 20),
                    decoration: BoxDecoration(color: Paper.accent, borderRadius: BorderRadius.circular(100)),
                    child: Text(unread > 99 ? '99+' : '$unread',
                        textAlign: TextAlign.center,
                        style: sans(11, color: Colors.white, weight: FontWeight.w700)),
                  ),
              ]),
              const SizedBox(height: 3),
              Text(
                '${summary.code} · ${summary.memberNames.length} ${summary.memberNames.length == 1 ? 'person' : 'people'}'
                '${summary.online > 0 ? ' · ${summary.online} online' : ''}',
                style: mono(9.5),
              ),
            ]),
          ),
        ]),
      ),
    );
  }
}

/// Join with your name and a room code.
class JoinRoomDialog extends StatefulWidget {
  const JoinRoomDialog({super.key, required this.api});
  final RoomApi api;

  @override
  State<JoinRoomDialog> createState() => _JoinRoomDialogState();
}

class _JoinRoomDialogState extends State<JoinRoomDialog> {
  late final TextEditingController _name;
  final _code = TextEditingController();
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _name = TextEditingController(text: context.read<AppState>().learner?.label ?? '');
  }

  @override
  void dispose() {
    _name.dispose();
    _code.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final name = _name.text.trim(), code = _code.text.trim();
    if (name.isEmpty || code.isEmpty) {
      setState(() => _error = 'Enter your name and the room code.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final joined = await widget.api.join(code: code, name: name);
      if (mounted) Navigator.of(context).pop(joined);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (e) {
      if (mounted) setState(() => _error = 'Could not reach the server ($e)');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      backgroundColor: Paper.surface,
      title: Text('Join a room', style: serif(19)),
      content: SizedBox(
        width: 420,
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(
            key: const ValueKey('join-name'),
            controller: _name,
            decoration: const InputDecoration(labelText: 'Your name in the room'),
          ),
          const SizedBox(height: 10),
          TextField(
            key: const ValueKey('join-code'),
            controller: _code,
            autofocus: true,
            onSubmitted: (_) => _submit(),
            decoration: InputDecoration(labelText: 'Room code', errorText: _error),
          ),
        ]),
      ),
      actions: [
        TextButton(onPressed: _busy ? null : () => Navigator.of(context).pop(), child: const Text('Cancel')),
        FilledButton(
          key: const ValueKey('join-go'),
          onPressed: _busy ? null : _submit,
          style: FilledButton.styleFrom(backgroundColor: Paper.accent),
          child: Text(_busy ? 'Joining…' : 'Join'),
        ),
      ],
    );
  }
}

enum _Source { search, pdf, link }

/// Create a room: your name, a room code to share, and the topic -- searched,
/// from a PDF, or from a web page (outlined like Learn a topic does).
class CreateRoomScreen extends StatefulWidget {
  const CreateRoomScreen({super.key});

  @override
  State<CreateRoomScreen> createState() => _CreateRoomScreenState();
}

class _CreateRoomScreenState extends State<CreateRoomScreen> {
  late final TextEditingController _name;
  final _code = TextEditingController(text: _randomCode());
  final _topic = TextEditingController();
  final _link = TextEditingController();
  _Source _source = _Source.search;
  PickedPdf? _pdf;
  String? _error;
  bool _busy = false;

  static String _randomCode() {
    const words = ['study', 'learn', 'think', 'quest', 'spark', 'orbit', 'prism', 'atlas'];
    final r = Random();
    return '${words[r.nextInt(words.length)]}-${1000 + r.nextInt(9000)}';
  }

  @override
  void initState() {
    super.initState();
    _name = TextEditingController(text: context.read<AppState>().learner?.label ?? '');
  }

  @override
  void dispose() {
    for (final c in [_name, _code, _topic, _link]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _pickPdf() async {
    try {
      final picked = await roomPdfPicker();
      if (picked == null || !mounted) return;
      if (!picked.name.toLowerCase().endsWith('.pdf')) {
        setState(() => _error = 'That isn\'t a PDF.');
        return;
      }
      setState(() {
        _pdf = picked;
        _error = null;
      });
    } catch (e) {
      setState(() => _error = 'Could not open the file picker ($e)');
    }
  }

  String? _validate() {
    if (_name.text.trim().isEmpty) return 'Enter your name.';
    if (!RegExp(r'^[A-Za-z0-9][A-Za-z0-9_-]{2,23}$').hasMatch(_code.text.trim())) {
      return 'Room code: 3-24 letters, digits, - or _.';
    }
    switch (_source) {
      case _Source.search:
        if (_topic.text.trim().isEmpty) return 'What will the room learn? Type a topic.';
      case _Source.pdf:
        if (_pdf == null) return 'Choose a PDF.';
      case _Source.link:
        final uri = Uri.tryParse(_link.text.trim());
        if (uri == null || !(uri.scheme == 'http' || uri.scheme == 'https') || uri.host.isEmpty) {
          return 'Paste a full web address starting with http:// or https://';
        }
    }
    return null;
  }

  Future<void> _submit() async {
    final problem = _validate();
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final api = RoomApi.of(context.read<AppState>().api);
    final name = _name.text.trim(), code = _code.text.trim();
    try {
      final joined = switch (_source) {
        _Source.search => await api.create(code: code, name: name, topic: _topic.text.trim()),
        _Source.link => await api.create(code: code, name: name, link: _link.text.trim()),
        _Source.pdf => await api.createFromPdf(code: code, name: name, filename: _pdf!.name, bytes: _pdf!.bytes),
      };
      if (mounted) Navigator.of(context).pop(joined);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (e) {
      if (mounted) setState(() => _error = 'Could not reach the server ($e)');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: Paper.surface,
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(32, 32, 32, 60),
        child: Align(
          alignment: Alignment.topLeft,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 620),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                PageHeading(
                  eyebrow: 'STUDY WITH OTHERS',
                  title: 'Create a room',
                  onBack: () => Navigator.of(context).maybePop(),
                  backKey: const ValueKey('create-room-back'),
                ),
                const SizedBox(height: 20),
                TextField(
                  key: const ValueKey('create-name'),
                  controller: _name,
                  decoration: const InputDecoration(labelText: 'Your name in the room'),
                ),
                const SizedBox(height: 12),
                TextField(
                  key: const ValueKey('create-code'),
                  controller: _code,
                  decoration: InputDecoration(
                    labelText: 'Room code (share it to invite people)',
                    suffixIcon: IconButton(
                      tooltip: 'Another code',
                      onPressed: () => setState(() => _code.text = _randomCode()),
                      icon: const Icon(Icons.casino_outlined, size: 20),
                    ),
                  ),
                ),
                const SizedBox(height: 22),
                Text('Topic', style: sans(13, weight: FontWeight.w700)),
                const SizedBox(height: 8),
                SegmentedButton<_Source>(
                  key: const ValueKey('create-source'),
                  segments: const [
                    ButtonSegment(value: _Source.search, icon: Icon(Icons.search_rounded, size: 16), label: Text('Search')),
                    ButtonSegment(value: _Source.pdf, icon: Icon(Icons.picture_as_pdf_outlined, size: 16), label: Text('PDF')),
                    ButtonSegment(value: _Source.link, icon: Icon(Icons.link_rounded, size: 16), label: Text('Web link')),
                  ],
                  selected: {_source},
                  onSelectionChanged: (s) => setState(() {
                    _source = s.first;
                    _error = null;
                  }),
                  style: SegmentedButton.styleFrom(
                    selectedBackgroundColor: Paper.accentSoft,
                    selectedForegroundColor: Paper.accentDark,
                  ),
                ),
                const SizedBox(height: 12),
                switch (_source) {
                  _Source.search => TextField(
                      key: const ValueKey('create-topic'),
                      controller: _topic,
                      autofocus: true,
                      onSubmitted: (_) => _submit(),
                      decoration: const InputDecoration(hintText: 'e.g. derivatives, the French revolution, TCP/IP'),
                    ),
                  _Source.link => TextField(
                      key: const ValueKey('create-link'),
                      controller: _link,
                      onSubmitted: (_) => _submit(),
                      decoration: const InputDecoration(hintText: 'https://…'),
                    ),
                  _Source.pdf => Row(children: [
                      OutlinedButton.icon(
                        key: const ValueKey('create-pdf'),
                        onPressed: _busy ? null : _pickPdf,
                        icon: const Icon(Icons.upload_file_rounded, size: 18),
                        label: Text(_pdf == null ? 'Choose a PDF' : 'Choose another'),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(_pdf?.name ?? 'No file chosen',
                            overflow: TextOverflow.ellipsis, style: sans(13, color: Paper.muted)),
                      ),
                    ]),
                },
                if (_error != null) ...[
                  const SizedBox(height: 12),
                  Text(_error!, key: const ValueKey('create-error'), style: sans(13, color: Paper.danger)),
                ],
                const SizedBox(height: 22),
                FilledButton(
                  key: const ValueKey('create-go'),
                  onPressed: _busy ? null : _submit,
                  style: FilledButton.styleFrom(
                    backgroundColor: Paper.accent,
                    padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
                  ),
                  child: Text(_busy ? 'Mapping the topic…' : 'Create the room'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
