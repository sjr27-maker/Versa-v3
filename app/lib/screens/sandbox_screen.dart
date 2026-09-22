import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../chat_controller.dart';
import '../theme.dart';
import '../widgets/chat_history_rail.dart';
import '../widgets/composer.dart';
import '../widgets/message_view.dart';
import '../widgets/placeholder_page.dart';

/// The one live mode: a chat with the full Versa loop behind it (ambiguity
/// check with clickable readings, streamed answers, memory of past chats).
class SandboxScreen extends StatelessWidget {
  const SandboxScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final shell = context.watch<ShellState>();
    final chat = shell.sandbox;
    if (chat == null) return const SizedBox.shrink();
    return LayoutBuilder(builder: (context, c) {
      final wide = c.maxWidth >= 1000;
      return Row(
        children: [
          if (wide)
            ChatHistoryRail(
              chats: shell.sandboxHistory,
              loading: shell.sandboxHistoryLoading,
              activeSessionId: chat.sessionId,
              onSelect: shell.openSandboxChat,
              onNewChat: shell.newSandboxChat,
            ),
          Expanded(child: _ChatColumn(chat: chat, shell: shell)),
          if (wide) const _KnobsRail(),
        ],
      );
    });
  }
}

class _ChatColumn extends StatefulWidget {
  const _ChatColumn({required this.chat, required this.shell});
  final ChatController chat;
  final ShellState shell;

  @override
  State<_ChatColumn> createState() => _ChatColumnState();
}

class _ChatColumnState extends State<_ChatColumn> {
  final _scroll = ScrollController();
  int _lastCount = 0;

  /// Whether the person is reading the newest content (at, or within a few
  /// lines of, the bottom). Updated only by scrolling, never by the content
  /// growing, so a tall block arriving at once (the readings) can't make us
  /// think they scrolled away.
  bool _atBottom = true;

  @override
  void initState() {
    super.initState();
    widget.chat.addListener(_onChange);
    _scroll.addListener(_trackPosition);
  }

  void _trackPosition() {
    if (!_scroll.hasClients) return;
    final p = _scroll.position;
    _atBottom = p.maxScrollExtent - p.pixels < 80;
  }

  @override
  void didUpdateWidget(covariant _ChatColumn old) {
    super.didUpdateWidget(old);
    if (old.chat != widget.chat) {
      old.chat.removeListener(_onChange);
      widget.chat.addListener(_onChange);
      _lastCount = 0;
    }
  }

  @override
  void dispose() {
    widget.chat.removeListener(_onChange);
    _scroll.dispose();
    super.dispose();
  }

  /// Follow the conversation: always on a new message, and while the reply
  /// grows (words streaming in, the readings appearing) if the person was
  /// already at the bottom. A person who scrolled up to reread is left alone.
  void _onChange() {
    final grew = widget.chat.messages.length != _lastCount;
    _lastCount = widget.chat.messages.length;
    if (grew || _atBottom) _followBottom(rounds: 3);
  }

  /// A lazy list only knows its true height once the items are built, so
  /// jumping to the "end" can land short; a couple of extra frames converge.
  void _followBottom({required int rounds}) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      final before = _scroll.position.maxScrollExtent;
      _scroll.jumpTo(before);
      _atBottom = true;
      if (rounds > 1) _followBottom(rounds: rounds - 1);
    });
  }

  @override
  Widget build(BuildContext context) {
    final showTiming = context.watch<AppState>().showTiming;
    final chat = widget.chat;
    return ListenableBuilder(
      listenable: chat,
      builder: (context, _) {
        return Column(
          children: [
            _Header(chat: chat, shell: widget.shell),
            if (chat.status == ChatStatus.disconnected) _Disconnected(chat: chat),
            Expanded(
              child: chat.messages.isEmpty
                  ? _EmptyState(chat: chat)
                  : Center(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 860),
                        child: ListView.separated(
                          key: const ValueKey('message-list'),
                          controller: _scroll,
                          padding: const EdgeInsets.fromLTRB(28, 24, 28, 16),
                          itemCount: chat.messages.length,
                          separatorBuilder: (_, _) => const SizedBox(height: 20),
                          itemBuilder: (context, i) {
                            final m = chat.messages[i];
                            return MessageView(
                              message: m,
                              showTiming: showTiming,
                              canPickOption: chat.canSend,
                              onPickOption: (o) => chat.pickOption(m, o),
                            );
                          },
                        ),
                      ),
                    ),
            ),
            Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 860),
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(24, 4, 24, 20),
                  child: Composer(
                    enabled: chat.canSend,
                    hint: chat.canSend || chat.busy
                        ? 'Ask anything, quick answers…'
                        : 'Waiting for the connection…',
                    onSend: chat.send,
                  ),
                ),
              ),
            ),
          ],
        );
      },
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.chat, required this.shell});
  final ChatController chat;
  final ShellState shell;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      final compact = c.maxWidth < 620; // a phone: icons only, status as a dot
      return Container(
        padding: EdgeInsets.symmetric(horizontal: compact ? 8 : 20, vertical: 12),
        decoration: const BoxDecoration(
          color: Paper.surface,
          border: Border(bottom: BorderSide(color: Paper.border)),
        ),
        child: Row(
          children: [
            IconButton(
              key: const ValueKey('back-to-modes'),
              tooltip: 'All modes',
              onPressed: shell.closeSandbox,
              icon: const Icon(Icons.arrow_back_rounded, color: Paper.faint, size: 20),
            ),
            if (compact)
              IconButton(
                key: const ValueKey('history-button'),
                tooltip: 'Chat history',
                onPressed: () => showChatHistorySheet(
                  context,
                  chats: shell.sandboxHistory,
                  loading: shell.sandboxHistoryLoading,
                  activeSessionId: chat.sessionId,
                  onSelect: shell.openSandboxChat,
                  onNewChat: shell.newSandboxChat,
                ),
                icon: const Icon(Icons.history_rounded, color: Paper.faint, size: 20),
              ),
            if (!compact) ...[
              const SizedBox(width: 4),
              Container(
                width: 36,
                height: 36,
                decoration:
                    BoxDecoration(color: Paper.ink, borderRadius: BorderRadius.circular(8)),
                child: const Icon(Icons.bubble_chart_rounded, color: Paper.page, size: 20),
              ),
              const SizedBox(width: 12),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('Sandbox', style: serif(19), overflow: TextOverflow.ellipsis),
                  if (!compact) ...[
                    const SizedBox(height: 2),
                    Text('ASK ANYTHING · NO SET TOPIC', style: mono(10)),
                  ],
                ],
              ),
            ),
            _StatusPill(status: chat.status, compact: compact),
            SizedBox(width: compact ? 4 : 12),
            if (compact)
              IconButton(
                key: const ValueKey('new-chat'),
                tooltip: 'New chat',
                onPressed: shell.newSandboxChat,
                icon: const Icon(Icons.add_rounded, color: Paper.ink),
              )
            else
              OutlinedButton.icon(
                key: const ValueKey('new-chat'),
                onPressed: shell.newSandboxChat,
                icon: const Icon(Icons.add_rounded, size: 18),
                label: const Text('New chat'),
                style: OutlinedButton.styleFrom(
                  foregroundColor: Paper.ink,
                  side: const BorderSide(color: Paper.borderStrong),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
                  textStyle: sans(12.5, weight: FontWeight.w500),
                ),
              ),
          ],
        ),
      );
    });
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.status, this.compact = false});
  final ChatStatus status;

  /// Just the coloured dot (with the words as its tooltip).
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (status) {
      ChatStatus.connecting => ('Connecting…', Paper.warn),
      ChatStatus.ready => ('Connected', Paper.olive),
      ChatStatus.thinking => ('Thinking…', Paper.accent),
      ChatStatus.streaming => ('Answering…', Paper.accent),
      ChatStatus.disconnected => ('Disconnected', Paper.danger),
    };
    final dot = Container(
        width: 8, height: 8, decoration: BoxDecoration(color: color, shape: BoxShape.circle));
    if (compact) return Tooltip(key: const ValueKey('status-pill'), message: label, child: dot);
    return Row(
      key: const ValueKey('status-pill'),
      mainAxisSize: MainAxisSize.min,
      children: [
        dot,
        const SizedBox(width: 6),
        Text(label, style: sans(12, color: Paper.muted)),
      ],
    );
  }
}

class _Disconnected extends StatelessWidget {
  const _Disconnected({required this.chat});
  final ChatController chat;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 10),
      color: const Color(0xFFFBECE8),
      child: Row(
        children: [
          const Icon(Icons.wifi_off_rounded, size: 18, color: Paper.danger),
          const SizedBox(width: 10),
          Expanded(
            child: Text(chat.problem ?? 'Disconnected.',
                style: sans(13, color: Paper.danger)),
          ),
          TextButton(
            key: const ValueKey('reconnect'),
            onPressed: chat.reconnect,
            child: const Text('Reconnect'),
          ),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.chat});
  final ChatController chat;

  static const _suggestions = [
    'Explain how binary search works.',
    'can you help me with derivatives?',
    "What's the difference between a list and a tuple in Python?",
  ];

  @override
  Widget build(BuildContext context) {
    final name = context.watch<AppState>().learner?.label ?? '';
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 620),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('SANDBOX', style: mono(11)),
              const SizedBox(height: 8),
              Text("What's on your mind, $name?", style: serif(34)),
              const SizedBox(height: 10),
              Text(
                'Ask anything. If a question could mean more than one thing, '
                "I'll offer the readings to pick from instead of guessing.",
                style: sans(14.5, color: Paper.muted, height: 1.6),
              ),
              const SizedBox(height: 24),
              Text('Try one', style: sans(12, color: Paper.faint, weight: FontWeight.w600)),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final s in _suggestions)
                    ActionChip(
                      key: ValueKey('suggestion-$s'),
                      label: Text(s, style: sans(13)),
                      backgroundColor: Paper.card,
                      side: const BorderSide(color: Paper.borderStrong),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
                      onPressed: chat.canSend ? () => chat.send(s) : null,
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Session controls from the design — visible so the shape of the screen is
/// right, disabled until they are built.
class _KnobsRail extends StatelessWidget {
  const _KnobsRail();

  @override
  Widget build(BuildContext context) {
    Widget knob(String label, String detail) => Padding(
          padding: const EdgeInsets.only(bottom: 18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label, style: sans(12, weight: FontWeight.w500, color: Paper.faint)),
              const SizedBox(height: 6),
              Container(
                height: 6,
                decoration: BoxDecoration(
                    color: Paper.border, borderRadius: BorderRadius.circular(3)),
              ),
              const SizedBox(height: 4),
              Text(detail, style: mono(9.5)),
            ],
          ),
        );
    return Container(
      width: 208,
      padding: const EdgeInsets.all(20),
      decoration: const BoxDecoration(
        color: Paper.sliver,
        border: Border(left: BorderSide(color: Paper.border)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('SESSION KNOBS', style: mono(10)),
          const SizedBox(height: 10),
          const ComingSoonPill(),
          const SizedBox(height: 18),
          knob('Answer length', 'terse ··· full'),
          knob('Depth', 'gist ··· rigorous'),
          knob('Animations', 'off'),
          knob('Tone', 'socratic · direct'),
        ],
      ),
    );
  }
}
