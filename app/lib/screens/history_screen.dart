import 'package:flutter/material.dart';

import '../widgets/placeholder_page.dart';

class HistoryScreen extends StatelessWidget {
  const HistoryScreen({super.key});

  @override
  Widget build(BuildContext context) => const PlaceholderPage(
        kicker: 'History',
        title: 'Everything you have worked through',
        blurb: 'Your past chats, in one place.',
        icon: Icons.history_rounded,
        planned: [
          'Search past chats by topic, date, or a phrase from the conversation',
          'Filter by mode',
          'Reopen a chat and pick up where you left off',
        ],
      );
}
