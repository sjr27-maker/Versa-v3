import 'package:flutter/material.dart';

import '../models.dart';
import '../theme.dart';

/// The list of past chats for the CURRENTLY ACTIVE mode — persistent as a
/// left column on a wide screen (see sandbox_screen.dart), or inside
/// [showChatHistorySheet] on a narrow one. Deliberately only ever rendered
/// while inside one mode's own chat screen: never on the Modes picker, Home,
/// or any other page, and never mixing chats across modes into one list —
/// that split is the whole point of this feature.
class ChatHistoryRail extends StatelessWidget {
  const ChatHistoryRail({
    super.key,
    required this.chats,
    required this.loading,
    required this.activeSessionId,
    required this.onSelect,
    required this.onNewChat,
  });

  final List<ChatSummary> chats;
  final bool loading;
  final String? activeSessionId;
  final void Function(ChatSummary chat) onSelect;
  final VoidCallback onNewChat;

  @override
  Widget build(BuildContext context) {
    return Container(
      key: const ValueKey('chat-history-rail'),
      width: 232,
      decoration: const BoxDecoration(
        color: Paper.sliver,
        border: Border(right: BorderSide(color: Paper.border)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 20, 10, 10),
            child: Row(
              children: [
                Text('CHATS', style: mono(10)),
                const Spacer(),
                IconButton(
                  key: const ValueKey('history-new-chat'),
                  tooltip: 'New chat',
                  visualDensity: VisualDensity.compact,
                  onPressed: onNewChat,
                  icon: const Icon(Icons.add_rounded, size: 18, color: Paper.faint),
                ),
              ],
            ),
          ),
          Expanded(
            child: _ChatList(
              chats: chats,
              loading: loading,
              activeSessionId: activeSessionId,
              onSelect: onSelect,
            ),
          ),
        ],
      ),
    );
  }
}

/// The same list, reached from a header icon on a narrow screen instead of
/// taking up a persistent column.
Future<void> showChatHistorySheet(
  BuildContext context, {
  required List<ChatSummary> chats,
  required bool loading,
  required String? activeSessionId,
  required void Function(ChatSummary chat) onSelect,
  required VoidCallback onNewChat,
}) {
  return showModalBottomSheet<void>(
    context: context,
    backgroundColor: Paper.sliver,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
    ),
    builder: (sheetContext) => SafeArea(
      child: SizedBox(
        height: 420,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 16, 10, 8),
              child: Row(
                children: [
                  Text('CHATS', style: mono(10)),
                  const Spacer(),
                  TextButton.icon(
                    key: const ValueKey('history-sheet-new-chat'),
                    onPressed: () {
                      Navigator.of(sheetContext).pop();
                      onNewChat();
                    },
                    icon: const Icon(Icons.add_rounded, size: 16),
                    label: const Text('New chat'),
                  ),
                ],
              ),
            ),
            Expanded(
              child: _ChatList(
                chats: chats,
                loading: loading,
                activeSessionId: activeSessionId,
                onSelect: (chat) {
                  Navigator.of(sheetContext).pop();
                  onSelect(chat);
                },
              ),
            ),
          ],
        ),
      ),
    ),
  );
}

class _ChatList extends StatelessWidget {
  const _ChatList({
    required this.chats,
    required this.loading,
    required this.activeSessionId,
    required this.onSelect,
  });

  final List<ChatSummary> chats;
  final bool loading;
  final String? activeSessionId;
  final void Function(ChatSummary chat) onSelect;

  @override
  Widget build(BuildContext context) {
    if (chats.isEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16),
        child: Text(
          loading ? 'Loading…' : 'No chats yet.',
          key: const ValueKey('chat-history-empty'),
          style: sans(12.5, color: Paper.faint),
        ),
      );
    }
    return ListView.builder(
      key: const ValueKey('chat-history-list'),
      padding: const EdgeInsets.symmetric(vertical: 4),
      itemCount: chats.length,
      itemBuilder: (context, i) {
        final chat = chats[i];
        return _ChatRow(
          chat: chat,
          active: chat.sessionId == activeSessionId,
          onTap: () => onSelect(chat),
        );
      },
    );
  }
}

class _ChatRow extends StatefulWidget {
  const _ChatRow({required this.chat, required this.active, required this.onTap});

  final ChatSummary chat;
  final bool active;
  final VoidCallback onTap;

  @override
  State<_ChatRow> createState() => _ChatRowState();
}

class _ChatRowState extends State<_ChatRow> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final chat = widget.chat;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        key: ValueKey('chat-row-${chat.sessionId}'),
        behavior: HitTestBehavior.opaque,
        onTap: widget.onTap,
        child: Container(
          margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
          decoration: BoxDecoration(
            color: widget.active
                ? Paper.accentSoft
                : (_hover ? const Color(0x0F000000) : Colors.transparent),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                chat.preview ?? 'New chat',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: sans(
                  12.5,
                  color: widget.active ? Paper.ink : Paper.body,
                  weight: widget.active ? FontWeight.w600 : FontWeight.w400,
                  height: 1.3,
                ),
              ),
              const SizedBox(height: 3),
              Text(_relativeTime(chat.lastActivityAt), style: mono(9.5)),
            ],
          ),
        ),
      ),
    );
  }
}

String _relativeTime(DateTime t) {
  final diff = DateTime.now().difference(t);
  if (diff.inMinutes < 1) return 'just now';
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  if (diff.inDays < 7) return '${diff.inDays}d ago';
  return '${t.year}-${t.month.toString().padLeft(2, '0')}-${t.day.toString().padLeft(2, '0')}';
}
