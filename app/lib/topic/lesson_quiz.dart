import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../chat_controller.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/rich_text.dart';
import '../widgets/typing_dots.dart';
import 'topic_api.dart';
import 'topic_models.dart';

enum QuizPhase { idle, loading, asking, checking, answered, failed }

/// A lesson taught point by point (src/versa/topics.py): after the tutor
/// explains the current point, a tap-to-answer quiz or puzzle on it --
/// never something to type. A right tap completes the point; the learner
/// types only when they choose to.
///
/// One quiz, shown in two places that stay in step: the chat's card
/// ([LessonQuizCard]) and, when the stage is showing, the slime acting out a
/// short lead-in that ends with it asking the same question
/// (widgets/stage_panel.dart). A tap in either answers both. The card waits
/// for the slime to get to its question before offering the choices, so
/// nothing jumps ahead of the animation.
class LessonQuizController extends ChangeNotifier {
  LessonQuizController({required this.api, required this.lesson, required this.chat, required this.onProgress}) {
    chat.addListener(_onChat);
    _onChat();
  }

  final TopicApi api;
  final Lesson lesson;
  final ChatController chat;

  /// A right tap completed the point: the same payload as a chat `progress`
  /// frame.
  final void Function(Map<String, dynamic> progress) onProgress;

  QuizPhase phase = QuizPhase.idle;
  LessonQuiz? quiz;
  QuizResult? result;
  String? picked;
  String? error;

  /// The stage is showing and will act the quiz out ([stageReady] once its
  /// question is up). Set by the stage panel.
  bool stageAttached = false;
  bool _stageAsked = false;
  Timer? _fallback;

  /// The last answered turn a quiz was set after, so each explanation gets one.
  int _quizzedTurn = -1;

  /// A turn ran while this lesson was open. Answers replayed from history
  /// (reopening a lesson) never set a quiz by themselves: the last point
  /// explained there may already be done -- the card offers a way on instead.
  bool _sawTurn = false;

  /// The turn under way explains the lesson's current point -- the lesson's
  /// own start, Continue or Explain again. Only those are followed by a quiz:
  /// a question the learner typed or a direction they took is theirs to
  /// steer (answered like any Sandbox turn), and may be about something else
  /// entirely, so it is followed by Continue / Quick check instead.
  bool _explaining = true;
  bool _disposed = false;

  /// Nothing is being asked and the lesson has more to cover: after reopening
  /// a lesson, or moving on while nothing was set.
  bool get canResume =>
      phase == QuizPhase.idle &&
      lesson.byPoints &&
      lesson.currentTask != null &&
      chat.messages.isNotEmpty &&
      chat.status == ChatStatus.ready;

  /// Whether the card should offer the choices now: at once without the
  /// stage; with it, once the slime has asked (or after a short wait, so a
  /// long scene never holds the lesson up).
  bool get choicesShown => phase != QuizPhase.asking || !stageAttached || _stageAsked;

  /// The stage put the quiz's question up.
  void stageAsked() {
    if (_stageAsked) return;
    _stageAsked = true;
    _notify();
  }

  int get pointNumber {
    final q = quiz;
    final i = q == null ? -1 : lesson.tasks.indexWhere((t) => t.id == q.taskId);
    return i < 0 ? 0 : i + 1;
  }

  /// A tutor answer just finished: set the quiz on the current point, once
  /// per answer, unless one on that point is still waiting for a tap.
  void _onChat() {
    if (chat.busy) _sawTurn = true;
    if (!_sawTurn && chat.messages.isNotEmpty) _explaining = false; // a reopened lesson's history
    if (!_sawTurn || !lesson.byPoints || chat.status != ChatStatus.ready || chat.messages.isEmpty) return;
    final last = chat.messages.last;
    final turn = last.turnIndex;
    if (last.role != Role.tutor || last.pending || last.streaming || last.hasOptions || turn == null) return;
    if (turn <= _quizzedTurn) return;
    _quizzedTurn = turn;
    final explained = _explaining;
    _explaining = false; // until the lesson asks for the next explanation
    if (!explained) {
      _notify(); // the card offers Continue / Quick check
      return;
    }
    final current = lesson.currentTask;
    if (current == null) return;
    if (quiz?.taskId == current.id && (phase == QuizPhase.asking || phase == QuizPhase.loading)) return;
    next();
  }

  /// A (new) quiz on the current point -- also "try another".
  Future<void> next() async {
    if (lesson.currentTask == null || phase == QuizPhase.loading) return;
    phase = QuizPhase.loading;
    quiz = null;
    result = null;
    picked = null;
    error = null;
    _stageAsked = false;
    _fallback?.cancel();
    _notify();
    try {
      final q = await api.quiz(lesson.id);
      if (_disposed) return;
      quiz = q;
      phase = QuizPhase.asking;
      _fallback = Timer(const Duration(seconds: 12), stageAsked);
    } catch (e) {
      if (_disposed) return;
      phase = QuizPhase.failed;
      error = e is ApiException ? e.message : "Couldn't set a question.";
    }
    _notify();
  }

  /// The tap -- from the card or the stage. Graded by the server.
  Future<void> answer(String choiceId) async {
    final q = quiz;
    if (q == null || phase != QuizPhase.asking) return;
    phase = QuizPhase.checking;
    picked = choiceId;
    _stageAsked = true;
    _notify();
    try {
      final r = await api.answerQuiz(lesson.id, q.activityId, choiceId);
      if (_disposed) return;
      result = r;
      phase = QuizPhase.answered;
      final progress = r.progress;
      if (progress != null) onProgress(progress);
    } catch (e) {
      if (_disposed) return;
      phase = QuizPhase.asking;
      picked = null;
      error = e is ApiException ? e.message : "Couldn't check that -- try again.";
    }
    _notify();
  }

  /// On to the next point (a tap, not typing).
  void carryOn() => _say('Continue.');

  /// The point once more, explained another way.
  void explainAgain() => _say('Could you explain that point again, another way?');

  void _say(String text) {
    if (!chat.canSend) return;
    _explaining = true;
    phase = QuizPhase.idle;
    quiz = null;
    result = null;
    picked = null;
    _notify();
    chat.send(text);
  }

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _fallback?.cancel();
    chat.removeListener(_onChat);
    super.dispose();
  }
}

/// The quiz in the chat: slides in under the explanation, offers the
/// choices, then shows how the tap went and the way on.
class LessonQuizCard extends StatelessWidget {
  const LessonQuizCard({super.key, required this.controller, this.onBackToCourse});
  final LessonQuizController controller;
  final VoidCallback? onBackToCourse;

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: Listenable.merge([controller, controller.chat]),
      builder: (context, _) => AnimatedSwitcher(
        duration: const Duration(milliseconds: 380),
        switchInCurve: Curves.easeOutCubic,
        switchOutCurve: Curves.easeInCubic,
        transitionBuilder: (child, a) => FadeTransition(
          opacity: a,
          child: SizeTransition(sizeFactor: a, child: child),
        ),
        child: _body(context),
      ),
    );
  }

  Widget _body(BuildContext context) {
    final c = controller;
    switch (c.phase) {
      case QuizPhase.idle when c.canResume:
        final pill = RoundedRectangleBorder(borderRadius: BorderRadius.circular(100));
        return _Frame(
          key: const ValueKey('quiz-resume'),
          label: 'BACK TO THE LESSON',
          child: Wrap(spacing: 8, runSpacing: 8, children: [
            FilledButton.icon(
              key: const ValueKey('quiz-resume-continue'),
              onPressed: c.carryOn,
              icon: const Icon(Icons.play_arrow_rounded, size: 18),
              label: const Text('Continue'),
              style: FilledButton.styleFrom(backgroundColor: Paper.accent, shape: pill),
            ),
            OutlinedButton(
              key: const ValueKey('quiz-resume-check'),
              onPressed: c.next,
              style: OutlinedButton.styleFrom(shape: pill),
              child: const Text('Quick check first'),
            ),
          ]),
        );
      case QuizPhase.idle:
        return const SizedBox.shrink(key: ValueKey('quiz-none'));
      case QuizPhase.loading:
        return _Frame(
          key: const ValueKey('quiz-loading'),
          label: 'QUICK CHECK',
          child: Row(children: [
            const TypingDots(),
            const SizedBox(width: 10),
            Text('Setting a quick check on that…', style: sans(13, color: Paper.muted)),
          ]),
        );
      case QuizPhase.failed:
        return _Frame(
          key: const ValueKey('quiz-failed'),
          label: 'QUICK CHECK',
          child: Row(children: [
            Expanded(child: Text(c.error ?? "Couldn't set a question.", style: sans(13, color: Paper.muted))),
            TextButton(key: const ValueKey('quiz-retry'), onPressed: c.next, child: const Text('Try again')),
          ]),
        );
      case QuizPhase.asking || QuizPhase.checking || QuizPhase.answered:
        return _question(context);
    }
  }

  Widget _question(BuildContext context) {
    final c = controller;
    final q = c.quiz!;
    final r = c.result;
    final total = c.lesson.tasks.length;
    final label = '${q.form == 'puzzle' ? 'PUZZLE' : 'QUICK CHECK'}'
        '${c.pointNumber > 0 ? ' · POINT ${c.pointNumber} OF $total' : ''}';
    return _Frame(
      key: ValueKey('quiz-${q.activityId}'),
      label: label,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          RichMessageText(q.question, style: sans(14.5, height: 1.45, weight: FontWeight.w600)),
          const SizedBox(height: 10),
          AnimatedCrossFade(
            duration: const Duration(milliseconds: 300),
            crossFadeState: c.choicesShown ? CrossFadeState.showSecond : CrossFadeState.showFirst,
            firstChild: Row(children: [
              Icon(Icons.auto_awesome_rounded, size: 15, color: Paper.accent),
              const SizedBox(width: 6),
              Text('Watch the stage…', style: sans(12.5, color: Paper.muted)),
            ]),
            secondChild: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [for (final choice in q.choices) _choice(choice)],
            ),
          ),
          if (c.error != null && c.phase == QuizPhase.asking) ...[
            const SizedBox(height: 6),
            Text(c.error!, style: sans(12, color: Paper.danger)),
          ],
          if (r != null) ...[
            const SizedBox(height: 6),
            Row(children: [
              Icon(r.correct ? Icons.celebration_rounded : Icons.lightbulb_outline_rounded,
                  size: 18, color: r.correct ? Paper.olive : Paper.accent),
              const SizedBox(width: 8),
              Text(r.correct ? 'Right!' : 'Not quite.',
                  key: const ValueKey('quiz-verdict'),
                  style: sans(14, weight: FontWeight.w700, color: r.correct ? Paper.olive : Paper.accentDark)),
            ]),
            if (r.explain.isNotEmpty) ...[
              const SizedBox(height: 4),
              RichMessageText(r.explain, style: sans(13, height: 1.45, color: Paper.muted)),
            ],
            const SizedBox(height: 10),
            _next(context, r),
          ],
        ],
      ),
    );
  }

  Widget _choice(QuizChoice choice) {
    final c = controller;
    final r = c.result;
    final chosen = c.picked == choice.id;
    final right = r != null && r.answer == choice.id;
    final wrongPick = r != null && chosen && !r.correct;
    final Color border = right ? Paper.olive : (wrongPick ? Paper.danger : (chosen ? Paper.accent : Paper.border));
    final Color fill = right ? Paper.oliveSoft : (wrongPick ? Paper.dangerSoft : Paper.card);
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 250),
        decoration: BoxDecoration(
          color: fill,
          border: Border.all(color: border, width: right || chosen ? 1.5 : 1),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            key: ValueKey('quiz-choice-${choice.id}'),
            borderRadius: BorderRadius.circular(12),
            onTap: c.phase == QuizPhase.asking ? () => c.answer(choice.id) : null,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
              child: Row(children: [
                // not selectable: a selection area would take the tap
                Expanded(child: RichMessageText(choice.text, selectable: false, style: sans(13.5, height: 1.35))),
                if (c.phase == QuizPhase.checking && chosen)
                  const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                if (right) Icon(Icons.check_circle_rounded, size: 18, color: Paper.olive),
                if (wrongPick) Icon(Icons.cancel_rounded, size: 18, color: Paper.danger),
              ]),
            ),
          ),
        ),
      ),
    );
  }

  Widget _next(BuildContext context, QuizResult r) {
    final c = controller;
    final lessonDone = c.lesson.currentTask == null;
    final pill = RoundedRectangleBorder(borderRadius: BorderRadius.circular(100));
    if (r.correct && lessonDone) {
      return Wrap(spacing: 8, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
        Text('Lesson complete.', style: sans(13.5, weight: FontWeight.w600)),
        if (onBackToCourse != null)
          FilledButton(
            key: const ValueKey('quiz-back-to-course'),
            onPressed: onBackToCourse,
            style: FilledButton.styleFrom(backgroundColor: Paper.accent, shape: pill),
            child: const Text('Back to the course'),
          ),
      ]);
    }
    return Wrap(spacing: 8, runSpacing: 8, children: [
      if (r.correct)
        FilledButton.icon(
          key: const ValueKey('quiz-continue'),
          onPressed: c.chat.canSend ? c.carryOn : null,
          icon: const Icon(Icons.arrow_forward_rounded, size: 18),
          label: const Text('Continue'),
          style: FilledButton.styleFrom(backgroundColor: Paper.accent, shape: pill),
        )
      else ...[
        FilledButton.icon(
          key: const ValueKey('quiz-another'),
          onPressed: c.next,
          icon: const Icon(Icons.refresh_rounded, size: 18),
          label: const Text('Try another'),
          style: FilledButton.styleFrom(backgroundColor: Paper.accent, shape: pill),
        ),
        OutlinedButton(
          key: const ValueKey('quiz-explain-again'),
          onPressed: c.chat.canSend ? c.explainAgain : null,
          style: OutlinedButton.styleFrom(shape: pill),
          child: const Text('Explain it again'),
        ),
      ],
    ]);
  }
}

class _Frame extends StatelessWidget {
  const _Frame({super.key, required this.label, required this.child});
  final String label;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: 4, bottom: 8),
      padding: const EdgeInsets.fromLTRB(16, 12, 16, 14),
      decoration: BoxDecoration(
        color: Paper.accentSoft,
        border: Border.all(color: Paper.accentLine),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [
            Icon(Icons.touch_app_rounded, size: 15, color: Paper.accent),
            const SizedBox(width: 6),
            Text(label, style: mono(9.5, color: Paper.accentDark, weight: FontWeight.w700)),
          ]),
          const SizedBox(height: 8),
          child,
        ],
      ),
    );
  }
}
