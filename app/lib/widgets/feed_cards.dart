import 'package:flutter/material.dart';

import '../feed_api.dart';
import '../models.dart';
import '../theme.dart';

/// "just now", "5 min ago", "3 h ago", "2 days ago".
String timeAgo(DateTime when, {DateTime? now}) {
  final d = (now ?? DateTime.now()).difference(when.toLocal());
  if (d.inMinutes < 1) return 'just now';
  if (d.inMinutes < 60) return '${d.inMinutes} min ago';
  if (d.inHours < 24) return '${d.inHours} h ago';
  final days = d.inDays;
  return days == 1 ? 'yesterday' : '$days days ago';
}

const _tileColors = [
  Color(0xFFC85A2E), // accent
  Color(0xFF6D9A5C), // olive
  Color(0xFF4F6D8A), // slate
  Color(0xFF7A4E6B), // plum
  Color(0xFF3F7F77), // teal
  Color(0xFFA8843F), // ochre
];

Color _tileColor(String seed) =>
    _tileColors[seed.codeUnits.fold<int>(7, (h, c) => (h * 31 + c) & 0x7fffffff) % _tileColors.length];

/// Cards laid out 3 / 2 / 1 across depending on the width available.
class FeedGrid extends StatelessWidget {
  const FeedGrid({super.key, required this.children});
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(builder: (context, c) {
      final columns = c.maxWidth >= 760 ? 3 : (c.maxWidth >= 480 ? 2 : 1);
      const gap = 18.0;
      final width = (c.maxWidth - gap * (columns - 1)) / columns;
      return Wrap(
        spacing: gap,
        runSpacing: 22,
        children: [for (final child in children) SizedBox(width: width, child: child)],
      );
    });
  }
}

class FeedSection extends StatelessWidget {
  const FeedSection({super.key, required this.title, required this.subtitle, required this.children});
  final String title;
  final String subtitle;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: serif(18)),
        const SizedBox(height: 2),
        Text(subtitle, style: sans(13, color: Paper.muted)),
        const SizedBox(height: 14),
        FeedGrid(children: children),
      ],
    );
  }
}

class _Tile extends StatelessWidget {
  const _Tile({required this.seed, required this.icon, required this.text});
  final String seed;
  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    final color = _tileColor(seed);
    return AspectRatio(
      aspectRatio: 16 / 9,
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(12),
          gradient: LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: [color, Color.lerp(color, Colors.black, 0.28)!],
          ),
        ),
        child: Stack(
          children: [
            Positioned(
              right: -6,
              bottom: -10,
              child: Icon(icon, size: 84, color: Colors.white.withValues(alpha: 0.16)),
            ),
            Align(
              alignment: Alignment.bottomLeft,
              child: Text(
                text,
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: serif(17, color: Colors.white, height: 1.2),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CardShell extends StatefulWidget {
  const _CardShell({required this.cardKey, required this.onTap, required this.child});
  final Key cardKey;
  final VoidCallback onTap;
  final Widget child;

  @override
  State<_CardShell> createState() => _CardShellState();
}

class _CardShellState extends State<_CardShell> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        key: widget.cardKey,
        behavior: HitTestBehavior.opaque,
        onTap: widget.onTap,
        child: AnimatedScale(
          duration: const Duration(milliseconds: 140),
          scale: _hover ? 1.015 : 1,
          child: widget.child,
        ),
      ),
    );
  }
}

class ContinueCard extends StatelessWidget {
  const ContinueCard({super.key, required this.chat, required this.onTap});
  final ChatSummary chat;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final preview = chat.preview ?? 'Chat';
    final messages = chat.turnCount == 1 ? '1 message' : '${chat.turnCount} messages';
    return _CardShell(
      cardKey: ValueKey('feed-continue-${chat.sessionId}'),
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _Tile(seed: chat.sessionId, icon: Icons.forum_outlined, text: '“$preview”'),
          const SizedBox(height: 10),
          Text(preview, maxLines: 2, overflow: TextOverflow.ellipsis, style: sans(14.5, weight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text('$messages · ${timeAgo(chat.lastActivityAt)}', style: sans(12.5, color: Paper.muted)),
        ],
      ),
    );
  }
}

class TopicCard extends StatelessWidget {
  const TopicCard({super.key, required this.item, required this.related, required this.onTap});
  final FeedItem item;
  final bool related;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return _CardShell(
      cardKey: ValueKey('feed-topic-${item.title}'),
      onTap: onTap,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _Tile(
            seed: item.title,
            icon: related ? Icons.auto_awesome_outlined : Icons.explore_outlined,
            text: item.title,
          ),
          const SizedBox(height: 10),
          Text(item.title, maxLines: 2, overflow: TextOverflow.ellipsis, style: sans(14.5, weight: FontWeight.w600)),
          const SizedBox(height: 4),
          Text(item.hook, maxLines: 2, overflow: TextOverflow.ellipsis, style: sans(13, color: Paper.body, height: 1.4)),
          const SizedBox(height: 6),
          Row(
            children: [
              Icon(related ? Icons.history_rounded : Icons.lightbulb_outline_rounded,
                  size: 14, color: related ? Paper.accent : Paper.faint),
              const SizedBox(width: 5),
              Expanded(
                child: Text(item.reason,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: sans(12, color: related ? Paper.accentDark : Paper.faint)),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class SkeletonCard extends StatelessWidget {
  const SkeletonCard({super.key});

  @override
  Widget build(BuildContext context) {
    Widget bar(double widthFactor, double h) => FractionallySizedBox(
          widthFactor: widthFactor,
          child: Container(
            height: h,
            decoration: BoxDecoration(color: Paper.border, borderRadius: BorderRadius.circular(4)),
          ),
        );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        AspectRatio(
          aspectRatio: 16 / 9,
          child: Container(
            decoration: BoxDecoration(
              color: Paper.sliver,
              border: Border.all(color: Paper.border),
              borderRadius: BorderRadius.circular(12),
            ),
          ),
        ),
        const SizedBox(height: 10),
        bar(0.85, 12),
        const SizedBox(height: 7),
        bar(0.5, 10),
      ],
    );
  }
}
