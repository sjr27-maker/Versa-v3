import 'package:flutter/material.dart';

import '../stage/engine.dart';
import '../stage/script.dart';
import '../stage/stage_view.dart';
import '../theme.dart';

/// A chat's saved stage performance, played small and on a loop -- the
/// picture on its Home card (server: `ChatSummary.scene`). The scene only:
/// no speech bubble, no quick check, nothing to tap.
class ScenePreview extends StatefulWidget {
  const ScenePreview({super.key, required this.actions});

  /// A stage script, as the director wrote it.
  final List<Map<String, dynamic>> actions;

  @override
  State<ScenePreview> createState() => _ScenePreviewState();
}

class _ScenePreviewState extends State<ScenePreview> {
  final _engine = StageEngine();

  /// When the performance ended (stage time); it starts over a little later.
  double? _endedAt;
  static const _pause = 5.0;

  @override
  void initState() {
    super.initState();
    _engine.addListener(_loop);
    _play();
  }

  @override
  void didUpdateWidget(covariant ScenePreview old) {
    super.didUpdateWidget(old);
    if (!identical(old.actions, widget.actions)) _play();
  }

  /// A question would wait for an answer nobody here can give.
  static List<dynamic> _watchable(List<dynamic> actions) => [
        for (final a in actions)
          if (a is Map && a['do'] != 'ask')
            a['do'] == 'together' && a['actions'] is List
                ? {...a, 'actions': _watchable(a['actions'] as List)}
                : a,
      ];

  void _play() {
    _endedAt = null;
    _engine.beginLive();
    for (final action in parseScript(_watchable(widget.actions))) {
      _engine.enqueue(action);
    }
    _engine.endLive();
  }

  void _loop() {
    if (_engine.running) {
      _endedAt = null;
      return;
    }
    final ended = _endedAt ??= _engine.time;
    if (_engine.time - ended > _pause) _play();
  }

  @override
  void dispose() {
    _engine.removeListener(_loop);
    _engine.stop();
    _engine.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AspectRatio(
      aspectRatio: 16 / 9,
      child: Container(
        clipBehavior: Clip.antiAlias,
        decoration: BoxDecoration(
          color: Paper.sliver,
          border: Border.all(color: Paper.border),
          borderRadius: BorderRadius.circular(12),
        ),
        child: IgnorePointer(child: StageView(engine: _engine, quiet: true)),
      ),
    );
  }
}
