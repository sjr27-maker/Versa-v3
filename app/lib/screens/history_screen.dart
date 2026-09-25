import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../models.dart';
import '../theme.dart';

/// Every chat this learner has, across all modes (Sandbox chats and lesson
/// chats), newest-active first, grouped by day, with a client-side search
/// over each chat's preview.
class HistoryScreen extends StatefulWidget {
  const HistoryScreen({super.key});

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  late Future<List<ChatSummary>> _future;
  final _search = TextEditingController();
  String _query = '';

  @override
  void initState() {
    super.initState();
    _future = _load();
    _search.addListener(() => setState(() => _query = _search.text.trim().toLowerCase()));
  }

  Future<List<ChatSummary>> _load() {
    final app = context.read<AppState>();
    return app.api.listAllSessions(app.learner!.id);
  }

  String _dayLabel(DateTime t) {
    final now = DateTime.now();
    final d = DateTime(t.year, t.month, t.day);
    final today = DateTime(now.year, now.month, now.day);
    final diff = today.difference(d).inDays;
    if (diff == 0) return 'Today';
    if (diff == 1) return 'Yesterday';
    return '${t.year}-${t.month.toString().padLeft(2, '0')}-${t.day.toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final shell = context.read<ShellState>();
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(40, 36, 40, 60),
      child: Align(
        alignment: Alignment.topLeft,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 800),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('HISTORY', style: mono(11)),
              const SizedBox(height: 8),
              Text('Everything you have worked through', style: serif(30)),
              const SizedBox(height: 20),
              TextField(
                key: const ValueKey('history-search'),
                controller: _search,
                decoration: InputDecoration(
                  hintText: 'Search your chats…',
                  prefixIcon: const Icon(Icons.search_rounded, size: 20),
                  filled: true,
                  fillColor: Paper.card,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: const BorderSide(color: Paper.border),
                  ),
                ),
              ),
              const SizedBox(height: 20),
              FutureBuilder<List<ChatSummary>>(
                future: _future,
                builder: (context, snap) {
                  if (snap.connectionState != ConnectionState.done) {
                    return const Padding(
                      padding: EdgeInsets.only(top: 40),
                      child: Center(child: CircularProgressIndicator()),
                    );
                  }
                  if (snap.hasError) {
                    return Text('Could not load history: ${snap.error}',
                        style: sans(13, color: Paper.danger));
                  }
                  final rows = (snap.data ?? [])
                      .where((c) => _query.isEmpty || (c.preview ?? 'new chat').toLowerCase().contains(_query))
                      .toList();
                  if (rows.isEmpty) {
                    return Padding(
                      padding: const EdgeInsets.only(top: 24),
                      child: Text(
                        _query.isEmpty ? 'No chats yet — start one from Sandbox.' : 'Nothing matches "$_query".',
                        style: sans(13.5, color: Paper.muted),
                      ),
                    );
                  }
                  final groups = <String, List<ChatSummary>>{};
                  for (final row in rows) {
                    groups.putIfAbsent(_dayLabel(row.lastActivityAt), () => []).add(row);
                  }
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final entry in groups.entries) ...[
                        Padding(
                          padding: const EdgeInsets.only(bottom: 8, top: 8),
                          child: Text(entry.key.toUpperCase(), style: mono(10)),
                        ),
                        for (final chat in entry.value)
                          _HistoryRow(
                            chat: chat,
                            onTap: switch (chat) {
                              ChatSummary(appMode: 'sandbox') => () => shell.openSandboxChat(chat),
                              ChatSummary(appMode: 'topic', :final lessonId?) => () =>
                                  shell.openLesson(lessonId),
                              _ => null,
                            },
                          ),
                        const SizedBox(height: 12),
                      ],
                    ],
                  );
                },
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _HistoryRow extends StatelessWidget {
  const _HistoryRow({required this.chat, this.onTap});
  final ChatSummary chat;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: onTap,
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        decoration: BoxDecoration(
          color: Paper.card,
          border: Border.all(color: Paper.border),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: Paper.sliver,
                borderRadius: BorderRadius.circular(100),
                border: Border.all(color: Paper.border),
              ),
              child: Text(chat.appMode.toUpperCase(), style: mono(9)),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                chat.preview ?? 'New chat',
                overflow: TextOverflow.ellipsis,
                style: sans(14, color: chat.preview == null ? Paper.faint : Paper.ink),
              ),
            ),
            const SizedBox(width: 12),
            Text('${chat.turnCount} turns', style: sans(12, color: Paper.muted)),
            if (onTap != null) ...[
              const SizedBox(width: 8),
              const Icon(Icons.arrow_forward_rounded, size: 16, color: Paper.faint),
            ],
          ],
        ),
      ),
    );
  }
}
