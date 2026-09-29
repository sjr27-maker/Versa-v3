import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../billing/sparks_widgets.dart';
import '../models.dart';
import 'directions_lab_screen.dart';
import 'profile_screen.dart';
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
                        Text(
                            (app.authConfig?.required ?? false)
                                ? (app.email ?? 'Signed in')
                                : 'Memory is kept under this name.',
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
                    child: Text((app.authConfig?.required ?? false) ? 'Sign out' : 'Switch learner'),
                  ),
                ]),
              ]),
              if ((app.authConfig?.required ?? false) && learner != null)
                card([_ProfileCard(learnerId: learner.id)]),
              const SparksPlanCard(),
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
                Material(
                  color: Colors.transparent,
                  child: SwitchListTile(
                    key: const ValueKey('stage-panel-switch'),
                    contentPadding: EdgeInsets.zero,
                    activeThumbColor: Colors.white,
                    activeTrackColor: Paper.accent,
                    title: Text('Stage panel', style: sans(14)),
                    subtitle: Text(
                        'Reserves space for an animation reactive to the chat (not built yet) '
                        'in any mode — same as the Animations knob in Sandbox.',
                        style: sans(12.5, color: Paper.muted)),
                    value: app.showStagePanel,
                    onChanged: app.setShowStagePanel,
                  ),
                ),
              ]),
              card([
                Text('Design lab', style: serif(19)),
                const SizedBox(height: 6),
                Text('Three ways to show "where this could go" -- a hand of three, a compass, a '
                    'constellation. Design only: sample cards, nothing is recorded.',
                    style: sans(12.5, color: Paper.muted)),
                const SizedBox(height: 10),
                Align(
                  alignment: Alignment.centerLeft,
                  child: OutlinedButton.icon(
                    key: const ValueKey('open-design-lab'),
                    onPressed: () => Navigator.of(context)
                        .push(MaterialPageRoute<void>(builder: (_) => const DirectionsLabScreen())),
                    icon: const Icon(Icons.explore_outlined, size: 16),
                    label: const Text('Open design lab'),
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


/// What the learner told Versa at sign-up (profiles.py), and a way to change it.
class _ProfileCard extends StatefulWidget {
  const _ProfileCard({required this.learnerId});
  final String learnerId;

  @override
  State<_ProfileCard> createState() => _ProfileCardState();
}

class _ProfileCardState extends State<_ProfileCard> {
  late Future<LearnerProfile?> _profile;

  @override
  void initState() {
    super.initState();
    _profile = context.read<AppState>().api.getProfile(widget.learnerId);
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<LearnerProfile?>(
        future: _profile,
        builder: (context, snap) {
          final profile = snap.data;
          final summary = profile?.summary ?? '';
          return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Expanded(child: Text('Your profile', style: serif(19))),
              TextButton(
                key: const ValueKey('edit-profile'),
                onPressed: snap.connectionState != ConnectionState.done
                    ? null
                    : () async {
                        final saved = await Navigator.of(context).push<LearnerProfile>(MaterialPageRoute(
                          builder: (_) => ProfileScreen(editing: true, initial: profile),
                        ));
                        if (saved != null && mounted) setState(() => _profile = Future.value(saved));
                      },
                child: const Text('Edit'),
              ),
            ]),
            const SizedBox(height: 6),
            Text(
              snap.connectionState != ConnectionState.done
                  ? 'Loading…'
                  : summary.isEmpty
                      ? 'Versa uses this to start at your level.'
                      : summary,
              key: const ValueKey('profile-summary'),
              style: sans(13.5, color: Paper.muted, height: 1.4),
            ),
          ]);
        },
      );
}
