import 'package:flutter/material.dart';

import '../theme.dart';

/// Whether the stage and the chat share the room, or one of them has it all.
enum StageSplitView { split, stageOnly, chatOnly }

/// The stage and the chat with a handle between them that the learner drags
/// to give either one more room -- a finger on a phone, the mouse on a
/// laptop. Double-tap the handle to put it back in the middle. On a phone a
/// single tap stretches the stage as tall as it goes (the ambiguity options
/// sit on it and need the room), and a second tap brings it back.
///
///  * wide (a laptop): stage on the left, chat on the right; the handle is a
///    vertical bar and sets how much of the row the stage takes.
///  * phone: stage above, chat below; the handle is a horizontal bar and sets
///    how tall the stage is.
///
/// The size lives here while dragging (so only this widget rebuilds each
/// frame) and is handed to [onHeight] / [onFraction] when the drag ends --
/// the screens keep it on ShellState, so it survives leaving the chat.
///
/// [view] gives the whole room to one of the two. The other is kept, only
/// out of sight: a hidden stage goes on being sent each answer's animation
/// (hiding it is not turning Animations off -- that is the switch), and a
/// hidden chat keeps its place and whatever was being typed.
class StageSplit extends StatefulWidget {
  const StageSplit({
    super.key,
    required this.wide,
    required this.stage,
    required this.chat,
    this.view = StageSplitView.split,
    this.height = defaultHeight,
    this.fraction = 0.5,
    this.onHeight,
    this.onFraction,
  });

  static const defaultHeight = 280.0;

  final bool wide;
  final StageSplitView view;

  /// The stage's height on a phone, and its share of the row when wide.
  final double height;
  final double fraction;
  final ValueChanged<double>? onHeight;
  final ValueChanged<double>? onFraction;

  /// The stage, given its height on a phone (null on a wide screen, where it
  /// fills its column).
  final Widget Function(double? height) stage;
  final Widget chat;

  @override
  State<StageSplit> createState() => _StageSplitState();
}

class _StageSplitState extends State<StageSplit> {
  late double _fraction = widget.fraction;
  late double _height = widget.height;

  static const _minStageWidth = 300.0;
  static const _minChatWidth = 380.0;
  static const _minStageHeight = 120.0;

  /// On a phone, what the chat keeps below the stage at the most: its
  /// header, a line or two, and the composer.
  static const _minChatHeight = 260.0;

  /// The stage and the chat keep their state as they move between the split
  /// and the one-at-a-time views.
  final _stageKey = GlobalKey();
  final _chatKey = GlobalKey();

  Widget _stage(double? height) => KeyedSubtree(key: _stageKey, child: widget.stage(height));
  Widget get _chat => KeyedSubtree(key: _chatKey, child: widget.chat);

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      final view = widget.view;
      if (widget.wide) {
        final room = c.maxWidth - ResizeHandle.thickness;
        final lo = (_minStageWidth / room).clamp(0.15, 0.5).toDouble();
        final hi = (1 - _minChatWidth / room).clamp(0.5, 0.85).toDouble();
        final fraction = _fraction.clamp(lo, hi).toDouble();
        final stageWidth = room * fraction;
        if (view == StageSplitView.stageOnly) {
          return Row(children: [
            Expanded(child: _stage(null)),
            Offstage(child: SizedBox(width: room - stageWidth, height: c.maxHeight, child: _chat)),
          ]);
        }
        if (view == StageSplitView.chatOnly) {
          return Row(children: [
            Offstage(child: SizedBox(width: stageWidth, height: c.maxHeight, child: _stage(null))),
            Expanded(child: _chat),
          ]);
        }
        return Row(children: [
          SizedBox(width: stageWidth, child: _stage(null)),
          ResizeHandle(
            key: const ValueKey('stage-resize'),
            axis: Axis.horizontal,
            onDrag: (dx) => setState(() => _fraction = ((stageWidth + dx) / room).clamp(lo, hi).toDouble()),
            onEnd: () => widget.onFraction?.call(_fraction),
            onReset: () {
              setState(() => _fraction = 0.5);
              widget.onFraction?.call(_fraction);
            },
          ),
          Expanded(child: _chat),
        ]);
      }
      // A phone: keep room below for the chat's header, a few lines and the composer.
      final maxHeight = (c.maxHeight - _minChatHeight).clamp(_minStageHeight + 40, 900.0).toDouble();
      final height = _height.clamp(_minStageHeight, maxHeight).toDouble();
      if (view == StageSplitView.stageOnly) {
        // no height: the stage fills the screen
        return Column(children: [
          Expanded(child: _stage(null)),
          Offstage(child: SizedBox(width: c.maxWidth, height: _minChatHeight, child: _chat)),
        ]);
      }
      if (view == StageSplitView.chatOnly) {
        return Column(children: [
          Offstage(child: SizedBox(width: c.maxWidth, child: _stage(height))),
          Expanded(child: _chat),
        ]);
      }
      return Column(children: [
        _stage(height),
        ResizeHandle(
          key: const ValueKey('stage-resize'),
          axis: Axis.vertical,
          onDrag: (dy) => setState(() => _height = (height + dy).clamp(_minStageHeight, maxHeight).toDouble()),
          onEnd: () => widget.onHeight?.call(_height),
          onTap: () {
            setState(() => _height = height < maxHeight - 1 ? maxHeight : StageSplit.defaultHeight);
            widget.onHeight?.call(_height);
          },
          onReset: () {
            setState(() => _height = StageSplit.defaultHeight);
            widget.onHeight?.call(_height);
          },
        ),
        Expanded(child: _chat),
      ]);
    });
  }
}

/// A grab bar for resizing. [axis] is the direction it moves: vertical for a
/// bar between a top and a bottom pane, horizontal for one between left and
/// right. The whole bar is the touch area, not just the drawn pill.
class ResizeHandle extends StatelessWidget {
  const ResizeHandle({
    super.key,
    required this.axis,
    required this.onDrag,
    this.onEnd,
    this.onTap,
    this.onReset,
  });

  /// How much room the handle takes across the split -- enough for a thumb.
  static const thickness = 20.0;

  final Axis axis;
  final ValueChanged<double> onDrag;
  final VoidCallback? onEnd;
  final VoidCallback? onTap;
  final VoidCallback? onReset;

  @override
  Widget build(BuildContext context) {
    final vertical = axis == Axis.vertical;
    // Paper.faint, not a border colour: on the light palettes a border-
    // coloured pill all but vanished and nobody found the handle.
    final pill = Container(
      width: vertical ? 48 : 5,
      height: vertical ? 5 : 48,
      decoration: BoxDecoration(color: Paper.faint, borderRadius: BorderRadius.circular(3)),
    );
    final bar = Container(
      width: vertical ? double.infinity : thickness,
      height: vertical ? thickness : double.infinity,
      color: Paper.sliver,
      alignment: Alignment.center,
      child: pill,
    );
    return Semantics(
      label: onTap == null
          ? 'Drag to resize the stage and the chat. Double-tap to reset.'
          : 'Drag to resize the stage and the chat. Tap to stretch it, double-tap to reset.',
      child: MouseRegion(
        cursor: vertical ? SystemMouseCursors.resizeRow : SystemMouseCursors.resizeColumn,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: onTap,
          onDoubleTap: onReset,
          onVerticalDragUpdate: vertical ? (d) => onDrag(d.delta.dy) : null,
          onVerticalDragEnd: vertical ? (_) => onEnd?.call() : null,
          onHorizontalDragUpdate: vertical ? null : (d) => onDrag(d.delta.dx),
          onHorizontalDragEnd: vertical ? null : (_) => onEnd?.call(),
          child: bar,
        ),
      ),
    );
  }
}
