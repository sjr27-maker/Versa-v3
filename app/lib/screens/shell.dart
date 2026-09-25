import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import '../widgets/collapsed_rail.dart';
import 'history_screen.dart';
import 'home_screen.dart';
import 'modes_screen.dart';
import 'sandbox_screen.dart';
import 'settings_screen.dart';
import 'thinking_style_screen.dart';
import '../room/rooms_root.dart';
import '../topic/topics_root.dart';

class _Destination {
  const _Destination(this.label, this.icon, this.selectedIcon);
  final String label;
  final IconData icon;
  final IconData selectedIcon;
}

const _destinations = [
  _Destination('Home', Icons.home_outlined, Icons.home_rounded),
  _Destination('Modes', Icons.dashboard_outlined, Icons.dashboard_rounded),
  _Destination('History', Icons.history_rounded, Icons.history_rounded),
  _Destination('Thinking style', Icons.psychology_alt_outlined, Icons.psychology_alt_rounded),
  _Destination('Settings', Icons.settings_outlined, Icons.settings_rounded),
];

/// The frame around everything: a left rail on a wide screen, a bottom bar on a
/// narrow one (so the same app works on a phone). Pages stay alive underneath
/// (an IndexedStack), so a chat isn't lost when you look at Settings.
class Shell extends StatelessWidget {
  const Shell({super.key});

  static const _wideBreakpoint = 900.0;

  @override
  Widget build(BuildContext context) {
    final shell = context.watch<ShellState>();
    final pages = IndexedStack(
      index: shell.tab,
      children: [
        const HomeScreen(),
        shell.inSandbox
            ? const SandboxScreen()
            : shell.inTopics
                ? const TopicsRoot()
                : (shell.inRooms ? const RoomsRoot() : const ModesScreen()),
        const HistoryScreen(),
        const ThinkingStyleScreen(),
        const SettingsScreen(),
      ],
    );
    return LayoutBuilder(builder: (context, c) {
      if (c.maxWidth >= _wideBreakpoint) {
        return Scaffold(
          backgroundColor: Paper.page,
          body: Row(
            children: [
              shell.navRailCollapsed
                  ? CollapsedRailStrip(
                      icon: Icons.menu_rounded,
                      tooltip: 'Show menu',
                      onExpand: shell.toggleNavRailCollapsed,
                    )
                  : _Rail(
                      selected: shell.tab,
                      onSelect: shell.goTab,
                      onCollapse: shell.toggleNavRailCollapsed,
                    ),
              Expanded(
                child: Container(
                  decoration: const BoxDecoration(
                    color: Paper.surface,
                    border: Border(left: BorderSide(color: Paper.border)),
                  ),
                  child: pages,
                ),
              ),
            ],
          ),
        );
      }
      return Scaffold(
        backgroundColor: Paper.surface,
        body: SafeArea(child: pages),
        bottomNavigationBar: NavigationBar(
          selectedIndex: shell.tab,
          onDestinationSelected: shell.goTab,
          backgroundColor: Paper.sliver,
          indicatorColor: Paper.accentSoft,
          labelBehavior: NavigationDestinationLabelBehavior.alwaysShow,
          destinations: [
            for (final d in _destinations)
              NavigationDestination(
                icon: Icon(d.icon),
                selectedIcon: Icon(d.selectedIcon, color: Paper.accent),
                label: d.label == 'Thinking style' ? 'Style' : d.label,
              ),
          ],
        ),
      );
    });
  }
}

class _Rail extends StatelessWidget {
  const _Rail({required this.selected, required this.onSelect, required this.onCollapse});
  final int selected;
  final ValueChanged<int> onSelect;
  final VoidCallback onCollapse;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 208,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 28, 16, 20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(left: 10, bottom: 24),
              child: Row(
                children: [
                  Expanded(child: Text('Versa', style: serif(26))),
                  IconButton(
                    key: const ValueKey('nav-rail-collapse'),
                    tooltip: 'Minimize menu',
                    visualDensity: VisualDensity.compact,
                    onPressed: onCollapse,
                    icon: const Icon(Icons.chevron_left_rounded, size: 18, color: Paper.faint),
                  ),
                ],
              ),
            ),
            for (var i = 0; i < _destinations.length; i++)
              _RailItem(
                key: ValueKey('nav-${_destinations[i].label}'),
                destination: _destinations[i],
                selected: i == selected,
                onTap: () => onSelect(i),
              ),
            const Spacer(),
            Padding(
              padding: const EdgeInsets.only(left: 10),
              child: Text('V0.1 · LOCAL', style: mono(9.5)),
            ),
          ],
        ),
      ),
    );
  }
}

class _RailItem extends StatefulWidget {
  const _RailItem({
    super.key,
    required this.destination,
    required this.selected,
    required this.onTap,
  });
  final _Destination destination;
  final bool selected;
  final VoidCallback onTap;

  @override
  State<_RailItem> createState() => _RailItemState();
}

class _RailItemState extends State<_RailItem> {
  bool _hover = false;

  @override
  Widget build(BuildContext context) {
    final d = widget.destination;
    final color = widget.selected ? Paper.accent : Paper.body;
    return MouseRegion(
      cursor: SystemMouseCursors.click,
      onEnter: (_) => setState(() => _hover = true),
      onExit: (_) => setState(() => _hover = false),
      child: GestureDetector(
        onTap: widget.onTap,
        behavior: HitTestBehavior.opaque,
        child: Container(
          margin: const EdgeInsets.only(bottom: 4),
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 11),
          decoration: BoxDecoration(
            color: widget.selected
                ? Paper.accentSoft
                : (_hover ? const Color(0x0F000000) : Colors.transparent),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Row(
            children: [
              Icon(widget.selected ? d.selectedIcon : d.icon, size: 19, color: color),
              const SizedBox(width: 12),
              Flexible(
                child: Text(d.label,
                    overflow: TextOverflow.ellipsis,
                    style: sans(13.5,
                        color: color,
                        weight: widget.selected ? FontWeight.w600 : FontWeight.w400)),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
