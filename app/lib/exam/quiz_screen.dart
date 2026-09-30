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
import 'exam_stage.dart';

/// The wall clock a mock counts down against (not the number of timer ticks,
/// which a browser throttles in a background tab). Replaceable in tests.
DateTime Function() quizClock = DateTime.now;

/// Taking a unit quiz or a mock test, then its marked results. [load]
/// starts a new sitting (or reopens one), so the questions are written while
/// [label] shows. [warmUp], for a new sitting: every chapter it covers is
/// warmed up first -- key points, formula, a worked example, the slime acting
/// it out -- and the sitting (and a mock's clock) starts only after it.
class QuizScreen extends StatefulWidget {
  const QuizScreen({super.key, required this.label, required this.load, this.warmUp});
  final String label;
  final Future<Quiz> Function() load;
  final Future<List<WarmUp>> Function()? warmUp;

  @override
  State<QuizScreen> createState() => _QuizScreenState();
}

class _QuizScreenState extends State<QuizScreen> {
  Quiz? _quiz;
  Object? _error;

  /// The warm-up before the questions: every chapter, one at a time.
  List<WarmUp>? _warm;
  int _warmAt = 0;
  bool _warmedUp = false;
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

  /// The question showing (one at a time).
  int _at = 0;

  /// Unit quiz: taps checked so far, and ones being checked.
  final Map<String, QuizCheck> _checks = {};
  final Set<String> _checking = {};

  /// Questions the stage has asked (the card then offers the choices), and
  /// each question's scene, fetched once.
  final Set<String> _asked = {};
  final Map<String, Future<List<Map<String, dynamic>>>> _scenes = {};
  Timer? _askFallback;
  final _stageKey = GlobalKey<ExamStageState>();

  ExamApi get _api => ExamApi.of(context.read<AppState>().api);

  @override
  void initState() {
    super.initState();
    _start();
  }

  @override
  void dispose() {
    _ticker?.cancel();
    _askFallback?.cancel();
    for (final c in _typed.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _start() async {
    setState(() => _error = null);
    try {
      final warmUp = widget.warmUp;
      if (warmUp != null && !_warmedUp) {
        final warm = await warmUp();
        if (mounted) setState(() => _warm = warm);
        return;
      }
      _show(await widget.load());
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  /// Warmed up: now the questions are written (and a mock's clock starts).
  void _begin() {
    setState(() {
      _warmedUp = true;
      _warm = null;
    });
    _start();
  }

  void _show(Quiz quiz) {
    if (!mounted) return;
    _ticker?.cancel();
    setState(() {
      _quiz = quiz;
      for (final c in quiz.checks) {
        _checks[c.questionId] = c;
        _answers[c.questionId] = c.response;
      }
      if (!quiz.submitted && _at == 0 && quiz.checks.isNotEmpty) {
        // reopened: carry on from the first question not yet answered
        final next = quiz.questions.indexWhere((q) => !_checks.containsKey(q.id));
        _at = next < 0 ? quiz.questions.length - 1 : next;
      }
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
    if (!quiz.submitted) _startAskFallback();
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
    } else if (_warm != null) {
      body = SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(32, 32, 32, 60),
        child: Align(
          alignment: Alignment.topLeft,
          child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 820), child: _warmUpView(_warm!)),
        ),
      );
    } else if (quiz == null) {
      body = Center(
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          CircularProgressIndicator(color: Paper.accent),
          const SizedBox(height: 16),
          Text(widget.warmUp != null && !_warmedUp ? 'Getting your warm-up ready…' : widget.label,
              key: const ValueKey('quiz-loading'), style: sans(14, color: Paper.body)),
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

  // ------------------------------------------------------------- warming up

  /// A chapter's warm-up: what to have fresh in mind before its questions.
  Widget _warmUpView(List<WarmUp> warm) {
    final w = warm[_warmAt];
    final last = _warmAt == warm.length - 1;
    final stageOn = context.watch<AppState>().showStagePanel;
    final pill = RoundedRectangleBorder(borderRadius: BorderRadius.circular(100));
    final heading = sans(12.5, color: Paper.faint, weight: FontWeight.w600);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      PageHeading(
        eyebrow: warm.length > 1 ? 'WARM-UP · CHAPTER ${_warmAt + 1} OF ${warm.length}' : 'WARM-UP',
        title: w.unitTitle,
        onBack: () => Navigator.of(context).maybePop(),
        backKey: const ValueKey('quiz-back'),
      ),
      const SizedBox(height: 6),
      Text('A quick refresher before the questions.', style: sans(13.5, color: Paper.muted)),
      const SizedBox(height: 16),
      if (stageOn && w.script.isNotEmpty) ...[
        WarmUpStage(key: ValueKey('warmup-stage-${w.unitId}'), script: w.script),
        const SizedBox(height: 16),
      ],
      AnimatedSwitcher(
        duration: const Duration(milliseconds: 280),
        child: Container(
          key: ValueKey('warmup-${w.unitId}'),
          width: double.infinity,
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
            color: Paper.card,
            border: Border.all(color: Paper.border),
            borderRadius: BorderRadius.circular(14),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('KEEP IN MIND', style: heading),
            const SizedBox(height: 8),
            for (final p in w.points)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Padding(
                    padding: const EdgeInsets.only(top: 3),
                    child: Icon(Icons.check_rounded, size: 16, color: Paper.accent),
                  ),
                  const SizedBox(width: 8),
                  Expanded(child: RichMessageText(p, selectable: false, style: sans(14.5, height: 1.45))),
                ]),
              ),
            if (w.formula != null) ...[
              const SizedBox(height: 6),
              Text('FORMULA', style: heading),
              const SizedBox(height: 6),
              Container(
                key: const ValueKey('warmup-formula'),
                width: double.infinity,
                padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 12),
                decoration: BoxDecoration(color: Paper.accentSoft, borderRadius: BorderRadius.circular(10)),
                child: RichMessageText('\$\$${w.formula}\$\$', selectable: false, style: sans(15)),
              ),
            ],
            if (w.example != null) ...[
              const SizedBox(height: 12),
              Text('WORKED EXAMPLE', style: heading),
              const SizedBox(height: 6),
              RichMessageText(w.example!, selectable: false, style: sans(14, height: 1.5, color: Paper.body)),
            ],
          ]),
        ),
      ),
      const SizedBox(height: 18),
      Row(children: [
        if (_warmAt > 0) ...[
          OutlinedButton(
            key: const ValueKey('warmup-prev'),
            onPressed: () => setState(() => _warmAt -= 1),
            style: OutlinedButton.styleFrom(shape: pill),
            child: const Text('Previous chapter'),
          ),
          const SizedBox(width: 10),
        ],
        if (!last)
          FilledButton(
            key: const ValueKey('warmup-next'),
            onPressed: () => setState(() => _warmAt += 1),
            style: FilledButton.styleFrom(backgroundColor: Paper.accent, shape: pill),
            child: const Text('Next chapter'),
          )
        else
          FilledButton.icon(
            key: const ValueKey('warmup-start'),
            onPressed: _begin,
            icon: const Icon(Icons.play_arrow_rounded),
            label: Text(warm.length > 1 ? 'Warmed up -- start the test' : 'Warmed up -- start the quiz'),
            style: FilledButton.styleFrom(
              backgroundColor: Paper.accent,
              shape: pill,
              padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
            ),
          ),
      ]),
    ]);
  }

  // ------------------------------------------------------------- taking it

  /// One question at a time: focused, like the real thing. A unit quiz checks
  /// each tap there and then (the answer and why, the slime reacting); a mock
  /// test only records it -- nothing is revealed until it is handed in.
  List<Widget> _questions(Quiz quiz) {
    final left = _secondsLeft;
    final q = quiz.questions[_at];
    final stageOn = context.watch<AppState>().showStagePanel;
    final label = skillLabel(q);
    return [
      Row(children: [
        Text('Question ${_at + 1} of ${quiz.questions.length}',
            key: const ValueKey('quiz-position'), style: sans(13.5, weight: FontWeight.w600)),
        if (label.isNotEmpty) ...[
          const SizedBox(width: 10),
          Container(
            key: const ValueKey('quiz-skill'),
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(color: Paper.sliver, borderRadius: BorderRadius.circular(100)),
            child: Text(label, style: mono(9.5, weight: FontWeight.w700)),
          ),
        ],
        const Spacer(),
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
      const SizedBox(height: 10),
      _progressDots(quiz),
      const SizedBox(height: 14),
      if (stageOn) ...[
        ExamStage(
          key: _stageKey,
          question: q,
          loadScene: (id) => _scenes.putIfAbsent(id, () => _api.scene(id)),
          onPick: _pick,
          onAsked: (id) {
            if (mounted && !_asked.contains(id)) setState(() => _asked.add(id));
          },
        ),
        const SizedBox(height: 14),
      ],
      AnimatedSwitcher(
        duration: const Duration(milliseconds: 280),
        transitionBuilder: (child, a) => FadeTransition(
          opacity: a,
          child: SlideTransition(
            position: Tween(begin: const Offset(0.04, 0), end: Offset.zero).animate(a),
            child: child,
          ),
        ),
        child: _questionCard(_at, q, quiz, stageOn),
      ),
      const SizedBox(height: 4),
      _navigation(quiz, q),
    ];
  }

  /// Where they are: one dot per question -- answered, checked right or
  /// wrong -- and a tap goes back to one.
  Widget _progressDots(Quiz quiz) {
    return Wrap(spacing: 6, runSpacing: 6, children: [
      for (final (i, q) in quiz.questions.indexed)
        InkWell(
          key: ValueKey('quiz-dot-$i'),
          borderRadius: BorderRadius.circular(100),
          onTap: _submitting ? null : () => _go(i),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 200),
            width: i == _at ? 26 : 12,
            height: 12,
            decoration: BoxDecoration(
              color: switch (_checks[q.id]?.correct) {
                true => Paper.olive,
                false => Paper.danger,
                null => _answerFor(q.id).isNotEmpty ? Paper.accent : Paper.border,
              },
              borderRadius: BorderRadius.circular(100),
            ),
          ),
        ),
    ]);
  }

  Widget _navigation(Quiz quiz, Question q) {
    final last = _at == quiz.questions.length - 1;
    final pill = RoundedRectangleBorder(borderRadius: BorderRadius.circular(100));
    final practice = !quiz.isMock;
    final settled = !practice || !q.isChoice || _checks.containsKey(q.id);
    return Row(children: [
      if (_at > 0) ...[
        OutlinedButton(
          key: const ValueKey('quiz-prev'),
          onPressed: _submitting ? null : () => _go(_at - 1),
          style: OutlinedButton.styleFrom(shape: pill),
          child: const Text('Previous'),
        ),
        const SizedBox(width: 10),
      ],
      if (!last)
        FilledButton(
          key: const ValueKey('quiz-next'),
          onPressed: _submitting || !settled ? null : () => _go(_at + 1),
          style: FilledButton.styleFrom(backgroundColor: Paper.accent, shape: pill),
          child: Text(practice ? 'Next question' : 'Next'),
        ),
      if (last || quiz.isMock) ...[
        if (!last) const SizedBox(width: 10),
        FilledButton(
          key: const ValueKey('quiz-submit'),
          onPressed: _submitting ? null : () => _submit(),
          style: FilledButton.styleFrom(
            backgroundColor: last ? Paper.accent : Paper.ink,
            shape: pill,
            padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 14),
          ),
          child: Text(_submitting ? 'Marking…' : (practice ? 'See your results' : 'Hand in')),
        ),
      ],
      const SizedBox(width: 14),
      Flexible(
        child: Text(
          '${quiz.questions.length - _unanswered} of ${quiz.questions.length} answered',
          style: sans(12.5, color: Paper.muted),
        ),
      ),
    ]);
  }

  void _go(int i) {
    final quiz = _quiz;
    if (quiz == null || i < 0 || i >= quiz.questions.length) return;
    setState(() => _at = i);
    _startAskFallback();
  }

  /// With the stage on, the card offers its choices once the slime has asked
  /// -- or after a short wait, so a slow scene never holds the question up.
  void _startAskFallback() {
    _askFallback?.cancel();
    final quiz = _quiz;
    if (quiz == null || quiz.submitted) return;
    final id = quiz.questions[_at].id;
    _askFallback = Timer(const Duration(seconds: 10), () {
      if (mounted && !_asked.contains(id)) setState(() => _asked.add(id));
    });
  }

  /// A tap, from the card or the stage. In a unit quiz it is checked at
  /// once (the first tap stands); in a mock it is only recorded.
  Future<void> _pick(Question q, int index) async {
    final quiz = _quiz;
    if (quiz == null || _submitting || quiz.submitted) return;
    if (quiz.isMock) {
      if (_answers[q.id] == '$index') return;
      setState(() => _answers[q.id] = '$index');
      _stageKey.currentState?.answered(index);
      _stageKey.currentState?.react(correct: null);
      return;
    }
    if (_checks.containsKey(q.id) || _checking.contains(q.id)) return;
    setState(() {
      _answers[q.id] = '$index';
      _checking.add(q.id);
    });
    _stageKey.currentState?.answered(index);
    try {
      final check = await _api.check(quiz.id, q.id, '$index');
      if (!mounted) return;
      setState(() {
        _checks[q.id] = check;
        _answers[q.id] = check.response;
      });
      _stageKey.currentState?.react(correct: check.correct, rightAnswer: check.correctAnswer);
    } catch (e) {
      if (!mounted) return;
      setState(() => _answers.remove(q.id));
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text('$e')));
    } finally {
      if (mounted) setState(() => _checking.remove(q.id));
    }
  }

  Widget _questionCard(int i, Question q, Quiz quiz, bool stageOn) {
    final check = _checks[q.id];
    final waiting = stageOn && q.isChoice && !_asked.contains(q.id) && check == null && !_answers.containsKey(q.id);
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
          if (quiz.isMock) ...[
            Text(q.unitTitle.toUpperCase(), style: mono(9.5)),
            const SizedBox(height: 4),
          ],
          _numbered(i, q.prompt),
          const SizedBox(height: 12),
          if (!q.isChoice) ...[
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
            _workingPicture(q.id),
          ] else
            AnimatedCrossFade(
              duration: const Duration(milliseconds: 300),
              crossFadeState: waiting ? CrossFadeState.showFirst : CrossFadeState.showSecond,
              firstChild: Row(children: [
                Icon(Icons.auto_awesome_rounded, size: 15, color: Paper.accent),
                const SizedBox(width: 6),
                Text('Watch the stage…', key: const ValueKey('quiz-watch-stage'), style: sans(12.5, color: Paper.muted)),
              ]),
              secondChild: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [for (final (c, text) in q.choices.indexed) _choiceTile(q, c, text, check)],
              ),
            ),
          if (check != null) _feedback(check),
          if (_checking.contains(q.id))
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text('Checking…', style: sans(12.5, color: Paper.muted)),
            ),
        ],
      ),
    );
  }

  /// A checked tap: right or not, and why -- the moment it is answered.
  Widget _feedback(QuizCheck check) {
    return Container(
      key: const ValueKey('quiz-feedback'),
      margin: const EdgeInsets.only(top: 6),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: check.correct ? Paper.oliveSoft : Paper.accentSoft,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(check.correct ? Icons.check_circle_rounded : Icons.lightbulb_outline_rounded,
              size: 18, color: check.correct ? Paper.olive : Paper.accent),
          const SizedBox(width: 8),
          Text(check.correct ? 'Correct' : 'Not quite',
              style: sans(14, weight: FontWeight.w700, color: check.correct ? Paper.olive : Paper.accentDark)),
        ]),
        if (!check.correct) ...[
          const SizedBox(height: 6),
          RichMessageText('Answer: ${check.correctAnswer}', selectable: false, style: sans(13.5, height: 1.4)),
        ],
        if (check.explanation.isNotEmpty) ...[
          const SizedBox(height: 4),
          RichMessageText(check.explanation, selectable: false, style: sans(13, height: 1.45, color: Paper.body)),
        ],
      ]),
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

  Widget _choiceTile(Question q, int index, String text, QuizCheck? check) {
    final picked = _answers[q.id] == '$index';
    final right = check != null && check.correctIndex == index;
    final wrongPick = check != null && picked && !check.correct;
    final locked = _submitting || check != null || _checking.contains(q.id);
    final Color border = right ? Paper.olive : (wrongPick ? Paper.danger : (picked ? Paper.accent : Paper.border));
    final Color fill = right ? Paper.oliveSoft : (wrongPick ? Paper.dangerSoft : (picked ? Paper.accentSoft : Paper.sliver));
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: InkWell(
        key: ValueKey('quiz-choice-${q.id}-$index'),
        borderRadius: BorderRadius.circular(10),
        onTap: locked ? null : () => _pick(q, index),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
          decoration: BoxDecoration(
            color: fill,
            border: Border.all(color: border, width: picked || right ? 1.5 : 1),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Row(children: [
            Icon(
              right
                  ? Icons.check_circle_rounded
                  : (wrongPick
                      ? Icons.cancel_rounded
                      : (picked ? Icons.radio_button_checked_rounded : Icons.radio_button_unchecked_rounded)),
              size: 18,
              color: right ? Paper.olive : (wrongPick ? Paper.danger : (picked ? Paper.accent : Paper.faint)),
            ),
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
      if (quiz.skills.isNotEmpty) ...[
        const SizedBox(height: 14),
        _skills(quiz.skills),
      ],
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

  /// What they can DO, skill by skill: recall, understand, apply, analyse.
  Widget _skills(List<SkillScore> skills) {
    String name(String s) => '${s[0].toUpperCase()}${s.substring(1)}';
    return Container(
      key: const ValueKey('quiz-skills'),
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('What this tested', style: sans(13.5, weight: FontWeight.w700)),
        const SizedBox(height: 10),
        for (final s in skills)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(children: [
              SizedBox(width: 92, child: Text(name(s.skill), style: sans(13, color: Paper.body))),
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(4),
                  child: TweenAnimationBuilder<double>(
                    tween: Tween(end: s.total == 0 ? 0 : s.correct / s.total),
                    duration: const Duration(milliseconds: 700),
                    curve: Curves.easeOutCubic,
                    builder: (context, v, _) => LinearProgressIndicator(
                      value: v,
                      minHeight: 8,
                      color: s.correct * 2 >= s.total ? Paper.olive : Paper.accent,
                      backgroundColor: Paper.border,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 10),
              Text('${s.correct}/${s.total}', key: ValueKey('quiz-skill-${s.skill}'), style: sans(12.5, weight: FontWeight.w600)),
            ]),
          ),
      ]),
    );
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
