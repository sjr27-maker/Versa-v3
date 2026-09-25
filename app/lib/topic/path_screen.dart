import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import 'topic_api.dart';
import 'topic_models.dart';
import 'topic_widgets.dart';
import 'topics_root.dart';

/// The lesson path (Duolingo as the reference): chapters as section banners,
/// lessons as round nodes on a winding road. Done lessons are filled, the one
/// in progress shows its ring, the rest are outlined. Every node opens: no
/// locking.
class PathScreen extends StatefulWidget {
  const PathScreen({super.key, required this.topicId, this.initial});
  final String topicId;
  final Topic? initial;

  @override
  State<PathScreen> createState() => _PathScreenState();
}

class _PathScreenState extends State<PathScreen> {
  Topic? _topic;
  Object? _error;

  @override
  void initState() {
    super.initState();
    _topic = widget.initial;
    _reload();
  }

  Future<void> _reload() async {
    try {
      final t = await TopicApi.of(context.read<AppState>().api).getTopic(widget.topicId);
      if (!mounted) return;
      setState(() {
        _topic = t;
        _error = null;
      });
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  Future<void> _open(String lessonId) async {
    await pushLesson(context, lessonId);
    _reload();
  }

  /// The lesson to point at: the first one in progress, else the first one
  /// not started.
  String? _nextLessonId(Topic t) {
    final lessons = [for (final c in t.chapters) ...c.lessons];
    for (final l in lessons) {
      if (l.status == LessonStatus.inProgress) return l.id;
    }
    for (final l in lessons) {
      if (l.status == LessonStatus.notStarted) return l.id;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final t = _topic;
    return Container(
      color: Paper.page,
      child: CustomScrollView(
        slivers: [
          SliverToBoxAdapter(
            child: Container(
              padding: const EdgeInsets.fromLTRB(28, 28, 28, 18),
              decoration: const BoxDecoration(
                color: Paper.surface,
                border: Border(bottom: BorderSide(color: Paper.border)),
              ),
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 720),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      PageHeading(
                        eyebrow: 'YOUR PATH',
                        title: t?.title ?? 'Loading…',
                        onBack: () => Navigator.of(context).maybePop(),
                        backKey: const ValueKey('path-back'),
                      ),
                      if (t != null) ...[
                        const SizedBox(height: 14),
                        PercentRow(percent: t.percent, height: 12, labelKey: const ValueKey('path-percent')),
                      ],
                    ],
                  ),
                ),
              ),
            ),
          ),
          if (t == null && _error != null)
            SliverToBoxAdapter(
              child: Padding(
                padding: const EdgeInsets.all(28),
                child: RetryLine(message: 'Could not load the path: $_error', onRetry: _reload),
              ),
            )
          else if (t == null)
            const SliverToBoxAdapter(
              child: Padding(
                padding: EdgeInsets.all(40),
                child: Center(child: CircularProgressIndicator(color: Paper.accent)),
              ),
            )
          else
            SliverToBoxAdapter(
              child: Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 720),
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(20, 20, 20, 80),
                    child: _PathBody(topic: t, nextLessonId: _nextLessonId(t), onOpen: _open),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _PathBody extends StatelessWidget {
  const _PathBody({required this.topic, required this.nextLessonId, required this.onOpen});
  final Topic topic;
  final String? nextLessonId;
  final void Function(String lessonId) onOpen;

  @override
  Widget build(BuildContext context) {
    var offsetIndex = 0;
    final children = <Widget>[];
    for (var i = 0; i < topic.chapters.length; i++) {
      final c = topic.chapters[i];
      children.add(_ChapterBanner(number: i + 1, chapter: c));
      children.add(_PathSection(
        lessons: c.lessons,
        startIndex: offsetIndex,
        nextLessonId: nextLessonId,
        onOpen: onOpen,
      ));
      offsetIndex += c.lessons.length;
    }
    if (topic.percent >= 100) {
      children.add(Padding(
        padding: const EdgeInsets.only(top: 12),
        child: Column(children: [
          const Icon(Icons.emoji_events_rounded, size: 56, color: Paper.warn),
          const SizedBox(height: 6),
          Text('Topic complete', key: const ValueKey('path-complete'), style: serif(20)),
        ]),
      ));
    }
    return Column(children: children);
  }
}

class _ChapterBanner extends StatelessWidget {
  const _ChapterBanner({required this.number, required this.chapter});
  final int number;
  final Chapter chapter;

  @override
  Widget build(BuildContext context) {
    final done = chapter.percent >= 100;
    final color = done ? Paper.olive : Paper.accent;
    return Container(
      key: ValueKey('path-chapter-${chapter.id}'),
      width: double.infinity,
      margin: const EdgeInsets.only(top: 8, bottom: 6),
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 16),
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(16),
        boxShadow: [BoxShadow(color: Color.lerp(color, Colors.black, 0.25)!, offset: const Offset(0, 4))],
      ),
      child: Row(children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('CHAPTER $number', style: mono(10, color: Colors.white70)),
              const SizedBox(height: 4),
              Text(chapter.title, style: serif(19, color: Colors.white)),
            ],
          ),
        ),
        const SizedBox(width: 12),
        Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
          Text('${chapter.percent}%', style: sans(18, color: Colors.white, weight: FontWeight.w700)),
          Text(
            '${chapter.lessons.where((l) => l.status == LessonStatus.done).length}/${chapter.lessons.length} lessons',
            style: sans(11.5, color: Colors.white70),
          ),
        ]),
      ]),
    );
  }
}

/// One chapter's stretch of road.
class _PathSection extends StatelessWidget {
  const _PathSection({
    required this.lessons,
    required this.startIndex,
    required this.nextLessonId,
    required this.onOpen,
  });

  final List<LessonSummary> lessons;

  /// Position along the whole path, so the road keeps winding smoothly
  /// across chapter banners.
  final int startIndex;
  final String? nextLessonId;
  final void Function(String lessonId) onOpen;

  static const _rowHeight = 150.0;
  static const _nodeSize = 72.0;
  static const _top = 36.0;

  // The road's sway, one step per lesson.
  static const _sway = [0.0, 0.55, 0.9, 0.55, 0.0, -0.55, -0.9, -0.55];

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      final width = c.maxWidth;
      final amplitude = math.min(width * 0.26, 150.0);
      final centers = [
        for (var i = 0; i < lessons.length; i++)
          Offset(width / 2 + _sway[(startIndex + i) % _sway.length] * amplitude,
              _top + _nodeSize / 2 + i * _rowHeight),
      ];
      final height = lessons.isEmpty ? 24.0 : _top + lessons.length * _rowHeight;
      return SizedBox(
        height: height,
        child: Stack(
          clipBehavior: Clip.none,
          children: [
            Positioned.fill(
              child: CustomPaint(
                painter: _RoadPainter(
                  centers: centers,
                  done: [for (final l in lessons) l.status == LessonStatus.done],
                ),
              ),
            ),
            for (var i = 0; i < lessons.length; i++)
              Positioned(
                left: centers[i].dx - 80,
                top: centers[i].dy - (_nodeSize + 16) / 2,
                width: 160,
                child: _PathNode(
                  lesson: lessons[i],
                  isNext: lessons[i].id == nextLessonId,
                  onTap: () => onOpen(lessons[i].id),
                ),
              ),
          ],
        ),
      );
    });
  }
}

/// The road joining consecutive lessons; a stretch leaving a done lesson is
/// coloured in.
class _RoadPainter extends CustomPainter {
  _RoadPainter({required this.centers, required this.done});
  final List<Offset> centers;
  final List<bool> done;

  @override
  void paint(Canvas canvas, Size size) {
    for (var i = 0; i + 1 < centers.length; i++) {
      final a = centers[i];
      final b = centers[i + 1];
      final midY = (a.dy + b.dy) / 2;
      final path = Path()
        ..moveTo(a.dx, a.dy)
        ..cubicTo(a.dx, midY, b.dx, midY, b.dx, b.dy);
      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..strokeCap = StrokeCap.round
        ..strokeWidth = 10
        ..color = done[i] ? Paper.olive.withValues(alpha: 0.55) : Paper.border;
      canvas.drawPath(path, paint);
    }
  }

  @override
  bool shouldRepaint(covariant _RoadPainter old) => old.centers != centers || old.done != done;
}

class _PathNode extends StatefulWidget {
  const _PathNode({required this.lesson, required this.isNext, required this.onTap});
  final LessonSummary lesson;
  final bool isNext;
  final VoidCallback onTap;

  @override
  State<_PathNode> createState() => _PathNodeState();
}

class _PathNodeState extends State<_PathNode> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final l = widget.lesson;
    const size = _PathSection._nodeSize;
    final (Color fill, Color rim, IconData icon, Color iconColor) = switch (l.status) {
      LessonStatus.done => (Paper.olive, const Color(0xFF557A47), Icons.check_rounded, Colors.white),
      LessonStatus.inProgress => (Paper.accent, Paper.accentDark, Icons.play_arrow_rounded, Colors.white),
      LessonStatus.notStarted => (Paper.card, Paper.borderStrong, Icons.menu_book_rounded, Paper.faint),
    };
    final circle = AnimatedContainer(
      duration: const Duration(milliseconds: 90),
      width: size,
      height: size,
      transform: Matrix4.translationValues(0, _pressed ? 4 : 0, 0),
      decoration: BoxDecoration(
        color: fill,
        shape: BoxShape.circle,
        border: Border.all(color: rim, width: 2),
        boxShadow: _pressed ? null : [BoxShadow(color: rim, offset: const Offset(0, 5))],
      ),
      child: Icon(icon, color: iconColor, size: 32),
    );
    final ring = l.status == LessonStatus.inProgress
        ? SizedBox(
            width: size + 16,
            height: size + 16,
            child: TweenAnimationBuilder<double>(
              tween: Tween(end: l.percent / 100),
              duration: const Duration(milliseconds: 600),
              builder: (context, v, _) => CircularProgressIndicator(
                value: v,
                strokeWidth: 5,
                backgroundColor: Paper.border,
                valueColor: const AlwaysStoppedAnimation(Paper.accent),
              ),
            ),
          )
        : const SizedBox(width: size + 16, height: size + 16);
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        MouseRegion(
          cursor: SystemMouseCursors.click,
          child: GestureDetector(
            key: ValueKey('path-node-${l.id}'),
            onTapDown: (_) => setState(() => _pressed = true),
            onTapCancel: () => setState(() => _pressed = false),
            onTapUp: (_) => setState(() => _pressed = false),
            onTap: widget.onTap,
            child: Tooltip(
              message: '${l.title} · ${l.percent}%',
              child: SizedBox(
                key: ValueKey('path-state-${l.status.name}-${l.id}'),
                width: size + 16,
                height: size + 16,
                child: Stack(alignment: Alignment.center, children: [ring, circle]),
              ),
            ),
          ),
        ),
        const SizedBox(height: 2),
        if (widget.isNext)
          Container(
            key: const ValueKey('path-next'),
            margin: const EdgeInsets.only(bottom: 3),
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            decoration: BoxDecoration(color: Paper.ink, borderRadius: BorderRadius.circular(100)),
            child: Text(l.status == LessonStatus.inProgress ? 'CONTINUE' : 'START',
                style: mono(9, color: Colors.white, weight: FontWeight.w700)),
          ),
        Text(
          l.title,
          maxLines: 2,
          textAlign: TextAlign.center,
          overflow: TextOverflow.ellipsis,
          style: sans(12, color: l.status == LessonStatus.notStarted ? Paper.muted : Paper.ink, weight: FontWeight.w500),
        ),
      ],
    );
  }
}
