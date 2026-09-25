import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import '../widgets/composer.dart';
import 'room_api.dart';
import 'room_chat.dart';
import 'room_controller.dart';
import 'room_models.dart';
import 'room_panels.dart';

/// How a room's socket is opened. Replaceable so tests use a scripted fake.
Future<RoomTransport> Function(Uri uri) roomSocketConnector = WebSocketRoomTransport.connect;

/// One room: a WhatsApp-style group chat with, above it, the shared stage +
/// board (everyone's tasks, Versa's questions) and "For you" (this person's
/// task and the options waiting for their click).
class RoomScreen extends StatefulWidget {
  const RoomScreen({super.key, required this.membership});
  final RoomMembership membership;

  @override
  State<RoomScreen> createState() => _RoomScreenState();
}

class _RoomScreenState extends State<RoomScreen> {
  late final RoomController _room;
  late final RoomMemberships _memberships;
  bool _topOpen = true;

  @override
  void initState() {
    super.initState();
    final app = context.read<AppState>();
    final api = RoomApi.of(app.api);
    _memberships = RoomMemberships(app.prefs, app.learner!.id);
    final m = widget.membership;
    _room = RoomController(
      code: m.code,
      memberId: m.memberId,
      connect: (code, memberId) => roomSocketConnector(api.socketUri(code, memberId)),
      onSeen: (seq) => _memberships.markSeen(m.code, seq),
    )..start();
  }

  @override
  void dispose() {
    _room.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: _room,
      builder: (context, _) => LayoutBuilder(builder: (context, c) {
        final wide = c.maxWidth >= 860;
        return Container(
          color: Paper.surface,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _Header(
                room: _room,
                membership: widget.membership,
                topOpen: _topOpen,
                onToggleTop: () => setState(() => _topOpen = !_topOpen),
              ),
              AnimatedSize(
                duration: const Duration(milliseconds: 220),
                curve: Curves.easeOutCubic,
                alignment: Alignment.topCenter,
                child: _topOpen
                    ? Padding(
                        padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
                        child: wide ? _wideTop() : _NarrowTop(room: _room),
                      )
                    : const SizedBox(width: double.infinity),
              ),
              const Divider(height: 1),
              Expanded(child: _chat()),
              if (_room.error != null) _ErrorLine(text: _room.error!, onClose: _room.clearError),
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 8, 12, 12),
                child: Composer(
                  enabled: _room.isLive,
                  onSend: _room.send,
                  onChanged: _room.typing,
                  hint: _room.isLive ? 'Message the group · @Versa to ask Versa' : 'Connecting…',
                ),
              ),
            ],
          ),
        );
      }),
    );
  }

  Widget _wideTop() {
    return SizedBox(
      height: 270,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Expanded(
            flex: 5,
            child: RoomPanelFrame(
              title: 'Stage · tasks · questions',
              padded: false,
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Expanded(flex: 5, child: RoomStage(controller: _room)),
                  const VerticalDivider(width: 1),
                  Expanded(
                    flex: 4,
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(12, 8, 10, 8),
                      child: RoomBoardPanel(controller: _room),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            flex: 3,
            child: RoomPanelFrame(
              key: const ValueKey('for-you-frame'),
              title: 'For you',
              child: ForYouPanel(controller: _room),
            ),
          ),
        ],
      ),
    );
  }

  Widget _chat() {
    final me = _room.memberId;
    final typing = _room.typingNames;
    if (_room.status == RoomStatus.connecting && _room.messages.isEmpty) {
      return const Center(child: CircularProgressIndicator(color: Paper.accent));
    }
    return Container(
      color: Paper.page,
      child: RoomMessageList(
        messages: _room.messages,
        meId: me,
        footer: typing.isEmpty ? null : RoomTypingBubble(names: typing),
      ),
    );
  }
}

/// On a phone the three boxes share one strip, as tabs.
class _NarrowTop extends StatefulWidget {
  const _NarrowTop({required this.room});
  final RoomController room;

  @override
  State<_NarrowTop> createState() => _NarrowTopState();
}

class _NarrowTopState extends State<_NarrowTop> {
  int _tab = 0;

  @override
  Widget build(BuildContext context) {
    final room = widget.room;
    final waiting = room.board.options.isNotEmpty;
    final tabs = ['Stage', 'Board', waiting ? 'For you •' : 'For you'];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SegmentedButton<int>(
          key: const ValueKey('room-top-tabs'),
          segments: [
            for (var i = 0; i < tabs.length; i++) ButtonSegment(value: i, label: Text(tabs[i])),
          ],
          selected: {_tab},
          showSelectedIcon: false,
          onSelectionChanged: (s) => setState(() => _tab = s.first),
          style: SegmentedButton.styleFrom(
            selectedBackgroundColor: Paper.accentSoft,
            selectedForegroundColor: Paper.accentDark,
            textStyle: sans(12.5, weight: FontWeight.w600),
            visualDensity: VisualDensity.compact,
          ),
        ),
        const SizedBox(height: 8),
        SizedBox(
          height: 210,
          child: IndexedStack(
            index: _tab,
            children: [
              ClipRRect(borderRadius: BorderRadius.circular(12), child: RoomStage(controller: room)),
              RoomPanelFrame(title: 'Tasks · questions', child: RoomBoardPanel(controller: room)),
              RoomPanelFrame(title: 'For you', child: ForYouPanel(controller: room)),
            ],
          ),
        ),
      ],
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.room, required this.membership, required this.topOpen, required this.onToggleTop});

  final RoomController room;
  final RoomMembership membership;
  final bool topOpen;
  final VoidCallback onToggleTop;

  String _subtitle() {
    if (room.status == RoomStatus.reconnecting) return 'Reconnecting…';
    if (room.status == RoomStatus.connecting) return 'Connecting…';
    final typing = room.typingNames;
    if (typing.isNotEmpty) return typing.length == 1 ? '${typing.first} is typing…' : '${typing.join(', ')} are typing…';
    final names = [
      for (final m in room.board.members) m.id == room.memberId ? 'You' : m.name,
      'Versa',
    ];
    final online = room.board.members.where((m) => m.online).length;
    return '${names.join(', ')} · $online online';
  }

  @override
  Widget build(BuildContext context) {
    final title = room.room?.title ?? membership.code;
    final typing = room.typingNames.isNotEmpty && room.isLive;
    return Container(
      padding: const EdgeInsets.fromLTRB(6, 8, 10, 8),
      decoration: const BoxDecoration(
        color: Paper.sliver,
        border: Border(bottom: BorderSide(color: Paper.border)),
      ),
      child: Row(
        children: [
          IconButton(
            key: const ValueKey('room-back'),
            tooltip: 'Back to your rooms',
            onPressed: () => Navigator.of(context).maybePop(),
            icon: const Icon(Icons.arrow_back_rounded, color: Paper.body),
          ),
          Container(
            width: 38,
            height: 38,
            alignment: Alignment.center,
            decoration: const BoxDecoration(color: Paper.accentSoft, shape: BoxShape.circle),
            child: const Icon(Icons.groups_rounded, color: Paper.accent, size: 22),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: InkWell(
              onTap: room.room == null ? null : () => _showTopic(context, room.room!),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title,
                      key: const ValueKey('room-title'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: sans(15.5, weight: FontWeight.w700)),
                  Text(_subtitle(),
                      key: const ValueKey('room-subtitle'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: sans(12, color: typing ? Paper.accent : Paper.muted)),
                ],
              ),
            ),
          ),
          TextButton.icon(
            key: const ValueKey('room-invite'),
            onPressed: () => showDialog<void>(
              context: context,
              builder: (_) => InviteDialog(code: room.room?.code ?? membership.code),
            ),
            icon: const Icon(Icons.person_add_alt_1_rounded, size: 18),
            label: Text(room.room?.code ?? membership.code),
            style: TextButton.styleFrom(foregroundColor: Paper.accentDark),
          ),
          IconButton(
            key: const ValueKey('room-toggle-top'),
            tooltip: topOpen ? 'Hide the stage and tasks' : 'Show the stage and tasks',
            onPressed: onToggleTop,
            icon: Icon(topOpen ? Icons.expand_less_rounded : Icons.expand_more_rounded, color: Paper.body),
          ),
        ],
      ),
    );
  }

  void _showTopic(BuildContext context, RoomInfo info) {
    final source = switch (info.sourceKind) {
      'pdf' => 'From the PDF ${info.resourceFilename ?? ''}',
      'link' => 'From ${info.resourceUrl ?? 'a web page'}',
      _ => 'Searched: ${info.query}',
    };
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: Paper.surface,
        title: Text(info.title, style: serif(19)),
        content: SizedBox(
          width: 480,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text('$source · created by ${info.createdBy}', style: sans(12.5, color: Paper.muted)),
                const SizedBox(height: 12),
                for (var i = 0; i < info.outline.length; i++)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text('${i + 1}. ${info.outline[i].title}', style: sans(14, weight: FontWeight.w600)),
                      Text(info.outline[i].summary, style: sans(12.5, color: Paper.body, height: 1.4)),
                    ]),
                  ),
              ],
            ),
          ),
        ),
        actions: [TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Close'))],
      ),
    );
  }
}

/// The room code and how someone else gets in.
class InviteDialog extends StatelessWidget {
  const InviteDialog({super.key, required this.code});
  final String code;

  @override
  Widget build(BuildContext context) {
    final server = kIsWeb ? Uri.base.origin : context.read<AppState>().api.baseUrl;
    return AlertDialog(
      backgroundColor: Paper.surface,
      title: Text('Invite people', style: serif(19)),
      content: SizedBox(
        width: 440,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('Room code', style: mono(10.5)),
            const SizedBox(height: 6),
            Row(children: [
              Expanded(
                child: SelectableText(code,
                    key: const ValueKey('invite-code'), style: sans(26, weight: FontWeight.w700, color: Paper.accentDark)),
              ),
              IconButton(
                tooltip: 'Copy the code',
                onPressed: () {
                  Clipboard.setData(ClipboardData(text: code));
                  ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Room code copied')));
                },
                icon: const Icon(Icons.copy_rounded, color: Paper.body),
              ),
            ]),
            const SizedBox(height: 14),
            Text(
              'On their own device, they open Versa at $server, go to Modes → Study with others → '
              'Join a room, and enter their name and this code.',
              style: sans(13, color: Paper.body, height: 1.5),
            ),
          ],
        ),
      ),
      actions: [TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Done'))],
    );
  }
}

class _ErrorLine extends StatelessWidget {
  const _ErrorLine({required this.text, required this.onClose});
  final String text;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) => Container(
        key: const ValueKey('room-error'),
        margin: const EdgeInsets.fromLTRB(12, 8, 12, 0),
        padding: const EdgeInsets.fromLTRB(12, 6, 4, 6),
        decoration: BoxDecoration(color: Paper.warnSoft, borderRadius: BorderRadius.circular(8)),
        child: Row(children: [
          Expanded(child: Text(text, style: sans(12.5, color: Paper.ink))),
          IconButton(
            visualDensity: VisualDensity.compact,
            onPressed: onClose,
            icon: const Icon(Icons.close_rounded, size: 16, color: Paper.body),
          ),
        ]),
      );
}
