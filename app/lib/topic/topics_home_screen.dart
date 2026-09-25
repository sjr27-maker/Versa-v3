import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import 'topic_api.dart';
import 'topic_models.dart';
import 'topic_widgets.dart';
import 'topics_root.dart';

typedef PickedPdf = ({String name, Uint8List bytes});

/// Opens the platform's file dialog for one PDF (web + desktop: read as
/// bytes). Replaceable so tests never open a real dialog.
Future<PickedPdf?> Function() topicPdfPicker = () async {
  final file = await FilePicker.pickFile(type: FileType.custom, allowedExtensions: const ['pdf']);
  if (file == null) return null;
  return (name: file.name, bytes: await file.readAsBytes());
};

/// Learn a topic: the person's courses, and the three ways to start one.
class TopicsHomeScreen extends StatefulWidget {
  const TopicsHomeScreen({super.key});

  @override
  State<TopicsHomeScreen> createState() => _TopicsHomeScreenState();
}

class _TopicsHomeScreenState extends State<TopicsHomeScreen> {
  final _search = TextEditingController();
  late Future<List<TopicSummary>> _topics;

  TopicApi get _api => TopicApi.of(context.read<AppState>().api);
  String get _learnerId => context.read<AppState>().learner!.id;

  @override
  void initState() {
    super.initState();
    _topics = _load();
    _search.addListener(() => setState(() {}));
  }

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  Future<List<TopicSummary>> _load() => _api.listTopics(_learnerId);

  void _reload() {
    if (!mounted) return;
    setState(() {
      _topics = _load();
    });
  }

  Future<void> _startSearch() async {
    final query = _search.text.trim();
    if (query.isEmpty) return;
    final api = _api;
    final learnerId = _learnerId;
    await pushExplorer(context, label: query, load: () => api.exploreSearch(learnerId, query));
    _reload();
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
    final api = _api;
    final learnerId = _learnerId;
    final (:name, :bytes) = picked;
    await pushExplorer(context, label: name, load: () => api.explorePdf(learnerId, name, bytes));
    _reload();
  }

  Future<void> _startLink() async {
    final url = await showDialog<String>(context: context, builder: (_) => const _LinkDialog());
    if (url == null || !mounted) return;
    final api = _api;
    final learnerId = _learnerId;
    await pushExplorer(context, label: url, load: () => api.exploreLink(learnerId, url));
    _reload();
  }

  void _toast(String text) => ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(text)));

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
                  eyebrow: 'LEARN A TOPIC',
                  title: 'What do you want to learn?',
                  onBack: shell.closeTopics,
                  backKey: const ValueKey('topics-back'),
                ),
                const SizedBox(height: 20),
                _startCard(),
                const SizedBox(height: 32),
                Text('Your topics', style: serif(21)),
                const SizedBox(height: 12),
                FutureBuilder<List<TopicSummary>>(
                  future: _topics,
                  builder: (context, snap) {
                    if (snap.connectionState != ConnectionState.done) {
                      return const Padding(
                        padding: EdgeInsets.all(24),
                        child: Center(child: CircularProgressIndicator(color: Paper.accent)),
                      );
                    }
                    if (snap.hasError) {
                      return RetryLine(message: 'Could not load your topics: ${snap.error}', onRetry: _reload);
                    }
                    final topics = snap.data ?? const [];
                    if (topics.isEmpty) {
                      return Text(
                        'No topics yet. Search one above, or bring a PDF or a web page, '
                        'and pick what you want to cover.',
                        key: const ValueKey('topics-empty'),
                        style: sans(13.5, color: Paper.muted, height: 1.5),
                      );
                    }
                    return Column(children: [
                      for (final t in topics)
                        _TopicCard(
                          topic: t,
                          onTap: () async {
                            await pushTopic(context, t.id);
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
    final canSearch = _search.text.trim().isNotEmpty;
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
          Text('Start a new topic', style: serif(19)),
          const SizedBox(height: 4),
          Text(
            'Search a subject to see how it branches, then tick what you want in your course.',
            style: sans(13, color: Paper.muted),
          ),
          const SizedBox(height: 14),
          Row(children: [
            Expanded(
              child: TextField(
                key: const ValueKey('topic-search-field'),
                controller: _search,
                textInputAction: TextInputAction.search,
                onSubmitted: (_) => _startSearch(),
                decoration: InputDecoration(
                  hintText: 'e.g. machine learning, the French Revolution, organic chemistry…',
                  prefixIcon: const Icon(Icons.search_rounded, size: 20),
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
              key: const ValueKey('topic-search-go'),
              onPressed: canSearch ? _startSearch : null,
              style: FilledButton.styleFrom(
                backgroundColor: Paper.accent,
                padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
              ),
              child: const Text('Explore'),
            ),
          ]),
          const SizedBox(height: 14),
          Row(children: [
            Expanded(child: Container(height: 1, color: Paper.border)),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Text('OR BRING YOUR OWN', style: mono(9.5)),
            ),
            Expanded(child: Container(height: 1, color: Paper.border)),
          ]),
          const SizedBox(height: 14),
          Wrap(spacing: 10, runSpacing: 10, children: [
            _SourceButton(
              buttonKey: const ValueKey('topic-upload-pdf'),
              icon: Icons.picture_as_pdf_outlined,
              label: 'Upload a PDF',
              onTap: _startPdf,
            ),
            _SourceButton(
              buttonKey: const ValueKey('topic-link'),
              icon: Icons.link_rounded,
              label: 'Use a web link',
              onTap: _startLink,
            ),
          ]),
        ],
      ),
    );
  }
}

class _SourceButton extends StatelessWidget {
  const _SourceButton({required this.buttonKey, required this.icon, required this.label, required this.onTap});
  final Key buttonKey;
  final IconData icon;
  final String label;
  final VoidCallback onTap;

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

class _TopicCard extends StatelessWidget {
  const _TopicCard({required this.topic, required this.onTap});
  final TopicSummary topic;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: InkWell(
        key: ValueKey('topic-card-${topic.id}'),
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
                Expanded(child: Text(topic.title, style: serif(18))),
                const Icon(Icons.arrow_forward_rounded, size: 18, color: Paper.faint),
              ]),
              const SizedBox(height: 10),
              PercentRow(percent: topic.percent, labelKey: ValueKey('topic-percent-${topic.id}')),
              const SizedBox(height: 6),
              Text(
                '${topic.lessonsDone} of ${topic.lessonCount} lessons done · '
                '${topic.chapterCount} chapter${topic.chapterCount == 1 ? '' : 's'}',
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
  Widget build(BuildContext context) {
    return AlertDialog(
      backgroundColor: Paper.surface,
      title: Text('Learn from a web page', style: serif(19)),
      content: SizedBox(
        width: 460,
        child: TextField(
          key: const ValueKey('topic-link-field'),
          controller: _url,
          autofocus: true,
          onSubmitted: (_) => _submit(),
          decoration: InputDecoration(hintText: 'https://…', errorText: _error),
        ),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Cancel')),
        FilledButton(
          key: const ValueKey('topic-link-go'),
          onPressed: _submit,
          style: FilledButton.styleFrom(backgroundColor: Paper.accent),
          child: const Text('Read it'),
        ),
      ],
    );
  }
}
