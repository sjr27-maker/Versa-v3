// Learn-a-topic data shapes (server: src/versa/topics.py).

List<String> _strings(Object? raw) => [for (final s in (raw as List? ?? const [])) s.toString()];

int _int(Object? raw, [int fallback = 0]) => (raw as num?)?.toInt() ?? fallback;

/// One branch of an exploration tree. `children` grows as the person asks a
/// branch to "branch further"; nothing is ever removed from it.
class TopicNode {
  TopicNode({
    required this.id,
    required this.parentId,
    required this.title,
    required this.summary,
    required this.depth,
    required this.expanded,
    required this.children,
    this.canBranch = true,
    this.beyondResource = false,
  });

  final String id;
  final String? parentId;
  final String title;
  final String summary;
  final int depth;

  /// The server has generated this node's children at least once.
  bool expanded;
  final List<TopicNode> children;

  /// Asking for (more) branches here can still bring some. False for a
  /// resource branch whose section has run out, and for a "beyond the
  /// resource" extra.
  bool canBranch;

  /// Not in the person's PDF or link: one of the few related extras offered
  /// once its section had no more parts.
  final bool beyondResource;

  factory TopicNode.fromJson(Map<String, dynamic> j) => TopicNode(
        id: j['id'] as String,
        parentId: j['parent_id'] as String?,
        title: j['title'] as String? ?? '',
        summary: j['summary'] as String? ?? '',
        depth: _int(j['depth']),
        expanded: j['expanded'] as bool? ?? false,
        children: [
          for (final c in (j['children'] as List? ?? const []))
            TopicNode.fromJson(c as Map<String, dynamic>),
        ],
        canBranch: j['can_branch'] as bool? ?? true,
        beyondResource: j['beyond_resource'] as bool? ?? false,
      );
}

class TopicResource {
  const TopicResource({required this.id, required this.kind, required this.title, this.url});
  final String id;
  final String kind; // pdf | link
  final String title;
  final String? url;

  factory TopicResource.fromJson(Map<String, dynamic> j) => TopicResource(
        id: j['id'] as String? ?? '',
        kind: j['kind'] as String? ?? 'link',
        title: j['title'] as String? ?? '',
        url: j['url'] as String?,
      );
}

class Exploration {
  const Exploration({
    required this.id,
    required this.query,
    required this.sourceKind,
    required this.rootNodes,
    this.resource,
    this.personalizedBy = const [],
  });

  final String id;
  final String query;
  final String sourceKind; // search | pdf | link
  final TopicResource? resource;
  final List<TopicNode> rootNodes;

  /// Which things about the learner shaped these branches (optional; shown
  /// as a small "Shaped by" note so the personalization is visible).
  final List<String> personalizedBy;

  /// A sensible default course title.
  String get suggestedTitle {
    final r = resource;
    if (r != null && r.title.isNotEmpty) return r.title;
    return query;
  }

  factory Exploration.fromJson(Map<String, dynamic> j) => Exploration(
        id: j['id'] as String,
        query: j['query'] as String? ?? '',
        sourceKind: j['source_kind'] as String? ?? 'search',
        resource: j['resource'] == null
            ? null
            : TopicResource.fromJson(j['resource'] as Map<String, dynamic>),
        rootNodes: [
          for (final n in (j['root_nodes'] as List? ?? const []))
            TopicNode.fromJson(n as Map<String, dynamic>),
        ],
        personalizedBy: _strings(j['personalized_by']),
      );
}

class TopicSummary {
  const TopicSummary({
    required this.id,
    required this.title,
    required this.percent,
    required this.chapterCount,
    required this.lessonCount,
    required this.lessonsDone,
    this.updatedAt,
  });

  final String id;
  final String title;
  final int percent;
  final int chapterCount;
  final int lessonCount;
  final int lessonsDone;
  final DateTime? updatedAt;

  factory TopicSummary.fromJson(Map<String, dynamic> j) => TopicSummary(
        id: j['id'] as String,
        title: j['title'] as String? ?? '',
        percent: _int(j['percent']),
        chapterCount: _int(j['chapter_count']),
        lessonCount: _int(j['lesson_count']),
        lessonsDone: _int(j['lessons_done']),
        updatedAt: j['updated_at'] == null ? null : DateTime.tryParse(j['updated_at'] as String),
      );
}

enum LessonStatus { notStarted, inProgress, done }

LessonStatus parseLessonStatus(String? raw) => switch (raw) {
      'done' => LessonStatus.done,
      'in_progress' => LessonStatus.inProgress,
      _ => LessonStatus.notStarted,
    };

class LessonSummary {
  LessonSummary({
    required this.id,
    required this.title,
    required this.objective,
    required this.position,
    required this.status,
    required this.percent,
    required this.tasksTotal,
    required this.tasksDone,
  });

  final String id;
  final String title;
  final String objective;
  final int position;
  LessonStatus status;
  int percent;
  final int tasksTotal;
  int tasksDone;

  factory LessonSummary.fromJson(Map<String, dynamic> j) => LessonSummary(
        id: j['id'] as String,
        title: j['title'] as String? ?? '',
        objective: j['objective'] as String? ?? '',
        position: _int(j['position']),
        status: parseLessonStatus(j['status'] as String?),
        percent: _int(j['percent']),
        tasksTotal: _int(j['tasks_total']),
        tasksDone: _int(j['tasks_done']),
      );
}

class Chapter {
  const Chapter({
    required this.id,
    required this.title,
    required this.summary,
    required this.position,
    required this.percent,
    required this.lessons,
  });

  final String id;
  final String title;
  final String summary;
  final int position;
  final int percent;
  final List<LessonSummary> lessons;

  factory Chapter.fromJson(Map<String, dynamic> j) => Chapter(
        id: j['id'] as String,
        title: j['title'] as String? ?? '',
        summary: j['summary'] as String? ?? '',
        position: _int(j['position']),
        percent: _int(j['percent']),
        lessons: [
          for (final l in (j['lessons'] as List? ?? const []))
            LessonSummary.fromJson(l as Map<String, dynamic>),
        ],
      );
}

class Topic {
  const Topic({
    required this.id,
    required this.title,
    required this.percent,
    required this.sourceKind,
    required this.chapters,
    this.personalizedBy = const [],
  });

  final String id;
  final String title;
  final int percent;
  final String sourceKind;
  final List<Chapter> chapters;
  final List<String> personalizedBy;

  int get lessonCount => chapters.fold(0, (n, c) => n + c.lessons.length);
  int get lessonsDone =>
      chapters.fold(0, (n, c) => n + c.lessons.where((l) => l.status == LessonStatus.done).length);

  factory Topic.fromJson(Map<String, dynamic> j) => Topic(
        id: j['id'] as String,
        title: j['title'] as String? ?? '',
        percent: _int(j['percent']),
        sourceKind: j['source_kind'] as String? ?? 'search',
        chapters: [
          for (final c in (j['chapters'] as List? ?? const []))
            Chapter.fromJson(c as Map<String, dynamic>),
        ],
        personalizedBy: _strings(j['personalized_by']),
      );
}

class LessonTask {
  LessonTask({
    required this.id,
    required this.position,
    required this.kind,
    required this.description,
    required this.done,
  });

  final String id;
  final int position;
  final String kind; // point | (courses before 2026-10-01) learn | practice | apply | check
  final String description;
  bool done;

  /// A point of content: explained, then checked with a tap-to-answer quiz.
  bool get isPoint => kind == 'point';

  factory LessonTask.fromJson(Map<String, dynamic> j) => LessonTask(
        id: j['id'] as String,
        position: _int(j['position']),
        kind: j['kind'] as String? ?? 'learn',
        description: j['description'] as String? ?? '',
        done: j['done'] as bool? ?? false,
      );
}

class Lesson {
  Lesson({
    required this.id,
    required this.title,
    required this.objective,
    required this.status,
    required this.percent,
    required this.chapterId,
    required this.chapterTitle,
    required this.topicId,
    required this.topicTitle,
    required this.tasks,
    this.sessionId,
    this.chapterPercent,
    this.topicPercent,
    this.personalizedBy = const [],
  });

  final String id;
  final String title;
  final String objective;
  LessonStatus status;
  int percent;
  final String chapterId;
  final String chapterTitle;
  final String topicId;
  final String topicTitle;
  final List<LessonTask> tasks;
  final String? sessionId;
  int? chapterPercent;
  int? topicPercent;
  final List<String> personalizedBy;

  int get tasksDone => tasks.where((t) => t.done).length;

  /// Taught point by point, each checked with a quiz (courses built from
  /// 2026-10-01 on).
  bool get byPoints => tasks.isNotEmpty && tasks.every((t) => t.isPoint);

  /// The task the tutor is working toward: the first one not done yet.
  LessonTask? get currentTask {
    for (final t in tasks) {
      if (!t.done) return t;
    }
    return null;
  }

  factory Lesson.fromJson(Map<String, dynamic> j) => Lesson(
        id: j['id'] as String,
        title: j['title'] as String? ?? '',
        objective: j['objective'] as String? ?? '',
        status: parseLessonStatus(j['status'] as String?),
        percent: _int(j['percent']),
        chapterId: j['chapter_id'] as String? ?? '',
        chapterTitle: j['chapter_title'] as String? ?? '',
        topicId: j['topic_id'] as String? ?? '',
        topicTitle: j['topic_title'] as String? ?? '',
        tasks: [
          for (final t in (j['tasks'] as List? ?? const []))
            LessonTask.fromJson(t as Map<String, dynamic>),
        ]..sort((a, b) => a.position.compareTo(b.position)),
        sessionId: j['session_id'] as String?,
        chapterPercent: (j['chapter_percent'] as num?)?.toInt(),
        topicPercent: (j['topic_percent'] as num?)?.toInt(),
        personalizedBy: _strings(j['personalized_by']),
      );
}

/// One choice of a lesson quiz.
class QuizChoice {
  const QuizChoice({required this.id, required this.text});
  final String id;
  final String text;
}

/// A tap-to-answer quiz or puzzle on the point just explained
/// (POST /api/lessons/{id}/activity). [script] is the same challenge as a
/// short scene for the stage, ending in the slime asking it.
class LessonQuiz {
  LessonQuiz({
    required this.activityId,
    required this.taskId,
    required this.question,
    required this.choices,
    required this.form,
    required this.script,
    this.facts = const [],
  });

  final String activityId;
  final String taskId;
  final String question;
  final List<QuizChoice> choices;
  final String form; // quiz | puzzle
  final List<Map<String, dynamic>> script;
  final List<String> facts;

  factory LessonQuiz.fromJson(Map<String, dynamic> j) => LessonQuiz(
        activityId: j['activity_id'] as String,
        taskId: j['task_id'] as String,
        question: j['question'] as String? ?? '',
        choices: [
          for (final c in (j['choices'] as List? ?? const []))
            QuizChoice(id: (c as Map)['id'] as String, text: c['text'] as String? ?? ''),
        ],
        form: j['form'] as String? ?? 'quiz',
        script: [for (final a in (j['script'] as List? ?? const [])) (a as Map).cast<String, dynamic>()],
        facts: _strings(j['facts']),
      );
}

/// What a tap on a quiz came to. [progress] is set when it completed the
/// point (the same payload as a chat `progress` frame).
class QuizResult {
  const QuizResult({required this.correct, required this.answer, this.explain = '', this.progress});
  final bool correct;
  final String answer; // the right choice's id
  final String explain;
  final Map<String, dynamic>? progress;

  factory QuizResult.fromJson(Map<String, dynamic> j) => QuizResult(
        correct: j['correct'] as bool? ?? false,
        answer: j['answer'] as String? ?? '',
        explain: j['explain'] as String? ?? '',
        progress: (j['progress'] as Map?)?.cast<String, dynamic>(),
      );
}
