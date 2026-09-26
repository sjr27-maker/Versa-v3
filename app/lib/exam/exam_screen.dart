import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import '../topic/topic_widgets.dart';
import '../topic/topics_root.dart' show topicRoute;
import 'exam_api.dart';
import 'exam_models.dart';
import 'exams_home_screen.dart' show examDateLine;
import 'exams_root.dart';
import 'plan_widgets.dart';

/// One exam: its date, the study plan, the mock test, and a quiz per
/// syllabus unit.
class ExamScreen extends StatefulWidget {
  const ExamScreen({super.key, required this.examId, this.initial});
  final String examId;
  final Exam? initial;

  @override
  State<ExamScreen> createState() => _ExamScreenState();
}

class _ExamScreenState extends State<ExamScreen> {
  late Future<Exam> _exam;
  StudyPlan? _plan;
  bool _planBusy = false;

  ExamApi get _api => ExamApi.of(context.read<AppState>().api);

  @override
  void initState() {
    super.initState();
    _exam = _load(initial: widget.initial);
  }

  /// The exam and its study plan, together, so the page never shows one
  /// without the other.
  Future<Exam> _load({Exam? initial}) async {
    final api = _api;
    final results = await Future.wait<Object?>([
      initial != null ? Future.value(initial) : api.getExam(widget.examId),
      api.getPlan(widget.examId),
    ]);
    _plan = results[1] as StudyPlan?;
    return results[0] as Exam;
  }

  void _toast(String text) => ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(text)));

  Future<void> _makePlan(Exam exam, {bool replan = false}) async {
    var date = exam.summary.examDate ?? _plan?.endDate;
    if (replan) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          backgroundColor: Paper.surface,
          title: Text('Re-plan from today?', style: serif(19)),
          content: Text(
            'The days from today to the exam are planned again from scratch. Your quizzes and '
            'mock results stay; the old plan is kept on record.',
            style: sans(14, color: Paper.body, height: 1.45),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Cancel')),
            FilledButton(
              key: const ValueKey('plan-replan-confirm'),
              onPressed: () => Navigator.of(context).pop(true),
              style: FilledButton.styleFrom(backgroundColor: Paper.accent),
              child: const Text('Re-plan'),
            ),
          ],
        ),
      );
      if (ok != true || !mounted) return;
    }
    if (date == null) {
      final now = DateTime.now();
      date = await showDatePicker(
        context: context,
        initialDate: now.add(const Duration(days: 14)),
        firstDate: now.add(const Duration(days: 1)),
        lastDate: now.add(const Duration(days: 365)),
        helpText: 'When is the exam?',
      );
      if (date == null || !mounted) return;
    }
    final api = _api;
    setState(() => _planBusy = true);
    try {
      final plan = await api.makePlan(widget.examId, examDate: date);
      if (mounted) setState(() => _plan = plan);
    } catch (e) {
      if (mounted) _toast('$e');
    } finally {
      if (mounted) setState(() => _planBusy = false);
    }
  }

  Future<StudyPlan?> _togglePlanItem(PlanItem item) async {
    try {
      final plan = await _api.tickPlanItem(item.id, !item.done);
      if (mounted) setState(() => _plan = plan);
      return plan;
    } catch (e) {
      if (mounted) {
        _toast('$e');
        _reload();
      }
      return null;
    }
  }

  Future<void> _startPlanItem(Exam exam, PlanItem item) async {
    if (item.kind == PlanKind.mock) return _mock(exam);
    final unitId = item.unitId;
    if (unitId == null) return;
    final api = _api;
    await pushQuiz(context,
        label: 'Writing a quiz on ${item.unitTitle ?? 'this unit'}…', load: () => api.startUnitQuiz(unitId));
    _reload();
  }

  Future<void> _openPlan(Exam exam) async {
    final plan = _plan;
    if (plan == null) return;
    await Navigator.of(context).push(topicRoute((_) => PlanScreen(
          examTitle: exam.summary.title,
          initial: plan,
          onToggle: _togglePlanItem,
          onStart: (item) => _startPlanItem(exam, item),
        )));
    _reload();
  }

  void _reload() {
    if (!mounted) return;
    setState(() {
      _exam = _load();
    });
  }

  Future<void> _quiz(ExamUnit unit) async {
    final api = _api;
    await pushQuiz(context, label: 'Writing a quiz on ${unit.title}…', load: () => api.startUnitQuiz(unit.id));
    _reload();
  }

  Future<void> _mock(Exam exam) async {
    final api = _api;
    await pushQuiz(context,
        label: 'Writing a mock test across all ${exam.units.length} units…', load: () => api.startMock(exam.id));
    _reload();
  }

  Future<void> _openMock(MockSummary mock) async {
    final api = _api;
    await pushQuiz(context, label: 'Opening your mock test…', load: () => api.getQuiz(mock.quizId));
    _reload();
  }

  @override
  Widget build(BuildContext context) {
    return Container(
      color: Paper.surface,
      child: FutureBuilder<Exam>(
        future: _exam,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator(color: Paper.accent));
          }
          if (snap.hasError) {
            return Padding(
              padding: const EdgeInsets.all(32),
              child: RetryLine(message: 'Could not load this exam: ${snap.error}', onRetry: _reload),
            );
          }
          final exam = snap.data!;
          final when = examDateLine(exam.summary);
          return SingleChildScrollView(
            padding: const EdgeInsets.fromLTRB(32, 32, 32, 60),
            child: Align(
              alignment: Alignment.topLeft,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 900),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    PageHeading(
                      eyebrow: 'EXAM',
                      title: exam.summary.title,
                      onBack: () => Navigator.of(context).maybePop(),
                      backKey: const ValueKey('exam-back'),
                    ),
                    const SizedBox(height: 8),
                    Padding(
                      padding: const EdgeInsets.only(left: 54),
                      child: Text(
                        when == null ? 'No date set' : 'Exam on $when',
                        key: const ValueKey('exam-when'),
                        style: sans(13.5, color: Paper.body, weight: FontWeight.w600),
                      ),
                    ),
                    const SizedBox(height: 22),
                    PlanCard(
                      plan: _plan,
                      hasExamDate: exam.summary.examDate != null,
                      busy: _planBusy,
                      onMake: () => _makePlan(exam),
                      onReplan: () => _makePlan(exam, replan: true),
                      onOpenFull: () => _openPlan(exam),
                      onToggle: _togglePlanItem,
                      onStart: (item) => _startPlanItem(exam, item),
                    ),
                    const SizedBox(height: 18),
                    _MockCard(exam: exam, onStart: () => _mock(exam), onOpen: _openMock),
                    const SizedBox(height: 28),
                    Text('Units', style: serif(21)),
                    const SizedBox(height: 4),
                    Text('A 5-question quiz on one unit at a time. Each retake asks new questions.',
                        style: sans(13, color: Paper.muted)),
                    const SizedBox(height: 12),
                    for (final u in exam.units) _UnitCard(unit: u, onQuiz: () => _quiz(u)),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _MockCard extends StatelessWidget {
  const _MockCard({required this.exam, required this.onStart, required this.onOpen});
  final Exam exam;
  final VoidCallback onStart;
  final void Function(MockSummary) onOpen;

  @override
  Widget build(BuildContext context) {
    final questions = exam.units.length * 2 > 20 ? 20 : exam.units.length * 2;
    return Container(
      padding: const EdgeInsets.all(22),
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.accent, width: 1.5),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            const Icon(Icons.timer_outlined, color: Paper.accent),
            const SizedBox(width: 10),
            Expanded(child: Text('Mock test', style: serif(19))),
            FilledButton(
              key: const ValueKey('exam-start-mock'),
              onPressed: onStart,
              style: FilledButton.styleFrom(backgroundColor: Paper.accent),
              child: Text(exam.mocks.isEmpty ? 'Start mock test' : 'New mock test'),
            ),
          ]),
          const SizedBox(height: 6),
          Text(
            'About $questions questions from every unit, against the clock. '
            'Hand it in any time; when time runs out it hands itself in.',
            style: sans(13, color: Paper.muted, height: 1.45),
          ),
          if (exam.mocks.isNotEmpty) ...[
            const SizedBox(height: 14),
            for (final (i, m) in exam.mocks.reversed.indexed)
              InkWell(
                key: ValueKey('exam-mock-${m.quizId}'),
                borderRadius: BorderRadius.circular(8),
                onTap: () => onOpen(m),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
                  child: Row(children: [
                    Text('Mock ${exam.mocks.length - i}', style: sans(13.5, weight: FontWeight.w600)),
                    const SizedBox(width: 12),
                    Expanded(
                      child: !m.submitted
                          ? Text('Not handed in -- continue', style: sans(13, color: Paper.warn))
                          : _ScoreLine(score: m.score, overTime: m.overTime),
                    ),
                    const Icon(Icons.chevron_right_rounded, color: Paper.faint, size: 18),
                  ]),
                ),
              ),
          ],
        ],
      ),
    );
  }
}

class _ScoreLine extends StatelessWidget {
  const _ScoreLine({required this.score, this.overTime = false});
  final Score? score;
  final bool overTime;

  @override
  Widget build(BuildContext context) {
    final s = score;
    final percent = s?.percent;
    return Row(children: [
      if (percent != null) ...[
        SizedBox(width: 120, child: TopicProgressBar(percent: percent, height: 6)),
        const SizedBox(width: 10),
      ],
      Text(
        s == null ? '' : '${percent == null ? '–' : '$percent%'} · ${s.correct} of ${s.total}',
        style: sans(12.5, color: Paper.body),
      ),
      if (overTime) ...[
        const SizedBox(width: 8),
        Text('over time', style: sans(12, color: Paper.warn, weight: FontWeight.w600)),
      ],
    ]);
  }
}

class _UnitCard extends StatelessWidget {
  const _UnitCard({required this.unit, required this.onQuiz});
  final ExamUnit unit;
  final VoidCallback onQuiz;

  @override
  Widget build(BuildContext context) {
    final last = unit.lastPercent;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Container(
        key: ValueKey('exam-unit-${unit.id}'),
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          color: Paper.card,
          border: Border.all(color: Paper.border),
          borderRadius: BorderRadius.circular(14),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('${unit.position + 1}. ${unit.title}', style: serif(17)),
                  const SizedBox(height: 4),
                  Text(unit.summary, style: sans(13, color: Paper.body, height: 1.45)),
                  const SizedBox(height: 10),
                  if (last == null)
                    Text('Not quizzed yet', style: sans(12, color: Paper.faint))
                  else
                    Row(children: [
                      SizedBox(width: 140, child: TopicProgressBar(percent: last, height: 6)),
                      const SizedBox(width: 10),
                      Text(
                        'Last $last%'
                        '${unit.bestPercent != null && unit.bestPercent != last ? ' · best ${unit.bestPercent}%' : ''}'
                        ' · ${unit.quizzesTaken} quiz${unit.quizzesTaken == 1 ? '' : 'zes'}',
                        key: ValueKey('exam-unit-score-${unit.id}'),
                        style: sans(12, color: Paper.muted),
                      ),
                    ]),
                ],
              ),
            ),
            const SizedBox(width: 12),
            OutlinedButton(
              key: ValueKey('exam-quiz-${unit.id}'),
              onPressed: onQuiz,
              style: OutlinedButton.styleFrom(
                foregroundColor: Paper.accent,
                side: const BorderSide(color: Paper.accent),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
              ),
              child: Text(unit.quizzesTaken == 0 ? 'Take quiz' : 'Quiz again'),
            ),
          ],
        ),
      ),
    );
  }
}
