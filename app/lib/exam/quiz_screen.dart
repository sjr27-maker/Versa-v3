import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../picture.dart';
import '../theme.dart';
import '../topic/topic_widgets.dart';
import '../widgets/message_view.dart' show PictureNote;
import '../widgets/rich_text.dart';
import 'exam_api.dart';
import 'exam_models.dart';

/// The wall clock a mock counts down against (not the number of timer ticks,
/// which a browser throttles in a background tab). Replaceable in tests.
DateTime Function() quizClock = DateTime.now;

/// Taking a unit quiz or a mock test, then its marked results. [load]
/// starts a new sitting (or reopens one), so the questions are written while
/// [label] shows.
class QuizScreen extends StatefulWidget {
  const QuizScreen({super.key, required this.label, required this.load});
  final String label;
  final Future<Quiz> Function() load;

  @override
  State<QuizScreen> createState() => _QuizScreenState();
}

class _QuizScreenState extends State<QuizScreen> {
  Quiz? _quiz;
  Object? _error;
  bool _submitting = false;
  final Map<String, String> _answers = {};
  final Map<String, TextEditingController> _typed = {};

  /// A photo of their working for a written answer (picture.dart): read on
  /// upload, and handed in as part of the answer -- what the marker sees.
  final Map<String, AttachedPicture> _pictures = {};
  final Set<String> _pictureReading = {};
  final Map<String, String> _pictureProblem = {};

  Future<void> _attachPicture(String questionId) async {
    final picked = await choosePicture(context);
    if (picked == null || !mounted) return;
    final app = context.read<AppState>();
    setState(() {
      _pictureReading.add(questionId);
      _pictureProblem.remove(questionId);
    });
    try {
      final picture = await app.api.uploadPicture(app.learner!.id, picked.bytes, picked.name);
      if (!mounted) return;
      setState(() => _pictures[questionId] = picture);
    } catch (e) {
      if (mounted) setState(() => _pictureProblem[questionId] = '$e');
    } finally {
      if (mounted) setState(() => _pictureReading.remove(questionId));
    }
  }

  /// What is handed in for a question: the words, plus what their photo showed.
  String _answerFor(String questionId) {
    final words = (_answers[questionId] ?? '').trim();
    final picture = _pictures[questionId];
    return picture == null ? words : withPicture(words, picture.reading);
  }
  Timer? _ticker;
  DateTime? _deadline;
  int? _secondsLeft;

  ExamApi get _api => ExamApi.of(context.read<AppState>().api);

  @override
  void initState() {
    super.initState();
    _start();
  }

  @override
  void dispose() {
    _ticker?.cancel();
    for (final c in _typed.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _start() async {
    setState(() => _error = null);
    try {
      _show(await widget.load());
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  void _show(Quiz quiz) {
    if (!mounted) return;
    _ticker?.cancel();
    setState(() {
      _quiz = quiz;
      final left = quiz.secondsLeft;
      if (!quiz.submitted && left != null) {
        // count down from what the server says is left, not from our own clock
        _deadline = quizClock().add(Duration(seconds: left));
        _secondsLeft = left;
        _ticker = Timer.periodic(const Duration(seconds: 1), (_) => _tick());
      } else {
        _deadline = null;
        _secondsLeft = null;
      }
    });
    if (!quiz.submitted && quiz.secondsLeft == 0) _submit(timeUp: true);
  }

  void _tick() {
    final deadline = _deadline;
    if (deadline == null || !mounted) return;
    final left = deadline.difference(quizClock()).inSeconds;
    setState(() => _secondsLeft = left < 0 ? 0 : left);
    if (left <= 0) {
      _ticker?.cancel();
      _submit(timeUp: true);
    }
  }

  TextEditingController _controllerFor(String questionId) => _typed.putIfAbsent(questionId, () {
        final c = TextEditingController(text: _answers[questionId] ?? '');
        c.addListener(() => _answers[questionId] = c.text);
        return c;
      });

  int get _unanswered => _quiz!.questions.where((q) => _answerFor(q.id).isEmpty).length;

  Future<void> _submit({bool timeUp = false}) async {
    final quiz = _quiz;
    if (quiz == null || quiz.submitted || _submitting) return;
    if (!timeUp && _unanswered > 0) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          backgroundColor: Paper.surface,
          title: Text('Hand it in?', style: serif(19)),
          content: Text(
            '$_unanswered question${_unanswered == 1 ? ' is' : 's are'} still unanswered '
            'and will be marked wrong.',
            style: sans(14, color: Paper.body),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.of(context).pop(false), child: const Text('Keep going')),
            FilledButton(
              key: const ValueKey('quiz-confirm-submit'),
              onPressed: () => Navigator.of(context).pop(true),
              style: FilledButton.styleFrom(backgroundColor: Paper.accent),
              child: const Text('Hand in'),
            ),
          ],
        ),
      );
      if (ok != true || !mounted) return;
    }
    _ticker?.cancel();
    setState(() => _submitting = true);
    try {
      final done = await _api.submit(quiz.id, {
        for (final id in {..._answers.keys, ..._pictures.keys})
          if (_answerFor(id).isNotEmpty) id: _answerFor(id),
      });
      if (!mounted) return;
      setState(() => _submitting = false);
      _show(done);
      if (timeUp) {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(const SnackBar(content: Text('Time\'s up -- your answers were handed in.')));
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _submitting = false);
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text('$e')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final quiz = _quiz;
    Widget body;
    if (_error != null) {
      body = Padding(
        padding: const EdgeInsets.all(32),
        child: RetryLine(message: '$_error', onRetry: _start),
      );
    } else if (quiz == null) {
      body = Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          CircularProgressIndicator(color: Paper.accent),
          const SizedBox(height: 16),
          Text(widget.label, key: const ValueKey('quiz-loading'), style: sans(14, color: Paper.body)),
        ]),
      );
    } else {
      body = SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(32, 32, 32, 60),
        child: Align(
          alignment: Alignment.topLeft,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 820),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                PageHeading(
                  eyebrow: quiz.examTitle.toUpperCase(),
                  title: quiz.title,
                  onBack: () => Navigator.of(context).maybePop(),
                  backKey: const ValueKey('quiz-back'),
                ),
                const SizedBox(height: 16),
                if (quiz.submitted) ..._results(quiz) else ..._questions(quiz),
              ],
            ),
          ),
        ),
      );
    }
    return Container(color: Paper.surface, child: body);
  }

  // ------------------------------------------------------------- taking it

  List<Widget> _questions(Quiz quiz) {
    final left = _secondsLeft;
    return [
      Row(children: [
        Expanded(
          child: Text(
            '${quiz.questions.length} questions'
            '${quiz.isMock ? ' from every unit' : ''}. Pick one answer, or write a sentence or two.',
            style: sans(13, color: Paper.muted),
          ),
        ),
        if (left != null)
          Container(
            key: const ValueKey('quiz-clock'),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              color: left <= 60 ? Paper.warnSoft : Paper.accentSoft,
              borderRadius: BorderRadius.circular(100),
            ),
            child: Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(Icons.timer_outlined, size: 16, color: left <= 60 ? Paper.danger : Paper.accent),
              const SizedBox(width: 6),
              Text(clockLabel(left),
                  style: sans(14, color: left <= 60 ? Paper.danger : Paper.accentDark, weight: FontWeight.w700)),
            ]),
          ),
      ]),
      const SizedBox(height: 18),
      for (final (i, q) in quiz.questions.indexed) _questionCard(i, q, quiz.isMock),
      const SizedBox(height: 8),
      Row(children: [
        FilledButton(
          key: const ValueKey('quiz-submit'),
          onPressed: _submitting ? null : () => _submit(),
          style: FilledButton.styleFrom(
            backgroundColor: Paper.accent,
            padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 16),
          ),
          child: Text(_submitting ? 'Marking…' : 'Hand in'),
        ),
        const SizedBox(width: 14),
        Text(
          '${quiz.questions.length - _unanswered} of ${quiz.questions.length} answered',
          style: sans(12.5, color: Paper.muted),
        ),
      ]),
    ];
  }

  Widget _questionCard(int i, Question q, bool showUnit) {
    return Container(
      key: ValueKey('quiz-question-${q.id}'),
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (showUnit) ...[
            Text(q.unitTitle.toUpperCase(), style: mono(9.5)),
            const SizedBox(height: 4),
          ],
          _numbered(i, q.prompt),
          const SizedBox(height: 12),
          if (q.isChoice)
            for (final (c, text) in q.choices.indexed) _choiceTile(q, c, text)
          else
            TextField(
              key: ValueKey('quiz-typed-${q.id}'),
              controller: _controllerFor(q.id),
              enabled: !_submitting,
              minLines: 2,
              maxLines: 5,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                hintText: 'Your answer…',
                filled: true,
                fillColor: Paper.sliver,
                border: OutlineInputBorder(
                  borderRadius: BorderRadius.circular(10),
                  borderSide: BorderSide(color: Paper.border),
                ),
              ),
            ),
          if (!q.isChoice) _workingPicture(q.id),
        ],
      ),
    );
  }

  /// "3. " and the question, its maths typeset (one line of inline text: the
  /// number must not read as a list).
  Widget _numbered(int i, String prompt) {
    final style = sans(15, weight: FontWeight.w600, height: 1.4);
    return Text.rich(TextSpan(children: [TextSpan(text: '${i + 1}. ', style: style), ...inlineSpans(prompt, style)]));
  }

  Widget _workingPicture(String questionId) {
    final picture = _pictures[questionId];
    final reading = _pictureReading.contains(questionId);
    final problem = _pictureProblem[questionId];
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Row(children: [
        if (picture != null) ...[
          ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: Image.memory(picture.bytes, width: 56, height: 56, fit: BoxFit.cover),
          ),
          const SizedBox(width: 10),
          Expanded(child: Text('Your working is attached', style: sans(12.5, color: Paper.muted))),
          IconButton(
            key: ValueKey('quiz-picture-remove-$questionId'),
            tooltip: 'Remove picture',
            onPressed: _submitting ? null : () => setState(() => _pictures.remove(questionId)),
            icon: Icon(Icons.close_rounded, size: 18, color: Paper.faint),
          ),
        ] else ...[
          TextButton.icon(
            key: ValueKey('quiz-picture-$questionId'),
            onPressed: _submitting || reading ? null : () => _attachPicture(questionId),
            icon: reading
                ? SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Paper.accent))
                : const Icon(Icons.add_photo_alternate_outlined, size: 18),
            label: Text(reading ? 'Reading your picture…' : 'Add a photo of your working'),
          ),
          if (problem != null)
            Expanded(child: Text(problem, style: sans(12, color: Paper.danger))),
        ],
      ]),
    );
  }

  Widget _choiceTile(Question q, int index, String text) {
    final picked = _answers[q.id] == '$index';
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: InkWell(
        key: ValueKey('quiz-choice-${q.id}-$index'),
        borderRadius: BorderRadius.circular(10),
        onTap: _submitting ? null : () => setState(() => _answers[q.id] = '$index'),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
          decoration: BoxDecoration(
            color: picked ? Paper.accentSoft : Paper.sliver,
            border: Border.all(color: picked ? Paper.accent : Paper.border, width: picked ? 1.5 : 1),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Row(children: [
            Icon(picked ? Icons.radio_button_checked_rounded : Icons.radio_button_unchecked_rounded,
                size: 18, color: picked ? Paper.accent : Paper.faint),
            const SizedBox(width: 10),
            Expanded(child: RichMessageText(text, selectable: false, style: sans(14, height: 1.35))),
          ]),
        ),
      ),
    );
  }

  // ------------------------------------------------------------- results

  List<Widget> _results(Quiz quiz) {
    final score = quiz.score;
    final percent = score?.percent;
    return [
      Container(
        key: const ValueKey('quiz-score'),
        padding: const EdgeInsets.all(22),
        decoration: BoxDecoration(
          color: Paper.card,
          border: Border.all(color: Paper.accent, width: 1.5),
          borderRadius: BorderRadius.circular(16),
        ),
        child: Row(children: [
          Text(percent == null ? '–' : '$percent%',
              style: serif(40, color: (percent ?? 0) >= 70 ? Paper.olive : Paper.accent)),
          const SizedBox(width: 18),
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(
                score == null ? '' : '${score.correct} of ${score.total} correct',
                style: sans(16, weight: FontWeight.w600),
              ),
              if (score != null && score.graded < score.total)
                Text('${score.total - score.graded} couldn\'t be graded and aren\'t counted.',
                    style: sans(12.5, color: Paper.muted)),
              if (quiz.overTime)
                Text('Handed in after the time ran out.', style: sans(12.5, color: Paper.warn)),
            ]),
          ),
        ]),
      ),
      const SizedBox(height: 20),
      for (final (i, r) in quiz.results.indexed) _resultCard(i, r, quiz.isMock),
      const SizedBox(height: 8),
      FilledButton(
        key: const ValueKey('quiz-done'),
        onPressed: () => Navigator.of(context).maybePop(),
        style: FilledButton.styleFrom(backgroundColor: Paper.accent),
        child: const Text('Back to the exam'),
      ),
    ];
  }

  Widget _resultCard(int i, QuestionResult r, bool showUnit) {
    final (icon, color) = switch (r.correct) {
      true => (Icons.check_circle_rounded, Paper.olive),
      false => (Icons.cancel_rounded, Paper.danger),
      null => (Icons.help_outline_rounded, Paper.warn),
    };
    final answered = r.responseText.trim();
    return Container(
      key: ValueKey('quiz-result-${r.question.id}'),
      margin: const EdgeInsets.only(bottom: 14),
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: color, size: 22),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (showUnit) ...[
                  Text(r.question.unitTitle.toUpperCase(), style: mono(9.5)),
                  const SizedBox(height: 4),
                ],
                _numbered(i, r.question.prompt),
                const SizedBox(height: 10),
                _line('Your answer', answered.isEmpty ? 'No answer' : answered,
                    color: r.correct == true ? Paper.olive : Paper.body),
                if (r.correct != true) _line('Answer', r.correctAnswer, color: Paper.ink),
                if (r.feedback.isNotEmpty) _line('Feedback', r.feedback),
                if (r.explanation.isNotEmpty) _line('Why', r.explanation),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _line(String label, String text, {Color? color}) => Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 92, child: Text(label, style: sans(12.5, color: Paper.faint, weight: FontWeight.w600))),
          Expanded(child: () {
            // a handed-in photo of their working shows as what it showed
            final (words, reading) = splitPicture(text);
            final style = sans(13.5, color: color ?? Paper.body, height: 1.45);
            return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              if (words.trim().isNotEmpty) RichMessageText(words.trim(), style: style),
              if (reading != null) ...[const SizedBox(height: 4), PictureNote(reading: reading)],
            ]);
          }()),
        ]),
      );
}
