// Exam-preparation data shapes (server: src/versa/exams.py).

int _int(Object? raw, [int fallback = 0]) => (raw as num?)?.toInt() ?? fallback;
int? _intOrNull(Object? raw) => (raw as num?)?.toInt();
DateTime? _date(Object? raw) => raw == null ? null : DateTime.tryParse(raw as String);

/// Correct out of the answers that could be graded; [percent] is null when
/// nothing could be.
class Score {
  const Score({required this.correct, required this.graded, required this.total, this.percent});
  final int correct;
  final int graded;
  final int total;
  final int? percent;

  factory Score.fromJson(Map<String, dynamic> j) => Score(
        correct: _int(j['correct']),
        graded: _int(j['graded']),
        total: _int(j['total']),
        percent: _intOrNull(j['percent']),
      );

  static Score? maybe(Object? raw) => raw == null ? null : Score.fromJson(raw as Map<String, dynamic>);
}

class ExamSummary {
  const ExamSummary({
    required this.id,
    required this.title,
    required this.sourceKind,
    required this.unitCount,
    required this.quizzesTaken,
    this.examDate,
    this.daysLeft,
  });

  final String id;
  final String title;
  final String sourceKind; // search | pdf | link | course
  final int unitCount;
  final int quizzesTaken;
  final DateTime? examDate;
  final int? daysLeft;

  factory ExamSummary.fromJson(Map<String, dynamic> j) => ExamSummary(
        id: j['id'] as String,
        title: j['title'] as String? ?? '',
        sourceKind: j['source_kind'] as String? ?? 'search',
        unitCount: _int(j['unit_count']),
        quizzesTaken: _int(j['quizzes_taken']),
        examDate: _date(j['exam_date']),
        daysLeft: _intOrNull(j['days_left']),
      );
}

class ExamUnit {
  const ExamUnit({
    required this.id,
    required this.position,
    required this.title,
    required this.summary,
    required this.quizzesTaken,
    this.lastPercent,
    this.bestPercent,
  });

  final String id;
  final int position;
  final String title;
  final String summary;
  final int quizzesTaken;
  final int? lastPercent;
  final int? bestPercent;

  factory ExamUnit.fromJson(Map<String, dynamic> j) => ExamUnit(
        id: j['id'] as String,
        position: _int(j['position']),
        title: j['title'] as String? ?? '',
        summary: j['summary'] as String? ?? '',
        quizzesTaken: _int(j['quizzes_taken']),
        lastPercent: _intOrNull(j['last_percent']),
        bestPercent: _intOrNull(j['best_percent']),
      );
}

class MockSummary {
  const MockSummary({
    required this.quizId,
    required this.submitted,
    required this.overTime,
    this.createdAt,
    this.score,
  });

  final String quizId;
  final bool submitted;
  final bool overTime;
  final DateTime? createdAt;
  final Score? score;

  factory MockSummary.fromJson(Map<String, dynamic> j) => MockSummary(
        quizId: j['quiz_id'] as String,
        submitted: j['submitted'] as bool? ?? false,
        overTime: j['over_time'] as bool? ?? false,
        createdAt: _date(j['created_at']),
        score: Score.maybe(j['score']),
      );
}

class Exam {
  const Exam({required this.summary, required this.units, required this.mocks});
  final ExamSummary summary;
  final List<ExamUnit> units;
  final List<MockSummary> mocks;

  String get id => summary.id;

  factory Exam.fromJson(Map<String, dynamic> j) => Exam(
        summary: ExamSummary.fromJson(j),
        units: [for (final u in (j['units'] as List? ?? const [])) ExamUnit.fromJson(u as Map<String, dynamic>)],
        mocks: [for (final m in (j['mocks'] as List? ?? const [])) MockSummary.fromJson(m as Map<String, dynamic>)],
      );
}

/// One question. [choices] is empty for a short-answer question.
class Question {
  const Question({
    required this.id,
    required this.position,
    required this.unitTitle,
    required this.prompt,
    required this.choices,
  });

  final String id;
  final int position;
  final String unitTitle;
  final String prompt;
  final List<String> choices;

  bool get isChoice => choices.isNotEmpty;

  factory Question.fromJson(Map<String, dynamic> j) => Question(
        id: j['id'] as String,
        position: _int(j['position']),
        unitTitle: j['unit_title'] as String? ?? '',
        prompt: j['prompt'] as String? ?? '',
        choices: [for (final c in (j['choices'] as List? ?? const [])) c.toString()],
      );
}

/// A handed-in question: what was answered, whether it was right (null =
/// couldn't be graded), the correct answer and why.
class QuestionResult {
  const QuestionResult({
    required this.question,
    required this.response,
    required this.correct,
    required this.correctAnswer,
    required this.explanation,
    required this.feedback,
  });

  final Question question;
  final String response;
  final bool? correct;
  final String correctAnswer;
  final String explanation;
  final String feedback;

  /// For a choice question, the text of the choice that was picked.
  String get responseText {
    if (!question.isChoice) return response;
    final i = int.tryParse(response);
    return i != null && i >= 0 && i < question.choices.length ? question.choices[i] : '';
  }

  factory QuestionResult.fromJson(Map<String, dynamic> j) => QuestionResult(
        question: Question.fromJson(j),
        response: j['response'] as String? ?? '',
        correct: j['correct'] as bool?,
        correctAnswer: j['correct_answer'] as String? ?? '',
        explanation: j['explanation'] as String? ?? '',
        feedback: j['feedback'] as String? ?? '',
      );
}

class Quiz {
  const Quiz({
    required this.id,
    required this.examId,
    required this.examTitle,
    required this.isMock,
    required this.questions,
    required this.submitted,
    this.unitTitle,
    this.timeLimitSeconds,
    this.secondsLeft,
    this.overTime = false,
    this.score,
    this.results = const [],
  });

  final String id;
  final String examId;
  final String examTitle;
  final bool isMock;
  final String? unitTitle;
  final int? timeLimitSeconds;

  /// Seconds left on the clock when the server answered (null = untimed or
  /// already handed in).
  final int? secondsLeft;
  final List<Question> questions;
  final bool submitted;
  final bool overTime;
  final Score? score;
  final List<QuestionResult> results;

  String get title => isMock ? 'Mock test' : (unitTitle ?? 'Quiz');

  factory Quiz.fromJson(Map<String, dynamic> j) => Quiz(
        id: j['id'] as String,
        examId: j['exam_id'] as String,
        examTitle: j['exam_title'] as String? ?? '',
        isMock: j['kind'] == 'mock',
        unitTitle: j['unit_title'] as String?,
        timeLimitSeconds: _intOrNull(j['time_limit_seconds']),
        secondsLeft: _intOrNull(j['seconds_left']),
        questions: [for (final q in (j['questions'] as List? ?? const [])) Question.fromJson(q as Map<String, dynamic>)],
        submitted: j['submitted'] as bool? ?? false,
        overTime: j['over_time'] as bool? ?? false,
        score: Score.maybe(j['score']),
        results: [
          for (final r in (j['results'] as List? ?? const [])) QuestionResult.fromJson(r as Map<String, dynamic>),
        ],
      );
}

/// "in 5 days", "tomorrow", "today", "3 days ago".
String daysLeftLabel(int days) => switch (days) {
      0 => 'today',
      1 => 'tomorrow',
      -1 => 'yesterday',
      > 1 => 'in $days days',
      _ => '${-days} days ago',
    };

/// 90 -> "1:30".
String clockLabel(int seconds) {
  final s = seconds < 0 ? 0 : seconds;
  return '${s ~/ 60}:${(s % 60).toString().padLeft(2, '0')}';
}

// ------------------------------------------------------------- study plan

enum PlanKind { revise, quiz, weakest, mock }

PlanKind _planKind(Object? raw) => switch (raw) {
      'revise' => PlanKind.revise,
      'quiz' => PlanKind.quiz,
      'weakest' => PlanKind.weakest,
      _ => PlanKind.mock,
    };

DateTime _day(Object? raw) => DateTime.parse(raw as String);

/// One thing to do on one day. Quizzes and mocks are ticked by the server
/// when one is handed in ([autoDone]); anything can be ticked by hand.
class PlanItem {
  const PlanItem({
    required this.id,
    required this.day,
    required this.kind,
    required this.text,
    required this.done,
    required this.autoDone,
    this.unitId,
    this.unitTitle,
  });

  final String id;
  final DateTime day;
  final PlanKind kind;
  final String text;
  final bool done;
  final bool autoDone;

  /// The unit to quiz (for 'weakest', the weakest one right now).
  final String? unitId;
  final String? unitTitle;

  /// Something a button can start right away.
  bool get startable => !done && (kind == PlanKind.mock || (kind != PlanKind.revise && unitId != null));

  factory PlanItem.fromJson(Map<String, dynamic> j) => PlanItem(
        id: j['id'] as String,
        day: _day(j['day']),
        kind: _planKind(j['kind']),
        text: j['text'] as String? ?? '',
        done: j['done'] as bool? ?? false,
        autoDone: j['auto_done'] as bool? ?? false,
        unitId: j['unit_id'] as String?,
        unitTitle: j['unit_title'] as String?,
      );
}

class PlanDay {
  const PlanDay({required this.day, required this.items});
  final DateTime day;
  final List<PlanItem> items;

  factory PlanDay.fromJson(Map<String, dynamic> j) => PlanDay(
        day: _day(j['day']),
        items: [for (final i in (j['items'] as List? ?? const [])) PlanItem.fromJson(i as Map<String, dynamic>)],
      );
}

class StudyPlan {
  const StudyPlan({
    required this.id,
    required this.startDate,
    required this.endDate,
    required this.today,
    required this.daysLeft,
    required this.done,
    required this.total,
    required this.percent,
    required this.todayItems,
    required this.overdue,
    required this.days,
  });

  final String id;
  final DateTime startDate;
  final DateTime endDate;

  /// "Today" as the server sees it.
  final DateTime today;
  final int daysLeft;
  final int done;
  final int total;
  final int percent;
  final List<PlanItem> todayItems;
  final List<PlanItem> overdue;
  final List<PlanDay> days;

  factory StudyPlan.fromJson(Map<String, dynamic> j) => StudyPlan(
        id: j['id'] as String,
        startDate: _day(j['start_date']),
        endDate: _day(j['end_date']),
        today: _day(j['today']),
        daysLeft: _int(j['days_left']),
        done: _int(j['done']),
        total: _int(j['total']),
        percent: _int(j['percent']),
        todayItems: [
          for (final i in (j['today_items'] as List? ?? const [])) PlanItem.fromJson(i as Map<String, dynamic>),
        ],
        overdue: [for (final i in (j['overdue'] as List? ?? const [])) PlanItem.fromJson(i as Map<String, dynamic>)],
        days: [for (final d in (j['days'] as List? ?? const [])) PlanDay.fromJson(d as Map<String, dynamic>)],
      );
}
