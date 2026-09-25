import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import 'topic_api.dart';
import 'topic_models.dart';
import 'topic_screen.dart';
import 'topic_widgets.dart';
import 'topics_root.dart';

/// The branching map of a topic: every branch can be ticked into the course
/// and opened to branch further. Nothing is chosen for the person.
class ExplorerScreen extends StatefulWidget {
  const ExplorerScreen({super.key, required this.label, required this.load});

  /// What is being mapped (the search words, a file name or a link).
  final String label;
  final Future<Exploration> Function() load;

  @override
  State<ExplorerScreen> createState() => _ExplorerScreenState();
}

class _ExplorerScreenState extends State<ExplorerScreen> {
  Exploration? _exploration;
  Object? _error;
  bool _loading = true;

  /// Ticked node ids, in the order they were ticked.
  final List<String> _selected = [];

  /// Nodes whose children are showing.
  final Set<String> _open = {};

  /// Nodes waiting on the server for (more) children.
  final Set<String> _branching = {};
  final _title = TextEditingController();
  bool _building = false;

  TopicApi get _api => TopicApi.of(context.read<AppState>().api);

  @override
  void initState() {
    super.initState();
    _start();
  }

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  Future<void> _start() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final e = await widget.load();
      if (!mounted) return;
      _title.text = e.suggestedTitle;
      setState(() {
        _exploration = e;
        _loading = false;
        // Branches that arrived already expanded (a document's own sections)
        // start open, so its structure is visible straight away.
        void openExpanded(List<TopicNode> nodes) {
          for (final n in nodes) {
            if (n.expanded && n.children.isNotEmpty) _open.add(n.id);
            openExpanded(n.children);
          }
        }

        openExpanded(e.rootNodes);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e;
        _loading = false;
      });
    }
  }

  Future<void> _branch(TopicNode node, {bool more = false}) async {
    if (!more && node.expanded) {
      setState(() => _open.contains(node.id) ? _open.remove(node.id) : _open.add(node.id));
      return;
    }
    setState(() => _branching.add(node.id));
    try {
      final children = await _api.expand(node.id, more: more);
      if (!mounted) return;
      setState(() {
        final known = {for (final c in node.children) c.id};
        node.children.addAll(children.where((c) => !known.contains(c.id)));
        node.expanded = true;
        _open.add(node.id);
      });
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text('Could not branch "${node.title}" further ($e)')));
      }
    } finally {
      if (mounted) setState(() => _branching.remove(node.id));
    }
  }

  void _toggle(TopicNode node) {
    setState(() => _selected.contains(node.id) ? _selected.remove(node.id) : _selected.add(node.id));
  }

  Future<void> _build() async {
    final e = _exploration;
    if (e == null || _selected.isEmpty || _building) return;
    setState(() => _building = true);
    try {
      final topic = await _api.createTopic(
        learnerId: context.read<AppState>().learner!.id,
        explorationId: e.id,
        selectedNodeIds: List.of(_selected),
        title: _title.text,
      );
      if (!mounted) return;
      await Navigator.of(context).pushReplacement(
        topicRoute((_) => TopicScreen(topicId: topic.id, initial: topic)),
      );
    } catch (err) {
      if (!mounted) return;
      setState(() => _building = false);
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text('Could not build the course ($err)')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: Paper.surface,
      child: Column(
        children: [
          Expanded(child: _body()),
          if (_exploration != null) _bottomBar(),
        ],
      ),
    );
  }

  Widget _body() {
    final e = _exploration;
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(32, 32, 32, 40),
      child: Align(
        alignment: Alignment.topLeft,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 900),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              PageHeading(
                eyebrow: switch (e?.sourceKind) {
                  'pdf' => 'FROM YOUR PDF',
                  'link' => 'FROM A WEB PAGE',
                  _ => 'EXPLORE A TOPIC',
                },
                title: e?.suggestedTitle ?? widget.label,
                onBack: () => Navigator.of(context).maybePop(),
                backKey: const ValueKey('explorer-back'),
              ),
              const SizedBox(height: 10),
              if (_loading)
                _Mapping(label: widget.label)
              else if (_error != null)
                RetryLine(message: 'Could not map this: $_error', onRetry: _start)
              else ...[
                Text(
                  'Tick what you want in your course. Open any branch to see what\'s inside it; '
                  'branches can keep branching.',
                  style: sans(13.5, color: Paper.muted, height: 1.5),
                ),
                const SizedBox(height: 8),
                ShapedByNote(sources: e!.personalizedBy),
                const SizedBox(height: 18),
                if (e.rootNodes.isEmpty)
                  Text('Nothing came back for this. Try other words.', style: sans(13.5, color: Paper.muted))
                else
                  for (var i = 0; i < e.rootNodes.length; i++)
                    _Appear(index: i, child: _nodeTree(e.rootNodes[i])),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _nodeTree(TopicNode node) {
    final open = _open.contains(node.id);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _NodeRow(
          node: node,
          selected: _selected.contains(node.id),
          open: open,
          branching: _branching.contains(node.id),
          onToggle: () => _toggle(node),
          onBranch: () => _branch(node),
        ),
        AnimatedSize(
          duration: const Duration(milliseconds: 280),
          curve: Curves.easeOutCubic,
          alignment: Alignment.topLeft,
          child: !open
              ? const SizedBox(width: double.infinity)
              : Container(
                  margin: const EdgeInsets.only(left: 21, bottom: 6),
                  padding: const EdgeInsets.only(left: 16),
                  decoration: const BoxDecoration(
                    border: Border(left: BorderSide(color: Paper.accentLine, width: 2)),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (var i = 0; i < node.children.length; i++)
                        _Appear(key: ValueKey('appear-${node.children[i].id}'), index: i, child: _nodeTree(node.children[i])),
                      if (node.children.isEmpty)
                        Padding(
                          padding: const EdgeInsets.symmetric(vertical: 8),
                          child: Text('This branch doesn\'t split further.', style: sans(12.5, color: Paper.faint)),
                        ),
                      TextButton.icon(
                        key: ValueKey('more-${node.id}'),
                        onPressed: _branching.contains(node.id) ? null : () => _branch(node, more: true),
                        icon: const Icon(Icons.add_rounded, size: 16),
                        label: const Text('More branches'),
                        style: TextButton.styleFrom(foregroundColor: Paper.muted, textStyle: sans(12.5)),
                      ),
                    ],
                  ),
                ),
        ),
      ],
    );
  }

  Widget _bottomBar() {
    final n = _selected.length;
    return Container(
      padding: const EdgeInsets.fromLTRB(24, 14, 24, 16),
      decoration: const BoxDecoration(
        color: Paper.card,
        border: Border(top: BorderSide(color: Paper.border)),
        boxShadow: [BoxShadow(color: Color(0x14000000), blurRadius: 16, offset: Offset(0, -4))],
      ),
      child: LayoutBuilder(builder: (context, c) {
        final narrow = c.maxWidth < 620;
        final count = AnimatedSwitcher(
          duration: const Duration(milliseconds: 200),
          child: Text(
            n == 0 ? 'Nothing ticked yet' : '$n selected',
            key: ValueKey('selected-count-$n'),
            style: sans(13.5, weight: FontWeight.w600, color: n == 0 ? Paper.faint : Paper.ink),
          ),
        );
        final title = TextField(
          key: const ValueKey('course-title-field'),
          controller: _title,
          decoration: InputDecoration(
            isDense: true,
            labelText: 'Course title',
            border: OutlineInputBorder(borderRadius: BorderRadius.circular(10)),
          ),
        );
        final button = FilledButton.icon(
          key: const ValueKey('build-course'),
          onPressed: n == 0 || _building ? null : _build,
          icon: _building
              ? const SizedBox(
                  width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : const Icon(Icons.auto_stories_outlined, size: 18),
          label: Text(_building ? 'Building…' : 'Build my course'),
          style: FilledButton.styleFrom(
            backgroundColor: Paper.accent,
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
          ),
        );
        final hint = Text(
          'Ticked branches become chapters; ticked branches inside a chapter become its lessons.',
          style: sans(11.5, color: Paper.faint),
        );
        if (narrow) {
          return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            count,
            const SizedBox(height: 8),
            title,
            const SizedBox(height: 8),
            button,
          ]);
        }
        return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            SizedBox(width: 150, child: count),
            Expanded(child: title),
            const SizedBox(width: 12),
            button,
          ]),
          const SizedBox(height: 6),
          hint,
        ]);
      }),
    );
  }
}

class _NodeRow extends StatelessWidget {
  const _NodeRow({
    required this.node,
    required this.selected,
    required this.open,
    required this.branching,
    required this.onToggle,
    required this.onBranch,
  });

  final TopicNode node;
  final bool selected;
  final bool open;
  final bool branching;
  final VoidCallback onToggle;
  final VoidCallback onBranch;

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 180),
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.fromLTRB(4, 8, 8, 8),
      decoration: BoxDecoration(
        color: selected ? Paper.accentSoft : Paper.card,
        border: Border.all(color: selected ? Paper.accent : Paper.border, width: selected ? 1.5 : 1),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Checkbox(
            key: ValueKey('select-${node.id}'),
            value: selected,
            activeColor: Paper.accent,
            onChanged: (_) => onToggle(),
          ),
          Expanded(
            child: InkWell(
              onTap: onToggle,
              child: Padding(
                padding: const EdgeInsets.only(top: 10, right: 8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(node.title, style: sans(14.5, weight: FontWeight.w600)),
                    if (node.summary.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(node.summary, style: sans(12.5, color: Paper.muted, height: 1.45)),
                    ],
                  ],
                ),
              ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: branching
                ? const Padding(
                    padding: EdgeInsets.all(10),
                    child: SizedBox(
                        width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Paper.accent)),
                  )
                : TextButton.icon(
                    key: ValueKey('branch-${node.id}'),
                    onPressed: onBranch,
                    icon: AnimatedRotation(
                      turns: open ? 0.5 : 0,
                      duration: const Duration(milliseconds: 220),
                      child: const Icon(Icons.expand_more_rounded, size: 18),
                    ),
                    label: Text(open ? 'Hide' : (node.expanded ? 'Show branches' : 'Branch further')),
                    style: TextButton.styleFrom(foregroundColor: Paper.accent, textStyle: sans(12.5)),
                  ),
          ),
        ],
      ),
    );
  }
}

/// A child fading and rising into place, a little after the one above it.
class _Appear extends StatelessWidget {
  const _Appear({super.key, required this.index, required this.child});
  final int index;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final ms = 240 + 60 * index.clamp(0, 8);
    return TweenAnimationBuilder<double>(
      tween: Tween(begin: 0, end: 1),
      duration: Duration(milliseconds: ms),
      curve: Interval((60 * index.clamp(0, 8)) / ms, 1, curve: Curves.easeOutCubic),
      builder: (context, v, child) => Opacity(
        opacity: v,
        child: Transform.translate(offset: Offset(0, (1 - v) * 10), child: child),
      ),
      child: child,
    );
  }
}

class _Mapping extends StatelessWidget {
  const _Mapping({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      key: const ValueKey('explorer-loading'),
      padding: const EdgeInsets.symmetric(vertical: 40),
      child: Row(children: [
        const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.4, color: Paper.accent)),
        const SizedBox(width: 14),
        Expanded(
          child: Text('Mapping "$label" into branches…', style: sans(14, color: Paper.muted)),
        ),
      ]),
    );
  }
}
