import 'dart:async';

import 'package:flutter/material.dart';

import '../stage/engine.dart';
import '../stage/script.dart';
import '../stage/stage_view.dart';
import '../theme.dart';
import 'exam_models.dart';

/// The stage in exam practice (src/versa/exams.py, stage.exam_scene_prompt):
/// each question is set up by the slime -- a short, focused scene showing
/// the situation -- and ends with the slime asking it, with the question's
/// own choices and never the answer. A tap on the stage answers it exactly as
/// a tap on the card does ([onPick]); [onAsked] says the question is up, so
/// the card can offer its choices in step with the animation.
class ExamStage extends StatefulWidget {
  const ExamStage({
    super.key,
    required this.question,
    required this.loadScene,
    required this.onPick,
    required this.onAsked,
    this.height = 230,
  });

  final Question question;
  final Future<List<Map<String, dynamic>>> Function(String questionId) loadScene;
  final void Function(Question question, int index) onPick;
  final void Function(String questionId) onAsked;
  final double height;

  @override
  State<ExamStage> createState() => ExamStageState();
}

class ExamStageState extends State<ExamStage> {
  final _engine = StageEngine();

  /// The question whose scene is showing.
  String? _showing;

  @override
  void initState() {
    super.initState();
    _engine.addListener(_watch);
    _engine.onChecked = (question, choices, picked, answer) {
      final i = int.tryParse(picked);
      if (i != null) widget.onPick(widget.question, i);
    };
    _play();
  }

  @override
  void didUpdateWidget(covariant ExamStage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.question.id != widget.question.id) _play();
  }

  Future<void> _play() async {
    final id = widget.question.id;
    _showing = id;
    _engine.reset();
    try {
      final script = await widget.loadScene(id);
      if (!mounted || _showing != id) return;
      unawaited(_engine.play(parseScript(script)));
    } catch (_) {
      // no scene: nothing to wait for -- the card offers its choices
      if (mounted && _showing == id) widget.onAsked(id);
    }
  }

  void _watch() {
    final id = _showing;
    if (id != null && (_engine.question?.key.startsWith('skit-') ?? false)) widget.onAsked(id);
  }

  /// The card was tapped: the slime takes the same answer.
  void answered(int index) {
    if (_engine.question?.key.startsWith('skit-') ?? false) _engine.answer('$index');
  }

  /// How a checked tap went (null: a mock, where nothing is revealed yet).
  void react({required bool? correct, String rightAnswer = ''}) {
    if (!mounted) return;
    _engine.play(parseScript([
      if (correct == true) ...[
        {'do': 'emote', 'mood': 'proud'},
        {'do': 'effect', 'kind': 'sparks', 'x': 0.5, 'y': 0.35},
        {'do': 'say', 'text': 'Correct.'},
      ] else if (correct == false) ...[
        {'do': 'emote', 'mood': 'thinking'},
        {'do': 'say', 'text': rightAnswer.isEmpty ? 'Not quite.' : 'Not quite -- see why below.'},
        {'do': 'emote', 'mood': 'happy'},
      ] else ...[
        {'do': 'emote', 'mood': 'happy'},
        {'do': 'say', 'text': 'Noted.'},
      ],
    ]));
  }

  @override
  void dispose() {
    _engine.removeListener(_watch);
    _engine.onChecked = null;
    _engine.stop();
    _engine.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      key: const ValueKey('exam-stage'),
      borderRadius: BorderRadius.circular(14),
      child: Container(
        height: widget.height,
        color: Paper.sliver,
        child: ListenableBuilder(
          listenable: _engine,
          builder: (context, _) => StageView(
            engine: _engine,
            onChoice: (_engine.question?.key.startsWith('skit-') ?? false) ? (c) => _engine.answer(c.id) : null,
          ),
        ),
      ),
    );
  }
}

/// A warm-up's scene: the slime acting a chapter's central idea out, once,
/// with a replay. It explains; it never asks.
class WarmUpStage extends StatefulWidget {
  const WarmUpStage({super.key, required this.script, this.height = 230});
  final List<Map<String, dynamic>> script;
  final double height;

  @override
  State<WarmUpStage> createState() => _WarmUpStageState();
}

class _WarmUpStageState extends State<WarmUpStage> {
  final _engine = StageEngine();

  @override
  void initState() {
    super.initState();
    _play();
  }

  @override
  void didUpdateWidget(covariant WarmUpStage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.script, widget.script)) _play();
  }

  void _play() {
    _engine.reset();
    if (widget.script.isNotEmpty) unawaited(_engine.play(parseScript(widget.script)));
  }

  @override
  void dispose() {
    _engine.stop();
    _engine.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      key: const ValueKey('warmup-stage'),
      borderRadius: BorderRadius.circular(14),
      child: Container(
        height: widget.height,
        color: Paper.sliver,
        child: Stack(children: [
          Positioned.fill(child: StageView(engine: _engine)),
          Positioned(
            top: 4,
            right: 4,
            child: IconButton(
              key: const ValueKey('warmup-replay'),
              tooltip: 'Play again',
              onPressed: _play,
              icon: Icon(Icons.replay_rounded, size: 20, color: Paper.faint),
            ),
          ),
        ]),
      ),
    );
  }
}
