import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import 'branch_tree.dart';
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

  /// The branch whose new children are growing in right now.
  String? _growing;
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
      setState(() {
        if (_open.remove(node.id)) return;
        _open.add(node.id);
        _growing = node.id; // showing again grows them back in
      });
      return;
    }
    setState(() => _branching.add(node.id));
    try {
      final children = await _api.expand(node.id, more: more);
      if (!mounted) return;
      setState(() {
        final known = {for (final c in node.children) c.id};
        final fresh = children.where((c) => !known.contains(c.id)).toList();
        node.children.addAll(fresh);
        node.expanded = true;
        // a resource section that has run out: its extras came, or nothing new did
        if (node.children.any((c) => c.beyondResource) || (more && fresh.isEmpty)) node.canBranch = false;
        _open.add(node.id);
        _growing = node.id;
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
    final header = Padding(
      padding: const EdgeInsets.fromLTRB(32, 32, 32, 12),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 900),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            PageHeading(
              eyebrow: switch (e?.sourceKind) {
                'pdf' => 'FROM YOUR PDF',
                'link' => 'FROM A WEB PAGE',
                'image' => 'FROM YOUR PICTURE',
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
                'Tap a branch to tick it into your course. Branch any of them further and watch it grow; '
                'branches can keep branching.',
                style: sans(13.5, color: Paper.muted, height: 1.5),
              ),
              const SizedBox(height: 8),
              ShapedByNote(sources: e!.personalizedBy),
              if (e.rootNodes.isEmpty) ...[
                const SizedBox(height: 18),
                Text('Nothing came back for this. Try other words.', style: sans(13.5, color: Paper.muted)),
              ],
            ],
          ],
        ),
      ),
    );
    final showTree = !_loading && _error == null && e != null && e.rootNodes.isNotEmpty;
    return LayoutBuilder(builder: (context, c) {
      // a phone: the tree runs down one column that fits the screen, instead
      // of spreading several screens wide (topic/branch_tree.dart)
      final narrow = c.maxWidth < 700;
      return SingleChildScrollView(
        key: const ValueKey('branch-tree-scroll'),
        padding: const EdgeInsets.only(bottom: 40),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Align(alignment: Alignment.topLeft, child: header),
            if (showTree)
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 12),
                child: ConstrainedBox(
                  // a narrow tree sits in the middle of the screen
                  constraints: BoxConstraints(minWidth: math.max(0, c.maxWidth - 24)),
                  child: Center(
                    child: BranchTree(
                      title: e.suggestedTitle.isEmpty ? widget.label : e.suggestedTitle,
                      roots: e.rootNodes,
                      open: _open,
                      selected: _selected,
                      branching: _branching,
                      growing: _growing,
                      narrowWidth: narrow ? c.maxWidth - 24 : null,
                      onToggle: _toggle,
                      onBranch: (n) => _branch(n),
                      onMore: (n) => _branch(n, more: true),
                    ),
                  ),
                ),
              ),
          ],
        ),
      );
    });
  }

  Widget _bottomBar() {
    final n = _selected.length;
    return Container(
      padding: const EdgeInsets.fromLTRB(24, 14, 24, 16),
      decoration: BoxDecoration(
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

class _Mapping extends StatelessWidget {
  const _Mapping({required this.label});
  final String label;

  @override
  Widget build(BuildContext context) {
    return Padding(
      key: const ValueKey('explorer-loading'),
      padding: const EdgeInsets.symmetric(vertical: 40),
      child: Row(children: [
        SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2.4, color: Paper.accent)),
        const SizedBox(width: 14),
        Expanded(
          child: Text('Mapping "$label" into branches…', style: sans(14, color: Paper.muted)),
        ),
      ]),
    );
  }
}
