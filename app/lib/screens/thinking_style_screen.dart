import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api.dart';
import '../app_state.dart';
import '../models.dart';
import '../theme.dart';

/// What Versa has actually stored about this learner: confirmed patterns,
/// ones still forming, retired ones, and observed preferences (claims) --
/// every status, since the student can approve, edit or archive any of
/// them (server: GET /api/learners/{id}/thinking-style).
class ThinkingStyleScreen extends StatefulWidget {
  const ThinkingStyleScreen({super.key});

  @override
  State<ThinkingStyleScreen> createState() => _ThinkingStyleScreenState();
}

class _ThinkingStyleScreenState extends State<ThinkingStyleScreen> {
  late Future<ThinkingStyleOverview> _future;
  bool _showArchived = false;

  @override
  void initState() {
    super.initState();
    _future = _load();
  }

  Future<ThinkingStyleOverview> _load() {
    final app = context.read<AppState>();
    return app.api.getThinkingStyle(app.learner!.id, includeArchived: _showArchived);
  }

  void _refresh() => setState(() => _future = _load());

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(40, 36, 40, 60),
      child: Align(
        alignment: Alignment.topLeft,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 820),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                Expanded(child: Text('THINKING STYLE', style: mono(11))),
                TextButton.icon(
                  key: const ValueKey('show-archived-toggle'),
                  onPressed: () => setState(() {
                    _showArchived = !_showArchived;
                    _refresh();
                  }),
                  icon: Icon(_showArchived ? Icons.visibility_off_rounded : Icons.archive_outlined, size: 16),
                  label: Text(_showArchived ? 'Hide archived' : 'Show archived'),
                ),
              ]),
              const SizedBox(height: 4),
              Text('What I have noticed about how you think', style: serif(28)),
              const SizedBox(height: 20),
              FutureBuilder<ThinkingStyleOverview>(
                future: _future,
                builder: (context, snap) {
                  if (snap.connectionState != ConnectionState.done) {
                    return const Padding(
                      padding: EdgeInsets.only(top: 40),
                      child: Center(child: CircularProgressIndicator()),
                    );
                  }
                  if (snap.hasError) {
                    return Text('Could not load this: ${snap.error}', style: sans(13, color: Paper.danger));
                  }
                  final data = snap.data!;
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      _Section(
                        title: 'Confirmed patterns',
                        empty: 'Nothing confirmed yet — it takes ${data.promotionThreshold} '
                            'independent sessions agreeing before Versa will name a pattern.',
                        children: [
                          for (final item in data.confirmed)
                            _ItemCard(kind: 'thinking_style', item: item, onChanged: _refresh),
                        ],
                      ),
                      _Section(
                        title: 'Emerging',
                        empty: 'Nothing forming yet.',
                        children: [
                          for (final item in data.emerging)
                            _ItemCard(
                              kind: 'thinking_style', item: item, onChanged: _refresh,
                              progressOf: data.promotionThreshold,
                            ),
                        ],
                      ),
                      if (data.retired.isNotEmpty)
                        _Section(
                          title: 'Retired',
                          empty: '',
                          children: [
                            for (final item in data.retired)
                              _ItemCard(kind: 'thinking_style', item: item, onChanged: _refresh),
                          ],
                        ),
                      _Section(
                        title: 'Preferences we\'ve noticed',
                        empty: 'Nothing observed yet — this fills in from surprising moments in '
                            'your chats, not from a survey.',
                        children: [
                          for (final item in data.claims)
                            _ItemCard(kind: 'claim', item: item, onChanged: _refresh),
                        ],
                      ),
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

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.empty, required this.children});
  final String title;
  final String empty;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 28),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: serif(19)),
          const SizedBox(height: 10),
          if (children.isEmpty)
            Text(empty, style: sans(13, color: Paper.muted, height: 1.5))
          else
            ...children,
        ],
      ),
    );
  }
}

class _ItemCard extends StatelessWidget {
  const _ItemCard({required this.kind, required this.item, required this.onChanged, this.progressOf});
  final String kind; // "claim" | "thinking_style"
  final ThinkingStyleItem item;
  final VoidCallback onChanged;
  final int? progressOf;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      borderRadius: BorderRadius.circular(12),
      onTap: () => showItemDetail(context, kind: kind, id: item.id, onChanged: onChanged),
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: item.archived ? Paper.sliver : Paper.card,
          border: Border.all(color: Paper.border),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(item.summary,
                      style: sans(14, color: item.archived ? Paper.faint : Paper.ink, height: 1.4)),
                ),
                if (item.edited) ...[
                  const SizedBox(width: 8),
                  Text('EDITED', style: mono(9, color: Paper.accent)),
                ],
                if (item.archived) ...[
                  const SizedBox(width: 8),
                  Text('ARCHIVED', style: mono(9)),
                ],
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Text(
                  kind == 'claim'
                      ? '${item.evidenceCount ?? 0} episode(s) · ${item.sessions} session(s)'
                      : '${item.confirmations} session(s) confirmed',
                  style: sans(11.5, color: Paper.muted),
                ),
              ],
            ),
            if (progressOf != null) ...[
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: LinearProgressIndicator(
                  value: item.confirmations / progressOf!,
                  minHeight: 5,
                  backgroundColor: Paper.border,
                  color: Paper.accent,
                ),
              ),
              const SizedBox(height: 2),
              Text('${item.confirmations} of $progressOf sessions', style: sans(10.5, color: Paper.faint)),
            ],
          ],
        ),
      ),
    );
  }
}

// -------------------------------------------------------------- detail sheet

Future<void> showItemDetail(
  BuildContext context, {
  required String kind,
  required String id,
  required VoidCallback onChanged,
}) {
  return showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: Paper.page,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (context) => _ItemDetailSheet(kind: kind, id: id, onChanged: onChanged),
  );
}

class _ItemDetailSheet extends StatefulWidget {
  const _ItemDetailSheet({required this.kind, required this.id, required this.onChanged});
  final String kind;
  final String id;
  final VoidCallback onChanged;

  @override
  State<_ItemDetailSheet> createState() => _ItemDetailSheetState();
}

class _ItemDetailSheetState extends State<_ItemDetailSheet> {
  ClaimDetail? _claim;
  ThinkingStyleDetail? _style;
  String? _why;
  List<QnAEntry> _qna = [];
  bool _loading = true;
  bool _editing = false;
  final _editController = TextEditingController();
  final _askController = TextEditingController();
  bool _asking = false;
  String? _error;

  VersaApi get _api => context.read<AppState>().api;
  bool get _isClaim => widget.kind == 'claim';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      if (_isClaim) {
        _claim = await _api.getClaim(widget.id);
        _qna = await _api.listClaimQna(widget.id);
      } else {
        _style = await _api.getThinkingStyleCandidate(widget.id);
        _qna = await _api.listThinkingStyleQna(widget.id);
      }
    } catch (e) {
      _error = '$e';
    }
    if (mounted) setState(() => _loading = false);
    unawaited(_loadWhy());
  }

  Future<void> _loadWhy() async {
    try {
      final why = _isClaim ? await _api.whyClaim(widget.id) : await _api.whyThinkingStyle(widget.id);
      if (mounted) setState(() => _why = why);
    } catch (_) {
      // the "why" paragraph is a nicety; the rest of the sheet still works
    }
  }

  Future<void> _act(String action, {String? revisedStatement}) async {
    try {
      if (_isClaim) {
        _claim = await _api.reviewClaim(widget.id, action, revisedStatement: revisedStatement);
      } else {
        _style = await _api.reviewThinkingStyle(widget.id, action, revisedStatement: revisedStatement);
      }
      widget.onChanged();
      if (mounted) setState(() => _editing = false);
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<void> _undo(String reviewId) async {
    try {
      if (_isClaim) {
        _claim = await _api.undoClaimReview(widget.id, reviewId);
      } else {
        _style = await _api.undoThinkingStyleReview(widget.id, reviewId);
      }
      widget.onChanged();
      if (mounted) setState(() {});
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  Future<void> _ask() async {
    final q = _askController.text.trim();
    if (q.isEmpty || _asking) return;
    setState(() => _asking = true);
    _askController.clear();
    try {
      final entry = _isClaim ? await _api.askClaim(widget.id, q) : await _api.askThinkingStyle(widget.id, q);
      if (mounted) setState(() => _qna = [..._qna, entry]);
      await _load(); // a chat-applied change (intent != none) needs the fresh statement/history
      widget.onChanged();
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _asking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final statement = _isClaim ? _claim?.statement : _style?.summary;
    final archived = _isClaim ? _claim?.archived : _style?.archived;
    final reviews = _isClaim ? _claim?.reviews : _style?.reviews;
    return DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.75,
      maxChildSize: 0.95,
      builder: (context, scrollController) => Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
        child: ListView(
          controller: scrollController,
          padding: const EdgeInsets.fromLTRB(24, 20, 24, 32),
          children: [
            if (_loading)
              const Center(child: Padding(padding: EdgeInsets.all(24), child: CircularProgressIndicator()))
            else if (statement == null)
              Text(_error ?? 'Not found', style: sans(13, color: Paper.danger))
            else ...[
              Text(_isClaim ? 'PREFERENCE' : 'THINKING STYLE', style: mono(10)),
              const SizedBox(height: 6),
              if (_editing)
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    TextField(
                      controller: _editController..text = statement,
                      maxLines: 3,
                      autofocus: true,
                      decoration: const InputDecoration(border: OutlineInputBorder()),
                    ),
                    const SizedBox(height: 8),
                    Row(children: [
                      FilledButton(
                        onPressed: () => _act('edit', revisedStatement: _editController.text.trim()),
                        child: const Text('Save'),
                      ),
                      const SizedBox(width: 8),
                      TextButton(
                        onPressed: () => setState(() => _editing = false),
                        child: const Text('Cancel'),
                      ),
                    ]),
                  ],
                )
              else
                Text(statement, style: serif(20)),
              const SizedBox(height: 16),
              if (_why != null) ...[
                Container(
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: Paper.accentSoft,
                    borderRadius: BorderRadius.circular(10),
                    border: Border.all(color: Paper.accentLine),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('WHY VERSA THINKS THIS', style: mono(9.5, color: Paper.accentDark)),
                      const SizedBox(height: 6),
                      Text(_why!, style: sans(13, color: Paper.ink, height: 1.5)),
                    ],
                  ),
                ),
                const SizedBox(height: 16),
              ],
              if (!_editing)
                Wrap(spacing: 8, runSpacing: 8, children: [
                  OutlinedButton.icon(
                    onPressed: () => _act('approve'),
                    icon: const Icon(Icons.check_rounded, size: 16),
                    label: const Text('Approve'),
                  ),
                  OutlinedButton.icon(
                    onPressed: () => setState(() => _editing = true),
                    icon: const Icon(Icons.edit_outlined, size: 16),
                    label: const Text('Edit'),
                  ),
                  if (archived == true)
                    OutlinedButton.icon(
                      onPressed: () => _act('restore'),
                      icon: const Icon(Icons.unarchive_outlined, size: 16),
                      label: const Text('Restore'),
                    )
                  else
                    OutlinedButton.icon(
                      onPressed: () => _act('archive'),
                      icon: const Icon(Icons.archive_outlined, size: 16, color: Paper.danger),
                      label: Text('Delete', style: sans(13, color: Paper.danger)),
                    ),
                ]),
              if (_error != null) ...[
                const SizedBox(height: 8),
                Text(_error!, style: sans(12, color: Paper.danger)),
              ],
              const SizedBox(height: 20),
              Text(_isClaim ? 'EVIDENCE' : 'SESSIONS BEHIND THIS', style: mono(10)),
              const SizedBox(height: 8),
              for (final line in (_isClaim ? _claim!.evidenceLines : _style!.sessionLines))
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Text(line, style: sans(12, color: Paper.body)),
                ),
              if (_isClaim) ...[
                const SizedBox(height: 6),
                Text('Test: ${_claim!.test}', style: sans(12, color: Paper.muted, height: 1.4)),
              ],
              const SizedBox(height: 20),
              Text('HISTORY', style: mono(10)),
              const SizedBox(height: 8),
              for (final r in reviews ?? <ReviewEntry>[])
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Row(children: [
                    Expanded(
                      child: Text(
                        r.revisedStatement != null
                            ? '${r.reviewType} → "${r.revisedStatement}"'
                            : r.reviewType,
                        style: sans(12, color: Paper.body),
                      ),
                    ),
                    Text('(${r.source})', style: sans(10.5, color: Paper.faint)),
                    TextButton(onPressed: () => _undo(r.id), child: const Text('Undo')),
                  ]),
                ),
              const SizedBox(height: 12),
              Text('ASK ABOUT THIS', style: mono(10)),
              const SizedBox(height: 8),
              for (final t in _qna)
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(t.question, style: sans(13, weight: FontWeight.w600)),
                      const SizedBox(height: 2),
                      Text(t.answer, style: sans(13, color: Paper.body, height: 1.4)),
                    ],
                  ),
                ),
              Row(children: [
                Expanded(
                  child: TextField(
                    controller: _askController,
                    decoration: const InputDecoration(hintText: 'Ask a question…'),
                    onSubmitted: (_) => _ask(),
                  ),
                ),
                IconButton(
                  onPressed: _asking ? null : _ask,
                  icon: _asking
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.send_rounded),
                ),
              ]),
            ],
          ],
        ),
      ),
    );
  }
}
