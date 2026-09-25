import 'package:flutter/material.dart';

import '../theme.dart';

/// Which edge a [CollapsedRailStrip] sits on — only changes which side its
/// border draws, so a collapsed strip still reads as part of the panel it
/// replaces regardless of which side of the screen that panel is on.
enum RailSide { left, right }

/// A minimized side panel: a thin, always-visible strip with one icon to
/// bring it back. Shared by every collapsible panel (chat history, stage,
/// the main nav rail, session knobs) so "minimized" looks and behaves the
/// same way everywhere it appears.
class CollapsedRailStrip extends StatelessWidget {
  const CollapsedRailStrip({
    super.key,
    required this.icon,
    required this.tooltip,
    required this.onExpand,
    this.side = RailSide.left,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback onExpand;
  final RailSide side;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 40,
      decoration: BoxDecoration(
        color: Paper.sliver,
        border: Border(
          right: side == RailSide.left
              ? const BorderSide(color: Paper.border)
              : BorderSide.none,
          left: side == RailSide.right
              ? const BorderSide(color: Paper.border)
              : BorderSide.none,
        ),
      ),
      child: Column(
        children: [
          const SizedBox(height: 12),
          IconButton(
            tooltip: tooltip,
            visualDensity: VisualDensity.compact,
            onPressed: onExpand,
            icon: Icon(icon, size: 18, color: Paper.faint),
          ),
        ],
      ),
    );
  }
}
