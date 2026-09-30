import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api.dart';
import '../app_state.dart';
import '../models.dart';
import '../theme.dart';
import 'pattern_sky_screen.dart';

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
  late Future<StyleReport> _patterns;
  bool _showArchived = false;

  @override
  void initState() {
    super.initState();
    _future = _load();
    final app = context.read<AppState>();
    _patterns = app.api.getStylePatterns(app.learner!.id);
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
              _ExploreSection(patterns: _patterns),
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

/// "How you explore" -- the patterns in your own choices (the cards you take
/// after an answer, the depth and breadth you set), each with the checks it
/// has to pass before Versa calls it your style. Tap one for the checks.
class _ExploreSection extends StatelessWidget {
  const _ExploreSection({required this.patterns});
  final Future<StyleReport> patterns;

  @override
  Widget build(BuildContext context) {
    return FutureBuilder<StyleReport>(
      future: patterns,
      builder: (context, snap) {
        if (snap.connectionState != ConnectionState.done || snap.hasError) return const SizedBox.shrink();
        final found = snap.data?.patterns ?? const <StylePattern>[];
        final misses = snap.data?.misses ?? const MissFollowThrough();
        final moves = snap.data?.newMoves ?? const <NewMove>[];
        return _Section(
          title: 'How you explore',
          empty: 'Nothing clear yet. This comes from the "where this could go" steps you take and the '
              'depth you set, across different topics \u2014 never from a single chat.',
          // one card per fact; patterns pointing the same way are its facets
          children: [
            for (final p in found)
              if (p.facetOf == null) _PatternCard(pattern: p, facets: [for (final f in found) if (f.facetOf == p.id) f]),
            if (misses.read > 0) _MissNote(misses: misses),
            if (moves.isNotEmpty) _NewMoves(moves: moves),
          ],
        );
      },
    );
  }
}

/// What they asked for that no card offers -- their own ways of thinking the
/// cards don't have yet.
class _NewMoves extends StatelessWidget {
  const _NewMoves({required this.moves});
  final List<NewMove> moves;

  @override
  Widget build(BuildContext context) {
    return Padding(
      key: const ValueKey('new-moves'),
      padding: const EdgeInsets.only(top: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('What you asked for that the cards don\'t offer yet', style: serif(16)),
          const SizedBox(height: 6),
          for (final m in moves.take(5))
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(
                '\u2022 \u201c${m.label}\u201d \u2014 ${m.times} ${m.times == 1 ? 'time' : 'times'}'
                '${m.chats > 1 ? ' in ${m.chats} chats' : ''}',
                style: sans(13, color: Paper.body, height: 1.4),
              ),
            ),
        ],
      ),
    );
  }
}

/// When the cards missed: what Versa did about it (experimenting on a miss).
class _MissNote extends StatelessWidget {
  const _MissNote({required this.misses});
  final MissFollowThrough misses;

  @override
  Widget build(BuildContext context) {
    final m = misses;
    final times = m.read == 1 ? 'once' : '${m.read} times';
    final after = m.offeredLater == 0
        ? 'Versa keeps dealing new hands until one of them offers it.'
        : 'When a later hand offered it, you took it ${m.taken} of ${m.offeredLater} '
            '${m.offeredLater == 1 ? 'time' : 'times'}'
            '${m.held > 0 ? ', and ${m.held} of those held in a later chat' : ''}.';
    return Padding(
      key: const ValueKey('miss-note'),
      padding: const EdgeInsets.only(top: 10),
      child: Text(
        'When none of the cards matched, the question you asked instead pointed to a way out '
        '$times. $after',
        style: sans(13, color: Paper.body, height: 1.45),
      ),
    );
  }
}

class _PatternCard extends StatefulWidget {
  const _PatternCard({required this.pattern, this.facets = const []});
  final StylePattern pattern;

  /// Weaker patterns pointing the same way: this fact seen from other angles.
  final List<StylePattern> facets;

  @override
  State<_PatternCard> createState() => _PatternCardState();
}

class _PatternCardState extends State<_PatternCard> {

  @override
  Widget build(BuildContext context) {
    final p = widget.pattern;
    final (label, color) = switch (p.status) {
      'confirmed' => ('Confirmed', Paper.olive),
      'fading' => ('Fading', Paper.warn),
      _ => ('Emerging \u00b7 ${p.gatesPassed} of ${p.gates.length} checks', Paper.muted),
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 10),
      child: InkWell(
        key: ValueKey('pattern-${p.kind}-${p.key}'),
        borderRadius: BorderRadius.circular(10),
        onTap: () => Navigator.of(context)
            .push(MaterialPageRoute<void>(builder: (_) => PatternSkyScreen(pattern: p, facets: widget.facets))),
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.all(14),
          decoration: BoxDecoration(
            color: Paper.card,
            border: Border.all(color: Paper.border),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(label.toUpperCase(), style: mono(10.5).copyWith(color: color)),
              const SizedBox(height: 6),
              Text(p.statement, style: sans(14.5, height: 1.5)),
              if (widget.facets.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  'Also seen as ${widget.facets.length} other '
                  '${widget.facets.length == 1 ? 'pattern' : 'patterns'} pointing the same way',
                  key: ValueKey('facets-${p.kind}-${p.key}'),
                  style: sans(12, color: Paper.muted),
                ),
              ],
              const SizedBox(height: 6),
              Row(children: [
                Icon(Icons.auto_awesome_outlined, size: 13, color: Paper.faint),
                const SizedBox(width: 5),
                Text('See it drawn from your picks', style: sans(12, color: Paper.faint)),
              ]),
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
  const _ItemCard({required this.kind, required this.item, required this.onChanged});
  final String kind; // "claim" | "thinking_style"
  final ThinkingStyleItem item;
  final VoidCallback onChanged;

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
                      icon: Icon(Icons.archive_outlined, size: 16, color: Paper.danger),
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
