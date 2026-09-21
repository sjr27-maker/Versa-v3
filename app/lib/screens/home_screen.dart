import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import '../widgets/placeholder_page.dart';

/// Home: one thing that works (start a chat) and a preview of the feed that is
/// planned. The cards are skeletons on purpose.
class HomeScreen extends StatelessWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final name = context.watch<AppState>().learner?.label ?? '';
    final shell = context.read<ShellState>();
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(40, 36, 40, 60),
      child: Align(
        alignment: Alignment.topLeft,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1000),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('HOME', style: mono(11)),
              const SizedBox(height: 8),
              Text('Hello, $name', style: serif(34)),
              const SizedBox(height: 22),
              InkWell(
                key: const ValueKey('home-start-sandbox'),
                borderRadius: BorderRadius.circular(16),
                onTap: shell.openSandbox,
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(24),
                  decoration: BoxDecoration(
                    color: Paper.card,
                    border: Border.all(color: Paper.accent, width: 1.5),
                    borderRadius: BorderRadius.circular(16),
                  ),
                  child: Row(
                    children: [
                      Container(
                        width: 44,
                        height: 44,
                        decoration: BoxDecoration(
                            color: Paper.accentSoft, borderRadius: BorderRadius.circular(12)),
                        child: const Icon(Icons.bubble_chart_rounded, color: Paper.accent),
                      ),
                      const SizedBox(width: 16),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('Start a Sandbox chat', style: serif(21)),
                            const SizedBox(height: 4),
                            Text('Ask anything. No syllabus, no set topic.',
                                style: sans(13.5, color: Paper.muted)),
                          ],
                        ),
                      ),
                      const Icon(Icons.arrow_forward_rounded, color: Paper.accent),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 32),
              Row(children: [
                Text('Your feed', style: serif(21)),
                const SizedBox(width: 12),
                const ComingSoonPill(),
              ]),
              const SizedBox(height: 6),
              Text(
                'Where you left off, suggestions from what you have learned, and things to explore.',
                style: sans(13.5, color: Paper.muted),
              ),
              const SizedBox(height: 16),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final c in ['All', 'Continue', 'For you', 'Just to explore'])
                    Chip(
                      label: Text(c, style: sans(12.5, color: Paper.faint)),
                      backgroundColor: Paper.sliver,
                      side: const BorderSide(color: Paper.border),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
                    ),
                ],
              ),
              const SizedBox(height: 18),
              LayoutBuilder(builder: (context, c) {
                final columns = c.maxWidth >= 760 ? 3 : (c.maxWidth >= 480 ? 2 : 1);
                const gap = 16.0;
                final width = (c.maxWidth - gap * (columns - 1)) / columns;
                return Wrap(
                  spacing: gap,
                  runSpacing: gap,
                  children: [for (var i = 0; i < 6; i++) _SkeletonCard(width: width)],
                );
              }),
            ],
          ),
        ),
      ),
    );
  }
}

class _SkeletonCard extends StatelessWidget {
  const _SkeletonCard({required this.width});
  final double width;

  @override
  Widget build(BuildContext context) {
    Widget bar(double w, double h) => Container(
        width: w,
        height: h,
        decoration:
            BoxDecoration(color: Paper.border, borderRadius: BorderRadius.circular(4)));
    return SizedBox(
      width: width,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AspectRatio(
            aspectRatio: 16 / 9,
            child: Container(
              decoration: BoxDecoration(
                color: Paper.sliver,
                border: Border.all(color: Paper.border),
                borderRadius: BorderRadius.circular(10),
              ),
            ),
          ),
          const SizedBox(height: 10),
          bar(width * 0.85, 12),
          const SizedBox(height: 7),
          bar(width * 0.5, 10),
        ],
      ),
    );
  }
}
