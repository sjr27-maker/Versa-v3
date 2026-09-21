import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import '../widgets/placeholder_page.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late Future<Map<String, dynamic>> _health;

  @override
  void initState() {
    super.initState();
    _health = context.read<AppState>().api.health();
  }

  @override
  Widget build(BuildContext context) {
    final app = context.watch<AppState>();
    final shell = context.read<ShellState>();
    final learner = app.learner;
    Widget card(List<Widget> children) => Container(
          width: double.infinity,
          margin: const EdgeInsets.only(bottom: 16),
          padding: const EdgeInsets.all(22),
          decoration: BoxDecoration(
            color: Paper.card,
            border: Border.all(color: Paper.border),
            borderRadius: BorderRadius.circular(14),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
        );

    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(40, 36, 40, 60),
      child: Align(
        alignment: Alignment.topLeft,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('SETTINGS', style: mono(11)),
              const SizedBox(height: 8),
              Text('Settings', style: serif(34)),
              const SizedBox(height: 24),
              card([
                Row(children: [
                  Container(
                    width: 52,
                    height: 52,
                    alignment: Alignment.center,
                    decoration:
                        const BoxDecoration(color: Paper.accent, shape: BoxShape.circle),
                    child: Text(
                      (learner?.label.isNotEmpty ?? false) ? learner!.label[0].toUpperCase() : '?',
                      style: sans(22, color: Colors.white, weight: FontWeight.w600),
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(learner?.label ?? '', key: const ValueKey('settings-name'),
                            style: serif(21)),
                        const SizedBox(height: 2),
                        Text('Memory is kept under this name.',
                            style: sans(12.5, color: Paper.muted)),
                      ],
                    ),
                  ),
                  OutlinedButton(
                    key: const ValueKey('switch-learner'),
                    onPressed: () async {
                      shell.reset();
                      await app.signOut();
                    },
                    style: OutlinedButton.styleFrom(
                      foregroundColor: Paper.ink,
                      side: const BorderSide(color: Paper.borderStrong),
                      shape:
                          RoundedRectangleBorder(borderRadius: BorderRadius.circular(100)),
                    ),
                    child: const Text('Switch learner'),
                  ),
                ]),
              ]),
              card([
                Text('Connection', style: serif(19)),
                const SizedBox(height: 12),
                Text(app.api.baseUrl, style: mono(12, color: Paper.body)),
                const SizedBox(height: 10),
                FutureBuilder<Map<String, dynamic>>(
                  future: _health,
                  builder: (context, snap) {
                    late final String text;
                    late final Color color;
                    if (snap.connectionState != ConnectionState.done) {
                      text = 'Checking…';
                      color = Paper.faint;
                    } else if (snap.hasError) {
                      text = 'Not reachable';
                      color = Paper.danger;
                    } else {
                      text = snap.data!['llm'] == 'live'
                          ? 'Connected · live Gemini'
                          : 'Connected · stub (no real model)';
                      color = Paper.olive;
                    }
                    return Row(children: [
                      Container(
                          width: 8,
                          height: 8,
                          decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
                      const SizedBox(width: 8),
                      Text(text, key: const ValueKey('health-text'),
                          style: sans(13, color: Paper.muted)),
                      const Spacer(),
                      TextButton(
                        onPressed: () => setState(() => _health = app.api.health()),
                        child: const Text('Check again'),
                      ),
                    ]);
                  },
                ),
              ]),
              card([
                Text('Preferences', style: serif(19)),
                const SizedBox(height: 6),
                Material(
                  color: Colors.transparent,
                  child: SwitchListTile(
                    key: const ValueKey('timing-switch'),
                    contentPadding: EdgeInsets.zero,
                    activeThumbColor: Colors.white,
                    activeTrackColor: Paper.accent,
                    title: Text('Show timing under answers', style: sans(14)),
                    subtitle: Text(
                        'How long until the first words and the whole answer, measured on this device.',
                        style: sans(12.5, color: Paper.muted)),
                    value: app.showTiming,
                    onChanged: app.setShowTiming,
                  ),
                ),
              ]),
              card([
                Row(children: [
                  Text('Coming later', style: serif(19)),
                  const SizedBox(width: 12),
                  const ComingSoonPill(),
                ]),
                const SizedBox(height: 10),
                for (final (title, detail) in const [
                  ('Learning defaults', 'Animations, depth, cross-topic memory, reminders.'),
                  ('Model of you', 'Export what Versa remembers, or pause its learning.'),
                  ('Appearance', 'Light, dark and more.'),
                ])
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(title, style: sans(14, color: Paper.faint)),
                        Text(detail, style: sans(12.5, color: Paper.faint)),
                      ],
                    ),
                  ),
              ]),
            ],
          ),
        ),
      ),
    );
  }
}
