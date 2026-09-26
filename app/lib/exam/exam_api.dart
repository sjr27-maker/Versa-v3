import 'dart:convert';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../api.dart';
import 'exam_models.dart';

/// The Exam-preparation REST endpoints (src/versa/exams.py). Shares the
/// app's HTTP client, and so its test fakes.
class ExamApi {
  ExamApi(this.baseUrl, {http.Client? client}) : _http = client ?? http.Client();

  factory ExamApi.of(VersaApi api) => ExamApi(api.baseUrl, client: api.httpClient);

  final String baseUrl;
  final http.Client _http;

  // Writing a syllabus, a quiz or a whole mock (one call per unit) and
  // grading are model calls: give them longer than a plain read.
  static const _generate = Duration(seconds: 150);
  static const _read = Duration(seconds: 15);
  static const _json = {'content-type': 'application/json'};

  Uri _uri(String path) => Uri.parse('$baseUrl/api$path');

  Future<Object?> _post(String path, Object? body, String fail) async {
    final r = await _http
        .post(_uri(path), headers: _json, body: body == null ? null : jsonEncode(body))
        .timeout(_generate);
    return _decode(r, fail);
  }

  Future<Object?> _get(String path, String fail) async => _decode(await _http.get(_uri(path)).timeout(_read), fail);

  Object? _decode(http.Response r, String fail) {
    if (r.statusCode != 200) {
      var detail = '$fail (${r.statusCode})';
      try {
        final d = (jsonDecode(r.body) as Map<String, dynamic>)['detail'];
        if (d is String) detail = d;
      } catch (_) {}
      throw ApiException(detail);
    }
    return jsonDecode(utf8.decode(r.bodyBytes));
  }

  static String? _day(DateTime? d) =>
      d == null ? null : '${d.year.toString().padLeft(4, '0')}-${d.month.toString().padLeft(2, '0')}-'
          '${d.day.toString().padLeft(2, '0')}';

  Map<String, dynamic> _setup(String learnerId, String? title, DateTime? examDate) => {
        'learner_id': learnerId,
        if (title != null && title.trim().isNotEmpty) 'title': title.trim(),
        if (examDate != null) 'exam_date': _day(examDate),
      };

  Future<Exam> createFromSearch(String learnerId, String query, {String? title, DateTime? examDate}) async =>
      Exam.fromJson(await _post('/exams', {..._setup(learnerId, title, examDate), 'query': query},
          'could not build a syllabus') as Map<String, dynamic>);

  Future<Exam> createFromLink(String learnerId, String url, {String? title, DateTime? examDate}) async =>
      Exam.fromJson(await _post('/exams/from-link', {..._setup(learnerId, title, examDate), 'url': url},
          'could not read that link') as Map<String, dynamic>);

  Future<Exam> createFromCourse(String learnerId, String topicId, {String? title, DateTime? examDate}) async =>
      Exam.fromJson(await _post('/exams/from-course', {..._setup(learnerId, title, examDate), 'topic_id': topicId},
          'could not use that course') as Map<String, dynamic>);

  Future<Exam> createFromPdf(String learnerId, String filename, Uint8List bytes,
      {String? title, DateTime? examDate}) async {
    final request = http.MultipartRequest('POST', _uri('/exams/from-pdf'))
      ..fields.addAll({
        for (final e in _setup(learnerId, title, examDate).entries) e.key: e.value.toString(),
      })
      ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: filename));
    final r = await http.Response.fromStream(await _http.send(request).timeout(_generate));
    return Exam.fromJson(_decode(r, 'could not read that PDF') as Map<String, dynamic>);
  }

  Future<List<ExamSummary>> listExams(String learnerId) async => [
        for (final row in await _get('/learners/$learnerId/exams', 'could not load your exams') as List)
          ExamSummary.fromJson(row as Map<String, dynamic>),
      ];

  Future<Exam> getExam(String examId) async =>
      Exam.fromJson(await _get('/exams/$examId', 'could not load this exam') as Map<String, dynamic>);

  Future<Quiz> startUnitQuiz(String unitId) async =>
      Quiz.fromJson(await _post('/exam-units/$unitId/quiz', null, 'could not write a quiz') as Map<String, dynamic>);

  Future<Quiz> startMock(String examId) async =>
      Quiz.fromJson(await _post('/exams/$examId/mock', null, 'could not write the mock test') as Map<String, dynamic>);

  Future<Quiz> getQuiz(String quizId) async =>
      Quiz.fromJson(await _get('/exam-quizzes/$quizId', 'could not load this quiz') as Map<String, dynamic>);

  /// The exam's current study plan, or null when it has none yet.
  Future<StudyPlan?> getPlan(String examId) async {
    final raw = await _get('/exams/$examId/plan', 'could not load the study plan');
    return raw == null ? null : StudyPlan.fromJson(raw as Map<String, dynamic>);
  }

  /// A new plan from today to [examDate] (default: the exam's own date).
  /// Replaces the current plan; the old one stays on record.
  Future<StudyPlan> makePlan(String examId, {DateTime? examDate}) async => StudyPlan.fromJson(await _post(
        '/exams/$examId/plan',
        {if (examDate != null) 'end_date': _day(examDate)},
        'could not make a study plan',
      ) as Map<String, dynamic>);

  Future<StudyPlan> tickPlanItem(String itemId, bool done) async => StudyPlan.fromJson(
      await _post('/exam-plan-items/$itemId/done', {'done': done}, 'could not update the plan')
          as Map<String, dynamic>);

  /// [answers]: question id -> the choice index as a string, or the typed answer.
  Future<Quiz> submit(String quizId, Map<String, String> answers) async => Quiz.fromJson(await _post(
        '/exam-quizzes/$quizId/submit',
        {'answers': [for (final e in answers.entries) {'question_id': e.key, 'response': e.value}]},
        'could not hand this in',
      ) as Map<String, dynamic>);
}
