import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../chat_controller.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/collapsed_rail.dart';
import '../widgets/composer.dart';
import '../widgets/level_slider.dart';
import '../widgets/message_view.dart';
import '../widgets/stage_panel.dart';
import 'topic_api.dart';
import 'topic_models.dart';
import 'topic_widgets.dart';

/// A lesson: a chat whose tutor works through this lesson's tasks, with the
/// task checklist and progress beside it (filled in live as the server judges
/// each task done), the length/depth sliders, and the stage when it's on.
class LessonChatScreen extends StatefulWidget {
  const LessonChatScreen({super.key, required this.lessonId});
  final String lessonId;

  @override
  State<LessonChatScreen> createState() => _LessonChatScreenState();
}

class _LessonChatScreenState extends State<LessonChatScreen> {
  Lesson? _lesson;
  ChatController? _chat;
  StreamSubscription<ProgressEvent>? _progress;
  Object? _error;

  /// The task that was just ticked, to flash it once.
  String? _justDone;
  bool _tasksCollapsed = false;

  @override
  void initState() {
    super.initState();
    _open();
  }

  Future<void> _open() async {
    setState(() => _error = null);
    final api = TopicApi.of(context.read<AppState>().api);
    final shell = context.read<ShellState>();
    try {
      final lesson = await api.getLesson(widget.lessonId);
      final sessionId = await api.startLesson(widget.lessonId);
      if (!mounted) return;
      final chat = shell.makeChat(resumeSessionId: sessionId);
      _progress = chat.progressEvents.listen(_onProgress);
      chat.start();
      setState(() {
        _lesson = lesson;
        _chat = chat;
      });
    } catch (e) {
      if (mounted) setState(() => _error = e);
    }
  }

  void _onProgress(ProgressEvent e) {
    final lesson = _lesson;
    if (lesson == null || e.lessonId != lesson.id) return;
    setState(() {
      for (final t in lesson.tasks) {
        if (t.id == e.taskId) t.done = true;
      }
      lesson.percent = e.lessonPercent;
      lesson.status = parseLessonStatus(e.lessonStatus);
      lesson.chapterPercent = e.chapterPercent;
      lesson.topicPercent = e.topicPercent;
      _justDone = e.taskId;
    });
    final task = lesson.tasks.where((t) => t.id == e.taskId).firstOrNull;
    if (task != null && mounted) {
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(
          duration: const Duration(seconds: 3),
          content: Text(lesson.status == LessonStatus.done
              ? 'Lesson complete. ${e.lessonPercent}%'
              : 'Task done: ${task.description}'),
        ));
    }
  }

  @override
  void dispose() {
    _progress?.cancel();
    _chat?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final lesson = _lesson;
    final chat = _chat;
    if (lesson == null || chat == null) {
      return Container(
        color: Paper.surface,
        padding: const EdgeInsets.all(32),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            PageHeading(
              eyebrow: 'LESSON',
              title: 'Opening the lesson…',
              onBack: () => Navigator.of(context).maybePop(),
              backKey: const ValueKey('lesson-back'),
            ),
            const SizedBox(height: 20),
            if (_error != null)
              RetryLine(message: 'Could not open this lesson: $_error', onRetry: _open)
            else
              const Center(child: CircularProgressIndicator(color: Paper.accent)),
          ],
        ),
      );
    }
    final shell = context.watch<ShellState>();
    final showStage = context.watch<AppState>().showStagePanel;
    return LayoutBuilder(builder: (context, c) {
      final wide = c.maxWidth >= 1000;
      final stageOpen = showStage && !shell.stagePanelCollapsed;
      final tasks = _TaskPanel(lesson: lesson, chat: chat, justDone: _justDone);
      final column = _LessonChatColumn(
        lesson: lesson,
        chat: chat,
        optionsOnStage: stageOpen,
        showStageToggle: showStage && !wide,
        stageCollapsed: shell.stagePanelCollapsed,
        onToggleStage: shell.toggleStagePanelCollapsed,
        compactTasks: wide
            ? null
            : _CompactTasks(
                lesson: lesson,
                collapsed: _tasksCollapsed,
                onToggle: () => setState(() => _tasksCollapsed = !_tasksCollapsed),
                child: tasks,
              ),
      );
      return Container(
        color: Paper.surface,
        child: Row(children: [
          if (wide && showStage)
            shell.stagePanelCollapsed
                ? CollapsedRailStrip(
                    icon: Icons.auto_awesome_rounded,
                    tooltip: 'Show stage',
                    onExpand: shell.toggleStagePanelCollapsed,
                  )
                : Expanded(child: StagePanel(chat: chat, onCollapse: shell.toggleStagePanelCollapsed)),
          Expanded(
            child: Column(children: [
              if (!wide && stageOpen)
                StagePanel(chat: chat, compact: true, onCollapse: shell.toggleStagePanelCollapsed),
              Expanded(child: column),
            ]),
          ),
          if (wide)
            Container(
              width: 290,
              decoration: const BoxDecoration(
                color: Paper.sliver,
                border: Border(left: BorderSide(color: Paper.border)),
              ),
              child: SingleChildScrollView(padding: const EdgeInsets.all(20), child: tasks),
            ),
        ]),
      );
    });
  }
}

class _LessonChatColumn extends StatefulWidget {
  const _LessonChatColumn({
    required this.lesson,
    required this.chat,
    required this.optionsOnStage,
    required this.showStageToggle,
    required this.stageCollapsed,
    required this.onToggleStage,
    this.compactTasks,
  });

  final Lesson lesson;
  final ChatController chat;
  final bool optionsOnStage;
  final bool showStageToggle;
  final bool stageCollapsed;
  final VoidCallback onToggleStage;
  final Widget? compactTasks;

  @override
  State<_LessonChatColumn> createState() => _LessonChatColumnState();
}

class _LessonChatColumnState extends State<_LessonChatColumn> {
  final _scroll = ScrollController();
  int _lastCount = 0;
  bool _atBottom = true;

  @override
  void initState() {
    super.initState();
    widget.chat.addListener(_onChange);
    _scroll.addListener(() {
      if (!_scroll.hasClients) return;
      final p = _scroll.position;
      _atBottom = p.maxScrollExtent - p.pixels < 80;
    });
  }

  @override
  void dispose() {
    widget.chat.removeListener(_onChange);
    _scroll.dispose();
    super.dispose();
  }

  void _onChange() {
    final grew = widget.chat.messages.length != _lastCount;
    _lastCount = widget.chat.messages.length;
    if (grew || _atBottom) _follow(3);
  }

  void _follow(int rounds) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || !_scroll.hasClients) return;
      _scroll.jumpTo(_scroll.position.maxScrollExtent);
      _atBottom = true;
      if (rounds > 1) _follow(rounds - 1);
    });
  }

  @override
  Widget build(BuildContext context) {
    final showTiming = context.watch<AppState>().showTiming;
    final chat = widget.chat;
    final lesson = widget.lesson;
    return ListenableBuilder(
      listenable: chat,
      builder: (context, _) {
        final shown = widget.optionsOnStage
            ? [for (final m in chat.messages) if (!(m.role == Role.tutor && m.hasOptions)) m]
            : chat.messages;
        return Column(children: [
          _LessonHeader(
            lesson: lesson,
            chat: chat,
            showStageToggle: widget.showStageToggle,
            stageCollapsed: widget.stageCollapsed,
            onToggleStage: widget.onToggleStage,
          ),
          if (chat.status == ChatStatus.disconnected)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 8),
              color: const Color(0xFFFBECE8),
              child: Row(children: [
                Expanded(child: Text(chat.problem ?? 'Disconnected.', style: sans(13, color: Paper.danger))),
                TextButton(onPressed: chat.reconnect, child: const Text('Reconnect')),
              ]),
            ),
          if (widget.compactTasks != null) widget.compactTasks!,
          Expanded(
            child: chat.messages.isEmpty
                ? _LessonIntro(lesson: lesson, chat: chat)
                : Center(
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 820),
                      child: ListView.separated(
                        key: const ValueKey('lesson-message-list'),
                        controller: _scroll,
                        padding: const EdgeInsets.fromLTRB(24, 22, 24, 16),
                        itemCount: shown.length,
                        separatorBuilder: (_, _) => const SizedBox(height: 18),
                        itemBuilder: (context, i) {
                          final m = shown[i];
                          return MessageView(
                            message: m,
                            showTiming: showTiming,
                            canPickOption: chat.canSend,
                            onPickOption: (o) => chat.pickOption(m, o),
                          );
                        },
                      ),
                    ),
                  ),
          ),
          Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 820),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(22, 4, 22, 18),
                child: Composer(
                  enabled: chat.canSend,
                  hint: chat.canSend || chat.busy ? 'Answer, ask, or say what you want to do…' : 'Waiting for the connection…',
                  onSend: chat.send,
                ),
              ),
            ),
          ),
        ]);
      },
    );
  }
}

class _LessonHeader extends StatelessWidget {
  const _LessonHeader({
    required this.lesson,
    required this.chat,
    required this.showStageToggle,
    required this.stageCollapsed,
    required this.onToggleStage,
  });

  final Lesson lesson;
  final ChatController chat;
  final bool showStageToggle;
  final bool stageCollapsed;
  final VoidCallback onToggleStage;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: const BoxDecoration(
        color: Paper.surface,
        border: Border(bottom: BorderSide(color: Paper.border)),
      ),
      child: Row(children: [
        IconButton(
          key: const ValueKey('lesson-back'),
          tooltip: 'Back',
          onPressed: () => Navigator.of(context).maybePop(),
          icon: const Icon(Icons.arrow_back_rounded, color: Paper.faint, size: 20),
        ),
        if (showStageToggle)
          IconButton(
            key: const ValueKey('lesson-stage-toggle'),
            tooltip: stageCollapsed ? 'Show stage' : 'Hide stage',
            onPressed: onToggleStage,
            icon: Icon(Icons.auto_awesome_rounded, color: stageCollapsed ? Paper.faint : Paper.accent, size: 20),
          ),
        const SizedBox(width: 6),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '${lesson.topicTitle}  ›  ${lesson.chapterTitle}'.toUpperCase(),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: mono(9.5),
              ),
              const SizedBox(height: 2),
              Text(lesson.title,
                  key: const ValueKey('lesson-title'),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: serif(18)),
            ],
          ),
        ),
        const SizedBox(width: 10),
        SizedBox(
          width: 120,
          child: PercentRow(percent: lesson.percent, height: 6, labelKey: const ValueKey('lesson-header-percent')),
        ),
      ]),
    );
  }
}

/// Before the first message: what this lesson is for and where it starts.
class _LessonIntro extends StatelessWidget {
  const _LessonIntro({required this.lesson, required this.chat});
  final Lesson lesson;
  final ChatController chat;

  @override
  Widget build(BuildContext context) {
    final current = lesson.currentTask;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(32),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 600),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('LESSON', style: mono(11)),
              const SizedBox(height: 8),
              Text(lesson.title, style: serif(30)),
              if (lesson.objective.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text(lesson.objective, style: sans(14.5, color: Paper.muted, height: 1.6)),
              ],
              if (current != null) ...[
                const SizedBox(height: 18),
                Text('First up', style: sans(12, color: Paper.faint, weight: FontWeight.w600)),
                const SizedBox(height: 4),
                Text(current.description, style: sans(14, height: 1.5)),
              ],
              const SizedBox(height: 22),
              ShapedByNote(sources: lesson.personalizedBy),
              if (lesson.personalizedBy.isNotEmpty) const SizedBox(height: 18),
              FilledButton.icon(
                key: const ValueKey('start-lesson'),
                onPressed: chat.canSend
                    ? () => chat.send(lesson.status == LessonStatus.notStarted
                        ? "I'm ready, let's start this lesson."
                        : "Let's pick up where I left off.")
                    : null,
                icon: const Icon(Icons.play_arrow_rounded),
                label: Text(lesson.status == LessonStatus.notStarted ? 'Start the lesson' : 'Continue'),
                style: FilledButton.styleFrom(
                  backgroundColor: Paper.accent,
                  padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 16),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// The lesson's tasks (the last is always the end-of-lesson questions), its
/// progress, where it sits in the course, and the sliders.
class _TaskPanel extends StatelessWidget {
  const _TaskPanel({required this.lesson, required this.chat, required this.justDone});
  final Lesson lesson;
  final ChatController chat;
  final String? justDone;

  @override
  Widget build(BuildContext context) {
    final current = lesson.currentTask;
    return Column(
      key: const ValueKey('lesson-tasks'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('THIS LESSON', style: mono(10)),
        const SizedBox(height: 10),
        PercentRow(percent: lesson.percent, labelKey: const ValueKey('lesson-percent')),
        const SizedBox(height: 4),
        Text(
          lesson.status == LessonStatus.done
              ? 'Done. Every task complete.'
              : '${lesson.tasksDone} of ${lesson.tasks.length} tasks done',
          style: sans(12, color: Paper.muted),
        ),
        const SizedBox(height: 14),
        for (final t in lesson.tasks)
          _TaskRow(task: t, current: identical(t, current), flash: t.id == justDone),
        if (lesson.chapterPercent != null || lesson.topicPercent != null) ...[
          const SizedBox(height: 10),
          const Divider(),
          const SizedBox(height: 8),
          if (lesson.chapterPercent != null) ...[
            Text('Chapter · ${lesson.chapterTitle}', style: sans(11.5, color: Paper.muted)),
            const SizedBox(height: 4),
            PercentRow(percent: lesson.chapterPercent!, height: 5, labelKey: const ValueKey('lesson-chapter-percent')),
            const SizedBox(height: 10),
          ],
          if (lesson.topicPercent != null) ...[
            Text('Topic · ${lesson.topicTitle}', style: sans(11.5, color: Paper.muted)),
            const SizedBox(height: 4),
            PercentRow(percent: lesson.topicPercent!, height: 5, labelKey: const ValueKey('lesson-topic-percent')),
          ],
        ],
        const SizedBox(height: 16),
        const Divider(),
        const SizedBox(height: 10),
        ListenableBuilder(
          listenable: chat,
          builder: (context, _) => Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              LevelSlider(
                sliderKey: const ValueKey('lesson-knob-length'),
                label: 'Length',
                lowLabel: 'short',
                highLabel: 'long',
                value: chat.knobs.answerLength,
                onChanged: (v) => chat.setKnobs(answerLength: v),
              ),
              LevelSlider(
                sliderKey: const ValueKey('lesson-knob-depth'),
                label: 'Depth',
                lowLabel: 'gist',
                highLabel: 'rigorous',
                value: chat.knobs.depth,
                onChanged: (v) => chat.setKnobs(depth: v),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _TaskRow extends StatelessWidget {
  const _TaskRow({required this.task, required this.current, required this.flash});
  final LessonTask task;
  final bool current;
  final bool flash;

  @override
  Widget build(BuildContext context) {
    final kind = switch (task.kind) {
      'practice' => 'PRACTICE',
      'apply' => 'APPLY',
      'check' => 'END-OF-LESSON QUESTIONS',
      _ => 'LEARN',
    };
    return TweenAnimationBuilder<double>(
      key: ValueKey('task-${task.id}-${task.done}'),
      tween: Tween(begin: flash ? 1 : 0, end: 0),
      duration: const Duration(milliseconds: 1400),
      builder: (context, glow, child) => Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: Color.lerp(current ? Paper.accentSoft : Paper.card, const Color(0xFFE3F0DC), glow),
          border: Border.all(color: current ? Paper.accent : Paper.border),
          borderRadius: BorderRadius.circular(10),
        ),
        child: child,
      ),
      child: Row(
        key: ValueKey('task-${task.id}'),
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 250),
            transitionBuilder: (c, a) => ScaleTransition(scale: a, child: c),
            child: task.done
                ? Icon(Icons.check_circle_rounded, key: ValueKey('task-done-${task.id}'), size: 20, color: Paper.olive)
                : Icon(Icons.radio_button_unchecked_rounded,
                    key: ValueKey('task-open-${task.id}'), size: 20, color: current ? Paper.accent : Paper.faint),
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  Text(kind, style: mono(9)),
                  if (current) ...[
                    const SizedBox(width: 6),
                    Text('· NOW', style: mono(9, color: Paper.accent, weight: FontWeight.w700)),
                  ],
                ]),
                const SizedBox(height: 2),
                Text(task.description,
                    style: sans(12.5,
                        color: task.done ? Paper.muted : Paper.ink,
                        height: 1.4)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// On a phone: the task panel folds into a strip under the header.
class _CompactTasks extends StatelessWidget {
  const _CompactTasks({required this.lesson, required this.collapsed, required this.onToggle, required this.child});
  final Lesson lesson;
  final bool collapsed;
  final VoidCallback onToggle;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: const BoxDecoration(
        color: Paper.sliver,
        border: Border(bottom: BorderSide(color: Paper.border)),
      ),
      child: Column(children: [
        InkWell(
          key: const ValueKey('lesson-tasks-toggle'),
          onTap: onToggle,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 10),
            child: Row(children: [
              Text('TASKS ${lesson.tasksDone}/${lesson.tasks.length}', style: mono(10)),
              const Spacer(),
              Icon(collapsed ? Icons.expand_more_rounded : Icons.expand_less_rounded, color: Paper.faint),
            ]),
          ),
        ),
        AnimatedSize(
          duration: const Duration(milliseconds: 220),
          alignment: Alignment.topCenter,
          child: collapsed
              ? const SizedBox(width: double.infinity)
              : ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 260),
                  child: SingleChildScrollView(padding: const EdgeInsets.fromLTRB(18, 0, 18, 12), child: child),
                ),
        ),
      ]),
    );
  }
}
