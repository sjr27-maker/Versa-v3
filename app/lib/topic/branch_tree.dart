import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';
import 'topic_models.dart';

/// The branching map of a topic, drawn as a tree growing DOWN the screen:
/// the topic at the top, its branches hanging below it, and every branch the
/// learner opens growing its own children beneath it -- the new branches
/// glow as they grow, a light runs down from the top along every branch,
/// and each child pops in at the tip of its branch. Ticked branches (what
/// goes into the course) are lit all the way back up to the top.
///
/// Laid out as a tidy tree: a branch sits centred over its children; wide
/// trees scroll sideways (the caller wraps this in scroll views), and the
/// view follows whatever just grew.
class BranchTree extends StatefulWidget {
  const BranchTree({
    super.key,
    required this.title,
    required this.roots,
    required this.open,
    required this.selected,
    required this.branching,
    required this.onToggle,
    required this.onBranch,
    required this.onMore,
    this.growing,
  });

  /// The topic itself: the top of the tree.
  final String title;
  final List<TopicNode> roots;

  /// Branches whose children are showing.
  final Set<String> open;
  final List<String> selected;

  /// Branches waiting on the server for children.
  final Set<String> branching;

  /// The branch that just opened (its new branches grow in), or null.
  final String? growing;
  final void Function(TopicNode) onToggle;
  final void Function(TopicNode) onBranch;
  final void Function(TopicNode) onMore;

  static const double cardWidth = 184;
  static const double cardHeight = 138;
  static const double gapX = 18;
  static const double levelGap = 64;
  static const double seedHeight = 56;

  @override
  State<BranchTree> createState() => _BranchTreeState();
}

/// One place in the laid-out tree: a branch, the "more branches" pill under
/// an open branch, or the note that a branch doesn't split further.
class _Slot {
  _Slot({required this.id, required this.rect, this.node, this.parent, this.depth = 0, this.index = 0,
      this.kind = _SlotKind.node});
  final String id;
  final Rect rect;
  final TopicNode? node;
  final String? parent;
  final int depth;
  final int index; // among its parent's children
  final _SlotKind kind;
}

enum _SlotKind { seed, node, more, leaf }

class _Layout {
  _Layout(this.slots, this.size);
  final Map<String, _Slot> slots;
  final Size size;

  static const seedId = '__seed__';

  static _Layout of(BranchTree t) {
    const w = BranchTree.cardWidth, h = BranchTree.cardHeight, gap = BranchTree.gapX;
    const level = h + BranchTree.levelGap;
    final slots = <String, _Slot>{};
    var maxY = 0.0;

    // items under a branch, in order: its children, then the pill / note
    List<(String, TopicNode?, _SlotKind)> itemsUnder(TopicNode n) => [
          for (final c in n.children) (c.id, c, _SlotKind.node),
          if (n.children.isEmpty) ('leaf-${n.id}', null, _SlotKind.leaf),
          ('more-${n.id}', n, _SlotKind.more),
        ];

    // returns the width used and the centre x of the item
    (double, double) place(String id, TopicNode? node, _SlotKind kind, String parent, int depth, int index,
        double x0) {
      final top = BranchTree.seedHeight + BranchTree.levelGap + (depth - 1) * level;
      final items = kind == _SlotKind.node && node != null && t.open.contains(node.id)
          ? itemsUnder(node)
          : const <(String, TopicNode?, _SlotKind)>[];
      double width, cx;
      if (items.isEmpty) {
        width = w + gap;
        cx = x0 + width / 2;
      } else {
        var x = x0;
        final centres = <double>[];
        for (var i = 0; i < items.length; i++) {
          final (cid, cnode, ckind) = items[i];
          final (cw, ccx) = place(cid, cnode, ckind, id, depth + 1, i, x);
          centres.add(ccx);
          x += cw;
        }
        width = math.max(x - x0, w + gap);
        cx = (centres.first + centres.last) / 2;
      }
      final small = kind == _SlotKind.more || kind == _SlotKind.leaf;
      final rect = small
          ? Rect.fromCenter(center: Offset(cx, top + 28), width: w - 24, height: 44)
          : Rect.fromLTWH(cx - w / 2, top, w, h);
      slots[id] = _Slot(id: id, rect: rect, node: node, parent: parent, depth: depth, index: index, kind: kind);
      maxY = math.max(maxY, rect.bottom);
      return (width, cx);
    }

    var x = gap / 2;
    final centres = <double>[];
    for (var i = 0; i < t.roots.length; i++) {
      final (rw, rcx) = place(t.roots[i].id, t.roots[i], _SlotKind.node, seedId, 1, i, x);
      centres.add(rcx);
      x += rw;
    }
    final width = math.max(x + gap / 2, w + gap);
    final seedX = centres.isEmpty ? width / 2 : (centres.first + centres.last) / 2;
    slots[seedId] = _Slot(
      id: seedId,
      rect: Rect.fromCenter(center: Offset(seedX, BranchTree.seedHeight / 2), width: 220, height: 44),
      kind: _SlotKind.seed,
    );
    return _Layout(slots, Size(width, maxY + 40));
  }
}

class _BranchTreeState extends State<BranchTree> with TickerProviderStateMixin {
  /// New branches growing out of the branch that just opened.
  late final AnimationController _grow =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 900));

  /// A light running down from the top along every branch, once per growth.
  late final AnimationController _sap =
      AnimationController(vsync: this, duration: const Duration(milliseconds: 2200));

  final Map<String, GlobalKey> _keys = {};
  String? _lastGrowing;

  @override
  void initState() {
    super.initState();
    _startGrowth();
  }

  @override
  void didUpdateWidget(BranchTree old) {
    super.didUpdateWidget(old);
    if (widget.growing != null && widget.growing != _lastGrowing) _startGrowth();
  }

  void _startGrowth() {
    _lastGrowing = widget.growing;
    _grow.forward(from: 0);
    _sap.forward(from: 0);
    final target = widget.growing;
    if (target == null) return;
    // follow the growth: bring the new branches into view
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      final node = _find(widget.roots, target);
      final first = node == null || node.children.isEmpty ? null : node.children.first.id;
      final ctx = _keys[first ?? 'more-$target']?.currentContext;
      if (ctx != null) {
        Scrollable.ensureVisible(ctx, alignment: 0.35, duration: const Duration(milliseconds: 500),
            curve: Curves.easeOutCubic);
      }
    });
  }

  TopicNode? _find(List<TopicNode> nodes, String id) {
    for (final n in nodes) {
      if (n.id == id) return n;
      final hit = _find(n.children, id);
      if (hit != null) return hit;
    }
    return null;
  }

  @override
  void dispose() {
    _grow.dispose();
    _sap.dispose();
    super.dispose();
  }

  /// Every branch on the way from a ticked one up to the top.
  Set<String> _litPath(_Layout layout) {
    final lit = <String>{};
    for (final id in widget.selected) {
      String? at = id;
      while (at != null && layout.slots.containsKey(at)) {
        lit.add(at);
        at = layout.slots[at]!.parent;
      }
    }
    return lit;
  }

  @override
  Widget build(BuildContext context) {
    final layout = _Layout.of(widget);
    final lit = _litPath(layout);
    return SizedBox(
      width: layout.size.width,
      height: layout.size.height,
      child: AnimatedBuilder(
        animation: Listenable.merge([_grow, _sap]),
        builder: (context, _) {
          final g = Curves.easeOutCubic.transform(_grow.value);
          return Stack(
            clipBehavior: Clip.none,
            children: [
              Positioned.fill(
                child: CustomPaint(
                  painter: _BranchPainter(
                    layout: layout,
                    growing: widget.growing,
                    grow: g,
                    sap: _sap.isAnimating ? _sap.value : null,
                    lit: lit,
                  ),
                ),
              ),
              for (final slot in layout.slots.values) _slot(slot, g, lit),
            ],
          );
        },
      ),
    );
  }

  Widget _slot(_Slot slot, double grow, Set<String> lit) {
    // a child of the branch that is growing appears as its branch reaches it
    final fresh = slot.parent != null && slot.parent == widget.growing;
    final reveal = fresh ? ((grow - 0.45 - 0.06 * slot.index.clamp(0, 6)) / 0.4).clamp(0.0, 1.0) : 1.0;
    final key = _keys.putIfAbsent(slot.id, GlobalKey.new);
    final Widget child = switch (slot.kind) {
      _SlotKind.seed => _Seed(title: widget.title),
      _SlotKind.leaf => Center(
          child: Text('This branch doesn\'t split further.',
              textAlign: TextAlign.center, style: sans(11.5, color: Paper.faint)),
        ),
      _SlotKind.more => Center(
          child: TextButton.icon(
            key: ValueKey('more-${slot.node!.id}'),
            onPressed: widget.branching.contains(slot.node!.id) ? null : () => widget.onMore(slot.node!),
            icon: const Icon(Icons.add_rounded, size: 16),
            label: const Text('More branches'),
            style: TextButton.styleFrom(
              foregroundColor: Paper.muted,
              textStyle: sans(12),
              backgroundColor: Paper.card.withValues(alpha: 0.9),
              shape: const StadiumBorder(side: BorderSide(color: Paper.border)),
            ),
          ),
        ),
      _SlotKind.node => _NodeCard(
          node: slot.node!,
          selected: widget.selected.contains(slot.node!.id),
          onPath: lit.contains(slot.node!.id),
          open: widget.open.contains(slot.node!.id),
          branching: widget.branching.contains(slot.node!.id),
          onToggle: () => widget.onToggle(slot.node!),
          onBranch: () => widget.onBranch(slot.node!),
        ),
    };
    return Positioned.fromRect(
      key: ValueKey('slot-${slot.id}'),
      rect: slot.rect,
      child: IgnorePointer(
        ignoring: reveal < 1,
        child: Opacity(
          opacity: reveal,
          child: Transform.scale(scale: 0.85 + 0.15 * reveal, child: KeyedSubtree(key: key, child: child)),
        ),
      ),
    );
  }
}

class _BranchPainter extends CustomPainter {
  _BranchPainter({required this.layout, required this.growing, required this.grow, required this.sap,
      required this.lit});

  final _Layout layout;
  final String? growing;
  final double grow;
  final double? sap;
  final Set<String> lit;

  static const _gold = Color(0xFFE2B33C);

  Path _edge(Rect from, Rect to) {
    final a = Offset(from.center.dx, from.bottom);
    final b = Offset(to.center.dx, to.top);
    final dy = (b.dy - a.dy) * 0.55;
    return Path()
      ..moveTo(a.dx, a.dy)
      ..cubicTo(a.dx, a.dy + dy, b.dx, b.dy - dy, b.dx, b.dy);
  }

  @override
  void paint(Canvas canvas, Size size) {
    final maxDepth = layout.slots.values.fold<int>(1, (m, s) => math.max(m, s.depth));
    for (final slot in layout.slots.values) {
      final parent = slot.parent == null ? null : layout.slots[slot.parent];
      if (parent == null) continue;
      final path = _edge(parent.rect, slot.rect);
      final metric = path.computeMetrics().first;
      final fresh = slot.parent == growing;
      // each new branch starts a little after the one before it
      final t = fresh ? ((grow - 0.05 * slot.index.clamp(0, 6)) / 0.7).clamp(0.0, 1.0) : 1.0;
      if (t <= 0) continue;
      final shown = metric.extractPath(0, metric.length * t);
      final onPath = lit.contains(slot.id);
      final ghost = slot.kind == _SlotKind.more || slot.kind == _SlotKind.leaf;
      final colour = onPath ? _gold : Paper.accent;
      // the glow, then the branch itself
      canvas.drawPath(
        shown,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeCap = StrokeCap.round
          ..strokeWidth = onPath ? 10 : 7
          ..color = colour.withValues(alpha: ghost ? 0.06 : (onPath ? 0.32 : 0.16 + 0.2 * (fresh ? 1 - t : 0)))
          ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 6),
      );
      final line = Paint()
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeWidth = ghost ? 1.2 : (onPath ? 3 : 2.2)
        ..color = colour.withValues(alpha: ghost ? 0.3 : 0.85);
      if (ghost) {
        _dashed(canvas, shown, line);
      } else {
        canvas.drawPath(shown, line);
      }
      // the growing tip: a bright bud of light
      if (fresh && t < 1) {
        final tip = metric.getTangentForOffset(metric.length * t)?.position;
        if (tip != null) _spark(canvas, tip, 9, colour);
      }
      // the light running down from the top, reaching each depth in turn
      if (sap != null && !ghost) {
        final along = sap! * (maxDepth + 1) - (slot.depth - 1);
        if (along > 0 && along < 1) {
          final at = metric.getTangentForOffset(metric.length * along)?.position;
          if (at != null) _spark(canvas, at, 6, colour);
        }
      }
    }
  }

  void _spark(Canvas canvas, Offset at, double r, Color colour) {
    canvas.drawCircle(at, r * 1.8,
        Paint()
          ..color = colour.withValues(alpha: 0.35)
          ..maskFilter = MaskFilter.blur(BlurStyle.normal, r));
    canvas.drawCircle(at, r * 0.45, Paint()..color = Colors.white.withValues(alpha: 0.95));
  }

  void _dashed(Canvas canvas, Path path, Paint paint) {
    for (final m in path.computeMetrics()) {
      for (var d = 0.0; d < m.length; d += 9) {
        canvas.drawPath(m.extractPath(d, math.min(d + 5, m.length)), paint);
      }
    }
  }

  @override
  bool shouldRepaint(_BranchPainter old) =>
      old.grow != grow || old.sap != sap || old.layout.size != layout.size || old.lit.length != lit.length ||
      old.growing != growing || !identical(old.layout, layout);
}

/// The top of the tree: the topic.
class _Seed extends StatelessWidget {
  const _Seed({required this.title});
  final String title;

  @override
  Widget build(BuildContext context) {
    return Container(
      alignment: Alignment.center,
      padding: const EdgeInsets.symmetric(horizontal: 14),
      decoration: BoxDecoration(
        color: Paper.accent,
        borderRadius: BorderRadius.circular(100),
        boxShadow: [BoxShadow(color: Paper.accent.withValues(alpha: 0.45), blurRadius: 18)],
      ),
      child: Text(title,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: sans(13.5, weight: FontWeight.w700, color: Colors.white)),
    );
  }
}

/// A branch: tick it into the course (tap the card), open it to branch on.
class _NodeCard extends StatelessWidget {
  const _NodeCard({
    required this.node,
    required this.selected,
    required this.onPath,
    required this.open,
    required this.branching,
    required this.onToggle,
    required this.onBranch,
  });

  final TopicNode node;
  final bool selected;
  final bool onPath;
  final bool open;
  final bool branching;
  final VoidCallback onToggle;
  final VoidCallback onBranch;

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 220),
      decoration: BoxDecoration(
        color: selected ? Paper.accentSoft : Paper.card,
        border: Border.all(color: selected ? Paper.accent : (onPath ? const Color(0xFFE2B33C) : Paper.border),
            width: selected ? 1.6 : 1),
        borderRadius: BorderRadius.circular(14),
        boxShadow: [
          BoxShadow(
            color: (selected ? Paper.accent : Colors.black).withValues(alpha: selected ? 0.28 : 0.06),
            blurRadius: selected ? 16 : 8,
            offset: const Offset(0, 3),
          ),
        ],
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onToggle,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(4, 4, 8, 4),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  SizedBox(
                    width: 34,
                    height: 34,
                    child: Checkbox(
                      key: ValueKey('select-${node.id}'),
                      value: selected,
                      activeColor: Paper.accent,
                      visualDensity: VisualDensity.compact,
                      onChanged: (_) => onToggle(),
                    ),
                  ),
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.only(top: 7),
                      child: Text(node.title,
                          maxLines: 2, overflow: TextOverflow.ellipsis, style: sans(13.5, weight: FontWeight.w600)),
                    ),
                  ),
                ]),
                if (node.summary.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(left: 6, top: 2),
                    child: Text(node.summary,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: sans(11.5, color: Paper.muted, height: 1.35)),
                  ),
                const Spacer(),
                Align(
                  alignment: Alignment.bottomRight,
                  child: branching
                      ? const Padding(
                          padding: EdgeInsets.all(8),
                          child: SizedBox(
                              width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Paper.accent)),
                        )
                      : TextButton.icon(
                          key: ValueKey('branch-${node.id}'),
                          onPressed: onBranch,
                          icon: AnimatedRotation(
                            turns: open ? 0.5 : 0,
                            duration: const Duration(milliseconds: 220),
                            child: const Icon(Icons.expand_more_rounded, size: 16),
                          ),
                          label: Text(open ? 'Hide' : (node.expanded ? 'Show branches' : 'Branch further')),
                          style: TextButton.styleFrom(
                            foregroundColor: Paper.accent,
                            textStyle: sans(11.5),
                            visualDensity: VisualDensity.compact,
                            padding: const EdgeInsets.symmetric(horizontal: 8),
                          ),
                        ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
