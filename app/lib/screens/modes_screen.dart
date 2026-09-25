import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import '../widgets/placeholder_page.dart';

/// The four ways to use Versa. Sandbox and Learn a topic are live; the others
/// are visible placeholders that say so.
class ModesScreen extends StatelessWidget {
  const ModesScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final shell = context.read<ShellState>();
    void notBuilt(String name) => ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text('$name isn\'t built yet. Sandbox is live.')));
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(40, 36, 40, 60),
      child: Align(
        alignment: Alignment.topLeft,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1000),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('CHOOSE HOW YOU\'LL LEARN', style: mono(11)),
              const SizedBox(height: 8),
              Text('Four ways to think together', style: serif(34)),
              const SizedBox(height: 28),
              LayoutBuilder(builder: (context, c) {
                final columns = c.maxWidth >= 640 ? 2 : 1;
                const gap = 18.0;
                final width = (c.maxWidth - gap * (columns - 1)) / columns;
                return Wrap(
                  spacing: gap,
                  runSpacing: gap,
                  children: [
                    _ModeCard(
                      width: width,
                      cardKey: 'mode-learn',
                      icon: Icons.menu_book_outlined,
                      title: 'Learn a topic',
                      blurb: 'Search a subject or bring a PDF or link; pick the branches you want, '
                          'then work through the chapters with your progress tracked.',
                      live: true,
                      onTap: shell.openTopics,
                    ),
                    _ModeCard(
                      width: width,
                      cardKey: 'mode-sandbox',
                      icon: Icons.bubble_chart_rounded,
                      title: 'Sandbox',
                      blurb: 'No set topic. Ask anything, get quick answers.',
                      live: true,
                      onTap: shell.openSandbox,
                    ),
                    _ModeCard(
                      width: width,
                      cardKey: 'mode-exam',
                      icon: Icons.fact_check_outlined,
                      title: 'Exam preparation',
                      blurb: 'A syllabus and a date; quizzes and mock tests to prepare.',
                      live: false,
                      onTap: () => notBuilt('Exam preparation'),
                    ),
                    _ModeCard(
                      width: width,
                      cardKey: 'mode-study',
                      icon: Icons.groups_outlined,
                      title: 'Study with others',
                      blurb: 'Learn together with friends or matched peers.',
                      live: false,
                      onTap: () => notBuilt('Study with others'),
                    ),
                  ],
                );
              }),
            ],
          ),
        ),
      ),
    );
  }
}

class _ModeCard extends StatefulWidget {
  const _ModeCard({
    required this.width,
    required this.cardKey,
    required this.icon,
    required this.title,
    required this.blurb,
    required this.live,
    required this.onTap,
  });

  final double width;
  final String cardKey;
  final IconData icon;
  final String title;
  final String blurb;
  final bool live;
  final VoidCallback onTap;

  @override
  State<_ModeCard> createState() => _ModeCardState();
}

class _ModeCardState extends State<_ModeCard> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final live = widget.live;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        key: ValueKey(widget.cardKey),
        onTap: widget.onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          width: widget.width,
          constraints: const BoxConstraints(minHeight: 210),
          padding: const EdgeInsets.all(26),
          decoration: BoxDecoration(
            color: Paper.card,
            border: Border.all(
              color: live || _hover ? Paper.accent : Paper.border,
              width: live ? 1.5 : 1,
            ),
            borderRadius: BorderRadius.circular(14),
            boxShadow: _hover
                ? const [BoxShadow(color: Color(0x1FC85A2E), blurRadius: 24, offset: Offset(0, 10))]
                : null,
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Icon(widget.icon, size: 30, color: live ? Paper.accent : Paper.faint),
                  live
                      ? Container(
                          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
                          decoration: BoxDecoration(
                            color: const Color(0xFFEAF3E6),
                            border: Border.all(color: const Color(0xFFCFE2C7)),
                            borderRadius: BorderRadius.circular(100),
                          ),
                          child: Text('LIVE',
                              style: mono(10, color: Paper.olive, weight: FontWeight.w600)),
                        )
                      : const ComingSoonPill(),
                ],
              ),
              const SizedBox(height: 18),
              Text(widget.title, style: serif(25, color: live ? Paper.ink : Paper.muted)),
              const SizedBox(height: 8),
              Text(widget.blurb, style: sans(13.5, color: Paper.body, height: 1.55)),
            ],
          ),
        ),
      ),
    );
  }
}
