import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../composer_draft.dart';
import '../feed_api.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/feed_cards.dart';

enum FeedFilter { all, continueChats, forYou, explore }

extension on FeedFilter {
  String get label => switch (this) {
        FeedFilter.all => 'All',
        FeedFilter.continueChats => 'Continue',
        FeedFilter.forYou => 'For you',
        FeedFilter.explore => 'Explore',
      };
}

/// Home: start a chat, pick up an old one, or follow a suggestion. The feed
/// (src/versa/feed.py) is built from what this learner actually asked about;
/// someone new sees only things to explore until they have a history.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  late final FeedApi _feedApi;
  late final ShellState _shell;
  HomeFeed? _feed;
  Object? _error;
  bool _loading = false;
  FeedFilter _filter = FeedFilter.all;
  int _lastTab = ShellState.tabHome;

  @override
  void initState() {
    super.initState();
    _feedApi = FeedApi.of(context.read<AppState>().api);
    _shell = context.read<ShellState>();
    _lastTab = _shell.tab;
    _shell.addListener(_onShellChange);
    _load();
  }

  @override
  void dispose() {
    _shell.removeListener(_onShellChange);
    super.dispose();
  }

  /// Home stays mounted behind the other tabs, so coming back to it is when
  /// the Continue row (and a now-stale feed) gets refreshed.
  void _onShellChange() {
    final tab = _shell.tab;
    if (tab == ShellState.tabHome && _lastTab != ShellState.tabHome) _load();
    _lastTab = tab;
  }

  Future<void> _load({bool refresh = false}) async {
    final learner = context.read<AppState>().learner;
    if (learner == null || _loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final feed = await _feedApi.getFeed(learner.id, refresh: refresh);
      if (!mounted) return;
      setState(() => _feed = feed);
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _continueChat(ChatSummary chat) {
    _shell.openSandboxChat(chat);
    _shell.openSandbox();
  }

  void _startTopic(FeedItem item) {
    final current = _shell.sandbox;
    if (current != null && current.messages.isNotEmpty) _shell.newSandboxChat();
    _shell.openSandbox();
    // After the chat screen has rebuilt for the new chat, so the starter lands
    // in the box that stays on screen.
    WidgetsBinding.instance.addPostFrameCallback((_) => composerDraft.value = item.starter);
  }

  @override
  Widget build(BuildContext context) {
    final name = context.watch<AppState>().learner?.label ?? '';
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(40, 36, 40, 60),
      child: Align(
        alignment: Alignment.topLeft,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1100),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('HOME', style: mono(11)),
              const SizedBox(height: 8),
              Text('Hello, $name', style: serif(34)),
              const SizedBox(height: 22),
              _StartSandboxCard(onTap: _shell.openSandbox),
              const SizedBox(height: 32),
              _feedHeader(),
              const SizedBox(height: 14),
              _chips(),
              const SizedBox(height: 20),
              _body(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _feedHeader() {
    final generated = _feed?.generatedAt;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Text('Your feed', style: serif(21)),
        const SizedBox(width: 12),
        if (generated != null)
          Flexible(
            child: Text('Suggestions updated ${timeAgo(generated)}',
                key: const ValueKey('feed-updated'),
                overflow: TextOverflow.ellipsis,
                style: sans(12.5, color: Paper.faint)),
          ),
        const Spacer(),
        _loading
            ? const Padding(
                padding: EdgeInsets.all(12),
                child: SizedBox(
                    width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
              )
            : IconButton(
                key: const ValueKey('feed-refresh'),
                tooltip: 'New suggestions',
                onPressed: () => _load(refresh: true),
                icon: const Icon(Icons.refresh_rounded, color: Paper.muted),
              ),
      ],
    );
  }

  Widget _chips() {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final f in FeedFilter.values)
          ChoiceChip(
            key: ValueKey('feed-chip-${f.label}'),
            label: Text(f.label,
                style: sans(12.5, color: _filter == f ? Colors.white : Paper.body)),
            selected: _filter == f,
            showCheckmark: false,
            selectedColor: Paper.ink,
            backgroundColor: Paper.sliver,
            side: BorderSide(color: _filter == f ? Paper.ink : Paper.border),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
            onSelected: (_) => setState(() => _filter = f),
          ),
      ],
    );
  }

  Widget _body() {
    final feed = _feed;
    if (feed == null) {
      if (_error != null) {
        return _Notice(
          key: const ValueKey('feed-error'),
          text: "Couldn't load your feed right now.",
          action: TextButton(onPressed: _load, child: const Text('Try again')),
        );
      }
      return FeedGrid(children: [for (var i = 0; i < 6; i++) const SkeletonCard()]);
    }

    final show = {
      FeedFilter.continueChats: _filter == FeedFilter.all || _filter == FeedFilter.continueChats,
      FeedFilter.forYou: _filter == FeedFilter.all || _filter == FeedFilter.forYou,
      FeedFilter.explore: _filter == FeedFilter.all || _filter == FeedFilter.explore,
    };
    final sections = <Widget>[];

    if (!feed.hasHistory && _filter == FeedFilter.all) {
      sections.add(const _Notice(
        key: ValueKey('feed-new-learner'),
        text: "You haven't chatted yet, so these are just things to explore. "
            'Once you have, this feed follows what you ask about.',
      ));
    }

    if (show[FeedFilter.continueChats]! && feed.continueChats.isNotEmpty) {
      sections.add(FeedSection(
        key: const ValueKey('feed-section-continue'),
        title: 'Continue',
        subtitle: 'Pick up where you left off.',
        children: [
          for (final c in feed.continueChats)
            ContinueCard(chat: c, onTap: () => _continueChat(c)),
        ],
      ));
    } else if (_filter == FeedFilter.continueChats) {
      sections.add(const _Notice(text: 'No chats yet. Anything you start shows up here.'));
    }

    if (show[FeedFilter.forYou]! && feed.related.isNotEmpty) {
      sections.add(FeedSection(
        key: const ValueKey('feed-section-related'),
        title: 'For you',
        subtitle: "Next to what you've asked about.",
        children: [
          for (final item in feed.related)
            TopicCard(item: item, related: true, onTap: () => _startTopic(item)),
        ],
      ));
    } else if (_filter == FeedFilter.forYou) {
      sections.add(_Notice(
        text: feed.hasHistory
            ? 'No related topics right now. Try New suggestions.'
            : "Nothing here yet. After a chat or two, topics related to what you asked will show up here.",
      ));
    }

    if (show[FeedFilter.explore]! && feed.explore.isNotEmpty) {
      sections.add(FeedSection(
        key: const ValueKey('feed-section-explore'),
        title: 'Just to explore',
        subtitle: 'Something different, just to learn.',
        children: [
          for (final item in feed.explore)
            TopicCard(item: item, related: false, onTap: () => _startTopic(item)),
        ],
      ));
    }

    if (sections.isEmpty) {
      sections.add(_Notice(
        text: "No suggestions right now.",
        action: TextButton(onPressed: () => _load(refresh: true), child: const Text('Try again')),
      ));
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final s in sections) ...[s, const SizedBox(height: 30)],
      ],
    );
  }
}

class _StartSandboxCard extends StatelessWidget {
  const _StartSandboxCard({required this.onTap});
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      key: const ValueKey('home-start-sandbox'),
      borderRadius: BorderRadius.circular(16),
      onTap: onTap,
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
              decoration:
                  BoxDecoration(color: Paper.accentSoft, borderRadius: BorderRadius.circular(12)),
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
    );
  }
}

class _Notice extends StatelessWidget {
  const _Notice({super.key, required this.text, this.action});
  final String text;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
      decoration: BoxDecoration(
        color: Paper.sliver,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          Expanded(child: Text(text, style: sans(13.5, color: Paper.body, height: 1.45))),
          ?action,
        ],
      ),
    );
  }
}
