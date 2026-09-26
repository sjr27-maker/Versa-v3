import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import '../topic/topic_api.dart';
import '../topic/topic_models.dart';
import '../topic/topic_widgets.dart';
import '../topic/topics_home_screen.dart' show PickedPdf, topicPdfPicker;
import 'exam_api.dart';
import 'exam_models.dart';
import 'exams_root.dart';

/// Exam preparation: the person's exams, and the four ways to set one up
/// (search a subject, a PDF, a web link, or one of their courses).
class ExamsHomeScreen extends StatefulWidget {
  const ExamsHomeScreen({super.key});

  @override
  State<ExamsHomeScreen> createState() => _ExamsHomeScreenState();
}

class _ExamsHomeScreenState extends State<ExamsHomeScreen> {
  final _subject = TextEditingController();
  late Future<List<ExamSummary>> _exams;
  DateTime? _examDate;

  /// What is being built right now ("Building a syllabus for …"), or null.
  String? _busy;

  ExamApi get _api => ExamApi.of(context.read<AppState>().api);
  String get _learnerId => context.read<AppState>().learner!.id;

  @override
  void initState() {
    super.initState();
    _exams = _load();
    _subject.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _subject.dispose();
    super.dispose();
  }

  Future<List<ExamSummary>> _load() => _api.listExams(_learnerId);

  void _reload() {
    if (!mounted) return;
    setState(() {
      _exams = _load();
    });
  }

  void _toast(String text) => ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(text)));

  /// Runs one way of setting up an exam, then opens it.
  Future<void> _create(String label, Future<Exam> Function(ExamApi api, String learnerId) make) async {
    if (_busy != null) return;
    final api = _api;
    final learnerId = _learnerId;
    setState(() => _busy = label);
    try {
      final exam = await make(api, learnerId);
      if (!mounted) return;
      setState(() {
        _busy = null;
        _subject.clear();
        _examDate = null;
      });
      await pushExam(context, exam.id, initial: exam);
      _reload();
    } catch (e) {
      if (!mounted) return;
      setState(() => _busy = null);
      _toast('$e');
    }
  }

  void _startSearch() {
    final subject = _subject.text.trim();
    if (subject.isEmpty) return;
    final date = _examDate;
    _create('Building a syllabus for $subject…',
        (api, id) => api.createFromSearch(id, subject, examDate: date));
  }

  Future<void> _startPdf() async {
    final PickedPdf? picked;
    try {
      picked = await topicPdfPicker();
    } catch (e) {
      _toast('Could not open the file picker ($e)');
      return;
    }
    if (picked == null || !mounted) return;
    if (!picked.name.toLowerCase().endsWith('.pdf')) {
      _toast('That isn\'t a PDF.');
      return;
    }
    final (:name, :bytes) = picked;
    final date = _examDate;
    _create('Reading $name…', (api, id) => api.createFromPdf(id, name, bytes, examDate: date));
  }

  Future<void> _startLink() async {
    final url = await showDialog<String>(context: context, builder: (_) => const _LinkDialog());
    if (url == null || !mounted) return;
    final date = _examDate;
    _create('Reading $url…', (api, id) => api.createFromLink(id, url, examDate: date));
  }

  Future<void> _startCourse() async {
    final topics = TopicApi.of(context.read<AppState>().api);
    final course = await showDialog<TopicSummary>(
      context: context,
      builder: (_) => _CourseDialog(load: topics.listTopics(_learnerId)),
    );
    if (course == null || !mounted) return;
    final date = _examDate;
    _create('Turning ${course.title} into an exam…',
        (api, id) => api.createFromCourse(id, course.id, examDate: date));
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: _examDate ?? now.add(const Duration(days: 14)),
      firstDate: DateTime(now.year, now.month, now.day),
      lastDate: now.add(const Duration(days: 730)),
      helpText: 'When is the exam?',
    );
    if (picked != null && mounted) setState(() => _examDate = picked);
  }

  @override
  Widget build(BuildContext context) {
    final shell = context.read<ShellState>();
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
                  eyebrow: 'EXAM PREPARATION',
                  title: 'What are you preparing for?',
                  onBack: shell.closeExams,
                  backKey: const ValueKey('exams-back'),
                ),
                const SizedBox(height: 20),
                _startCard(),
                const SizedBox(height: 32),
                Text('Your exams', style: serif(21)),
                const SizedBox(height: 12),
                FutureBuilder<List<ExamSummary>>(
                  future: _exams,
                  builder: (context, snap) {
                    if (snap.connectionState != ConnectionState.done) {
                      return const Padding(
                        padding: EdgeInsets.all(24),
                        child: Center(child: CircularProgressIndicator(color: Paper.accent)),
                      );
                    }
                    if (snap.hasError) {
                      return RetryLine(message: 'Could not load your exams: ${snap.error}', onRetry: _reload);
                    }
                    final exams = snap.data ?? const [];
                    if (exams.isEmpty) {
                      return Text(
                        'No exams yet. Set one up above: Versa splits it into units you can '
                        'quiz yourself on, and writes timed mock tests across all of them.',
                        key: const ValueKey('exams-empty'),
                        style: sans(13.5, color: Paper.muted, height: 1.5),
                      );
                    }
                    return Column(children: [
                      for (final e in exams)
                        _ExamCard(
                          exam: e,
                          onTap: () async {
                            await pushExam(context, e.id);
                            _reload();
                          },
                        ),
                    ]);
                  },
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _startCard() {
    final canSearch = _subject.text.trim().isNotEmpty && _busy == null;
    final date = _examDate;
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
          Text('Set up an exam', style: serif(19)),
          const SizedBox(height: 4),
          Text(
            'Say what it\'s on, or bring the material. Versa turns it into units to revise.',
            style: sans(13, color: Paper.muted),
          ),
          const SizedBox(height: 14),
          Row(children: [
            Expanded(
              child: TextField(
                key: const ValueKey('exam-subject-field'),
                controller: _subject,
                enabled: _busy == null,
                textInputAction: TextInputAction.go,
                onSubmitted: (_) => _startSearch(),
                decoration: InputDecoration(
                  hintText: 'e.g. A-level chemistry, AWS Solutions Architect, GRE verbal…',
                  prefixIcon: const Icon(Icons.fact_check_outlined, size: 20),
                  filled: true,
                  fillColor: Paper.sliver,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: const BorderSide(color: Paper.border),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 10),
            FilledButton(
              key: const ValueKey('exam-search-go'),
              onPressed: canSearch ? _startSearch : null,
              style: FilledButton.styleFrom(
                backgroundColor: Paper.accent,
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              ),
              child: const Text('Build syllabus'),
            ),
          ]),
          const SizedBox(height: 10),
          Wrap(crossAxisAlignment: WrapCrossAlignment.center, spacing: 6, children: [
            TextButton.icon(
              key: const ValueKey('exam-date'),
              onPressed: _busy == null ? _pickDate : null,
              icon: const Icon(Icons.event_outlined, size: 18),
              label: Text(date == null ? 'Add the exam date (optional)' : 'Exam on ${_dayLabel(date)}'),
              style: TextButton.styleFrom(foregroundColor: Paper.body),
            ),
            if (date != null)
              IconButton(
                key: const ValueKey('exam-date-clear'),
                tooltip: 'No date',
                onPressed: () => setState(() => _examDate = null),
                icon: const Icon(Icons.close_rounded, size: 16, color: Paper.faint),
              ),
          ]),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(child: Container(height: 1, color: Paper.border)),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Text('OR START FROM', style: mono(9.5)),
            ),
            Expanded(child: Container(height: 1, color: Paper.border)),
          ]),
          const SizedBox(height: 14),
          Wrap(spacing: 10, runSpacing: 10, children: [
            _SourceButton(
              buttonKey: const ValueKey('exam-upload-pdf'),
              icon: Icons.picture_as_pdf_outlined,
              label: 'A PDF',
              onTap: _busy == null ? _startPdf : null,
            ),
            _SourceButton(
              buttonKey: const ValueKey('exam-link'),
              icon: Icons.link_rounded,
              label: 'A web link',
              onTap: _busy == null ? _startLink : null,
            ),
            _SourceButton(
              buttonKey: const ValueKey('exam-from-course'),
              icon: Icons.menu_book_outlined,
              label: 'One of my courses',
              onTap: _busy == null ? _startCourse : null,
            ),
          ]),
          if (_busy != null) ...[
            const SizedBox(height: 16),
            Row(key: const ValueKey('exam-busy'), children: [
              const SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(strokeWidth: 2, color: Paper.accent),
              ),
              const SizedBox(width: 10),
              Expanded(child: Text(_busy!, style: sans(13, color: Paper.body))),
            ]),
          ],
        ],
      ),
    );
  }
}

const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

String _dayLabel(DateTime d) => '${d.day} ${_months[d.month - 1]} ${d.year}';

/// "12 Oct 2026 · in 16 days", or null when the exam has no date.
String? examDateLine(ExamSummary e) {
  final date = e.examDate;
  if (date == null) return null;
  final left = e.daysLeft;
  return left == null ? _dayLabel(date) : '${_dayLabel(date)} · ${daysLeftLabel(left)}';
}

class _SourceButton extends StatelessWidget {
  const _SourceButton({required this.buttonKey, required this.icon, required this.label, required this.onTap});
  final Key buttonKey;
  final IconData icon;
  final String label;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => OutlinedButton.icon(
        key: buttonKey,
        onPressed: onTap,
        icon: Icon(icon, size: 18),
        label: Text(label),
        style: OutlinedButton.styleFrom(
          foregroundColor: Paper.ink,
          side: const BorderSide(color: Paper.borderStrong),
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
        ),
      );
}

class _ExamCard extends StatelessWidget {
  const _ExamCard({required this.exam, required this.onTap});
  final ExamSummary exam;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final when = examDateLine(exam);
    final soon = (exam.daysLeft ?? 99) >= 0 && (exam.daysLeft ?? 99) <= 7;
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        key: ValueKey('exam-card-${exam.id}'),
        borderRadius: BorderRadius.circular(14),
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.all(18),
          decoration: BoxDecoration(
            color: Paper.card,
            border: Border.all(color: Paper.border),
            borderRadius: BorderRadius.circular(14),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                Expanded(child: Text(exam.title, style: serif(18))),
                const Icon(Icons.arrow_forward_rounded, size: 18, color: Paper.faint),
              ]),
              const SizedBox(height: 8),
              if (when != null) ...[
                Row(children: [
                  Icon(Icons.event_outlined, size: 15, color: soon ? Paper.accent : Paper.faint),
                  const SizedBox(width: 6),
                  Text(when, style: sans(12.5, color: soon ? Paper.accent : Paper.body, weight: FontWeight.w600)),
                ]),
                const SizedBox(height: 6),
              ],
              Text(
                '${exam.unitCount} unit${exam.unitCount == 1 ? '' : 's'} · '
                '${exam.quizzesTaken} quiz${exam.quizzesTaken == 1 ? '' : 'zes'} taken',
                style: sans(12, color: Paper.muted),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _LinkDialog extends StatefulWidget {
  const _LinkDialog();

  @override
  State<_LinkDialog> createState() => _LinkDialogState();
}

class _LinkDialogState extends State<_LinkDialog> {
  final _url = TextEditingController();
  String? _error;

  void _submit() {
    final text = _url.text.trim();
    final uri = Uri.tryParse(text);
    if (uri == null || !(uri.scheme == 'http' || uri.scheme == 'https') || uri.host.isEmpty) {
      setState(() => _error = 'Paste a full web address starting with http:// or https://');
      return;
    }
    Navigator.of(context).pop(text);
  }

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: Paper.surface,
        title: Text('Prepare from a web page', style: serif(19)),
        content: SizedBox(
          width: 460,
          child: TextField(
            key: const ValueKey('exam-link-field'),
            controller: _url,
            autofocus: true,
            onSubmitted: (_) => _submit(),
            decoration: InputDecoration(hintText: 'https://…', errorText: _error),
          ),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Cancel')),
          FilledButton(
            key: const ValueKey('exam-link-go'),
            onPressed: _submit,
            style: FilledButton.styleFrom(backgroundColor: Paper.accent),
            child: const Text('Read it'),
          ),
        ],
      );
}

/// Pick one of the learner's Learn-a-topic courses.
class _CourseDialog extends StatelessWidget {
  const _CourseDialog({required this.load});
  final Future<List<TopicSummary>> load;

  @override
  Widget build(BuildContext context) => AlertDialog(
        backgroundColor: Paper.surface,
        title: Text('Which course is the exam on?', style: serif(19)),
        content: SizedBox(
          width: 460,
          child: FutureBuilder<List<TopicSummary>>(
            future: load,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) {
                return const SizedBox(
                  height: 80,
                  child: Center(child: CircularProgressIndicator(color: Paper.accent)),
                );
              }
              if (snap.hasError) {
                return Text('Could not load your courses: ${snap.error}', style: sans(13, color: Paper.danger));
              }
              final courses = snap.data ?? const [];
              if (courses.isEmpty) {
                return Text(
                  'You have no courses yet. Build one in Learn a topic, or start the exam '
                  'from a subject, a PDF or a link instead.',
                  key: const ValueKey('exam-no-courses'),
                  style: sans(13.5, color: Paper.muted, height: 1.5),
                );
              }
              return ConstrainedBox(
                constraints: const BoxConstraints(maxHeight: 360),
                child: ListView(shrinkWrap: true, children: [
                  for (final c in courses)
                    ListTile(
                      key: ValueKey('exam-course-${c.id}'),
                      leading: const Icon(Icons.menu_book_outlined, color: Paper.accent),
                      title: Text(c.title, style: sans(14.5, color: Paper.ink, weight: FontWeight.w600)),
                      subtitle: Text(
                        '${c.chapterCount} chapter${c.chapterCount == 1 ? '' : 's'} · ${c.percent}% done',
                        style: sans(12, color: Paper.muted),
                      ),
                      onTap: () => Navigator.of(context).pop(c),
                    ),
                ]),
              );
            },
          ),
        ),
        actions: [TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Cancel'))],
      );
}
