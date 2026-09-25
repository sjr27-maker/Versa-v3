import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import 'topic_api.dart';
import 'topic_models.dart';
import 'topic_widgets.dart';
import 'topics_root.dart';

/// One course: the overall progress (tap it for the lesson path), and every
/// chapter with its own progress and lessons. Nothing is locked.
class TopicScreen extends StatefulWidget {
  const TopicScreen({super.key, required this.topicId, this.initial});

  final String topicId;

  /// Already in hand (just built); shown at once, then refreshed.
  final Topic? initial;

  @override
  State<TopicScreen> createState() => _TopicScreenState();
}

class _TopicScreenState extends State<TopicScreen> {
  Topic? _topic;
  Object? _error;

  /// Chapters whose lesson list is folded away.
  final Set<String> _folded = {};

  @override
  void initState() {
    super.initState();
    _topic = widget.initial;
    if (_topic == null) _reload();
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

  Future<void> _openLesson(String id) async {
    await pushLesson(context, id);
    _reload();
  }

  Future<void> _openPath() async {
    await pushPath(context, widget.topicId, initial: _topic);
    _reload();
  }

  @override
  Widget build(BuildContext context) {
    final t = _topic;
    return Container(
      color: Paper.surface,
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(32, 32, 32, 60),
        child: Align(
          alignment: Alignment.topLeft,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 900),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                PageHeading(
                  eyebrow: 'TOPIC',
                  title: t?.title ?? 'Loading…',
                  onBack: () => Navigator.of(context).maybePop(),
                  backKey: const ValueKey('topic-back'),
                ),
                const SizedBox(height: 16),
                if (t == null && _error == null)
                  const Center(child: CircularProgressIndicator(color: Paper.accent))
                else if (t == null)
                  RetryLine(message: 'Could not load this topic: $_error', onRetry: _reload)
                else ...[
                  ShapedByNote(sources: t.personalizedBy),
                  if (t.personalizedBy.isNotEmpty) const SizedBox(height: 14),
                  _OverallCard(topic: t, onTap: _openPath),
                  const SizedBox(height: 26),
                  Text('Chapters', style: serif(21)),
                  const SizedBox(height: 4),
                  Text('Start anywhere: every lesson is open.', style: sans(12.5, color: Paper.muted)),
                  const SizedBox(height: 14),
                  for (var i = 0; i < t.chapters.length; i++)
                    _ChapterCard(
                      number: i + 1,
                      chapter: t.chapters[i],
                      folded: _folded.contains(t.chapters[i].id),
                      onFold: () => setState(() {
                        final id = t.chapters[i].id;
                        _folded.contains(id) ? _folded.remove(id) : _folded.add(id);
                      }),
                      onLesson: _openLesson,
                    ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// The overall bar at the top: tapping it opens the lesson path.
class _OverallCard extends StatelessWidget {
  const _OverallCard({required this.topic, required this.onTap});
  final Topic topic;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        key: const ValueKey('topic-progress'),
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            color: Paper.card,
            border: Border.all(color: Paper.accent, width: 1.5),
            borderRadius: BorderRadius.circular(16),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                Text('OVERALL', style: mono(10)),
                const Spacer(),
                Text('See your path', style: sans(12.5, color: Paper.accent, weight: FontWeight.w600)),
                const SizedBox(width: 4),
                const Icon(Icons.route_rounded, size: 16, color: Paper.accent),
              ]),
              const SizedBox(height: 10),
              PercentRow(percent: topic.percent, height: 12, labelKey: const ValueKey('topic-percent')),
              const SizedBox(height: 8),
              Text(
                topic.percent >= 100
                    ? 'Topic complete. Every lesson is done.'
                    : '${topic.lessonsDone} of ${topic.lessonCount} lessons done',
                style: sans(12.5, color: Paper.muted),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ChapterCard extends StatelessWidget {
  const _ChapterCard({
    required this.number,
    required this.chapter,
    required this.folded,
    required this.onFold,
    required this.onLesson,
  });

  final int number;
  final Chapter chapter;
  final bool folded;
  final VoidCallback onFold;
  final void Function(String lessonId) onLesson;

  @override
  Widget build(BuildContext context) {
    return Container(
      key: ValueKey('chapter-${chapter.id}'),
      margin: const EdgeInsets.only(bottom: 14),
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            borderRadius: BorderRadius.circular(14),
            onTap: onFold,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(18, 16, 12, 14),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    width: 32,
                    height: 32,
                    alignment: Alignment.center,
                    decoration: BoxDecoration(
                      color: chapter.percent >= 100 ? Paper.olive : Paper.accentSoft,
                      shape: BoxShape.circle,
                    ),
                    child: chapter.percent >= 100
                        ? const Icon(Icons.check_rounded, size: 18, color: Colors.white)
                        : Text('$number', style: sans(13, color: Paper.accent, weight: FontWeight.w700)),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(chapter.title, style: serif(17)),
                        if (chapter.summary.isNotEmpty) ...[
                          const SizedBox(height: 3),
                          Text(chapter.summary, style: sans(12.5, color: Paper.muted, height: 1.45)),
                        ],
                        const SizedBox(height: 10),
                        PercentRow(percent: chapter.percent, labelKey: ValueKey('chapter-percent-${chapter.id}')),
                      ],
                    ),
                  ),
                  AnimatedRotation(
                    turns: folded ? -0.25 : 0,
                    duration: const Duration(milliseconds: 200),
                    child: const Icon(Icons.expand_more_rounded, color: Paper.faint),
                  ),
                ],
              ),
            ),
          ),
          AnimatedSize(
            duration: const Duration(milliseconds: 240),
            curve: Curves.easeOutCubic,
            alignment: Alignment.topLeft,
            child: folded
                ? const SizedBox(width: double.infinity)
                : Padding(
                    padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
                    child: Column(children: [
                      for (final l in chapter.lessons) _LessonRow(lesson: l, onTap: () => onLesson(l.id)),
                    ]),
                  ),
          ),
        ],
      ),
    );
  }
}

class _LessonRow extends StatelessWidget {
  const _LessonRow({required this.lesson, required this.onTap});
  final LessonSummary lesson;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      key: ValueKey('lesson-row-${lesson.id}'),
      borderRadius: BorderRadius.circular(10),
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 10),
        child: Row(children: [
          LessonStatusIcon(status: lesson.status),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(lesson.title, style: sans(14, weight: FontWeight.w500)),
                if (lesson.objective.isNotEmpty)
                  Text(lesson.objective,
                      maxLines: 1, overflow: TextOverflow.ellipsis, style: sans(12, color: Paper.muted)),
              ],
            ),
          ),
          const SizedBox(width: 10),
          SizedBox(width: 90, child: TopicProgressBar(percent: lesson.percent, height: 6)),
          SizedBox(
            width: 44,
            child: Text('${lesson.percent}%',
                textAlign: TextAlign.right, style: sans(12, color: Paper.body)),
          ),
          const SizedBox(width: 4),
          const Icon(Icons.chevron_right_rounded, size: 18, color: Paper.faint),
        ]),
      ),
    );
  }
}
