import 'package:flutter/material.dart';

import '../widgets/placeholder_page.dart';

class ThinkingStyleScreen extends StatelessWidget {
  const ThinkingStyleScreen({super.key});

  @override
  Widget build(BuildContext context) => const PlaceholderPage(
        kicker: 'Thinking style',
        title: 'What I have noticed about how you think',
        blurb:
            'Versa already remembers how past questions of yours were resolved, in every chat. '
            'This page will show what it has learned about you, and only what it can back with evidence.',
        icon: Icons.psychology_alt_outlined,
        planned: [
          'Patterns confirmed across many sessions, each with the evidence behind it',
          'Things still forming, and how much more it needs to be sure',
          'Say "sounds right" or "not really" to correct it',
          'Export what it knows about you',
        ],
      );
}
