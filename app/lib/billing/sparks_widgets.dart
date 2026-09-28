import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../theme.dart';
import 'sparks.dart';

String _clock(DateTime t) {
  final h = t.hour % 12 == 0 ? 12 : t.hour % 12;
  final m = t.minute.toString().padLeft(2, '0');
  return '$h:$m ${t.hour < 12 ? 'am' : 'pm'}';
}

String _day(DateTime t) {
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return '${t.day} ${months[t.month - 1]}';
}

String _actionLabel(String action) => switch (action) {
      'answer' => 'an answer',
      'explore_topic' => 'exploring a topic',
      'expand_topic' => 'more branches',
      'build_course' => 'building a course',
      'create_exam' => 'setting up an exam',
      'unit_quiz' => 'a unit quiz',
      'mock_test' => 'a mock test',
      _ => action.replaceAll('_', ' '),
    };

String _until(DateTime t, DateTime now) {
  final d = t.difference(now);
  if (d.inMinutes < 1) return 'under a minute';
  final h = d.inHours;
  final m = d.inMinutes % 60;
  if (h == 0) return '$m min';
  return m == 0 ? '$h hr' : '$h hr $m min';
}

/// The usage meter, in the spirit of Claude's usage page: how much is left
/// of this plan's tank, when it refills, what today used, and the study
/// streak that pays Sparks back.
class SparksUsageMeter extends StatelessWidget {
  const SparksUsageMeter({super.key, required this.status, this.now});

  final SparksStatus status;

  /// For tests; defaults to the clock.
  final DateTime? now;

  @override
  Widget build(BuildContext context) {
    final s = status;
    final low = s.cap > 0 && s.balance <= (s.cap * 0.2).floor();
    final barColor = low ? Paper.warn : Paper.accent;
    final clock = now ?? DateTime.now();
    final refill = s.nextRefillAt;
    final full = s.balance >= s.cap;

    final left = s.balance > s.cap ? '${s.balance} left · above the ${s.cap} cap' : '${s.balance} of ${s.cap} left';
    final refillLine = full
        ? 'Full. Refills resume once you spend some'
        : refill == null
            ? 'Refills +${s.refillAmount} twice a day'
            : 'Refills +${s.refillAmount} in ${_until(refill, clock)}';
    final todayLine = '${s.spentToday} used today';

    final goal = s.streakGoal;
    final progress = s.streakProgress;
    final bonus = s.rewardFor('study_streak');
    final String streakTitle;
    final String streakHint;
    if (s.streakDays == 0) {
      streakTitle = 'No study streak yet';
      streakHint = 'Learn something today to start one. Every $goal days in a row earns +$bonus Sparks.';
    } else {
      streakTitle = '${s.streakDays}-day study streak';
      if (!s.studiedToday) {
        streakHint = 'Study today to keep it going.';
      } else if (progress == goal) {
        streakHint = 'Streak bonus earned today: +$bonus Sparks.';
      } else {
        final more = goal - progress;
        streakHint = '$more more day${more == 1 ? '' : 's'} for +$bonus Sparks.';
      }
    }

    return Column(key: const ValueKey('sparks-meter'), crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(children: [
        Text('Sparks', style: sans(13.5, weight: FontWeight.w600)),
        const Spacer(),
        Text(left, key: const ValueKey('sparks-meter-left'), style: sans(12.5, color: Paper.body)),
      ]),
      const SizedBox(height: 8),
      ClipRRect(
        borderRadius: BorderRadius.circular(6),
        child: LinearProgressIndicator(
          value: s.fill,
          minHeight: 8,
          backgroundColor: Paper.border,
          valueColor: AlwaysStoppedAnimation(barColor),
        ),
      ),
      const SizedBox(height: 6),
      Text('$refillLine · $todayLine', key: const ValueKey('sparks-meter-refill'), style: sans(12, color: Paper.muted)),
      const SizedBox(height: 14),
      Row(children: [
        Icon(Icons.local_fire_department_rounded,
            size: 20, color: s.streakDays > 0 && s.studiedToday ? Paper.accent : Paper.faint),
        const SizedBox(width: 6),
        Expanded(
          child: Text(streakTitle, key: const ValueKey('sparks-streak'), style: sans(13.5, weight: FontWeight.w600)),
        ),
        for (var i = 0; i < goal; i++)
          Container(
            width: 10,
            height: 10,
            margin: const EdgeInsets.only(left: 5),
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: i < progress ? Paper.accent : Colors.transparent,
              border: Border.all(color: i < progress ? Paper.accent : Paper.borderStrong, width: 1.5),
            ),
          ),
      ]),
      const SizedBox(height: 4),
      Padding(
        padding: const EdgeInsets.only(left: 26),
        child: Text(streakHint, style: sans(12, color: Paper.muted)),
      ),
    ]);
  }
}

/// A small "⚡ 14" pill: the balance, tap for plans.
class SparksChip extends StatelessWidget {
  const SparksChip({super.key});

  @override
  Widget build(BuildContext context) {
    final status = context.watch<SparksState>().status;
    if (status == null) return const SizedBox.shrink();
    return Tooltip(
      message: '${status.planName} · ${status.balance} of ${status.cap} Sparks · '
          '${status.streakDays > 0 ? '${status.streakDays}-day streak' : 'no streak yet'}',
      child: InkWell(
        key: const ValueKey('sparks-chip'),
        borderRadius: BorderRadius.circular(20),
        onTap: () => showSparksSheet(context, const PaywallRequest(action: 'upgrade')),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 6),
          decoration: BoxDecoration(
            color: Paper.accentSoft,
            border: Border.all(color: Paper.accentLine),
            borderRadius: BorderRadius.circular(20),
          ),
          child: IntrinsicWidth(
            child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Row(mainAxisSize: MainAxisSize.min, children: [
                const Icon(Icons.bolt_rounded, size: 16, color: Paper.accent),
                const SizedBox(width: 4),
                Text('${status.balance}', style: sans(13, color: Paper.accentDark, weight: FontWeight.w600)),
                if (status.isPaid) ...[
                  const SizedBox(width: 6),
                  Text(status.tier == 'plus' ? 'PLUS' : 'PASS', style: mono(9.5, color: Paper.accent)),
                ],
                if (status.streakDays > 0) ...[
                  const SizedBox(width: 6),
                  Icon(Icons.local_fire_department_rounded,
                      size: 14, color: status.studiedToday ? Paper.accent : Paper.faint),
                  Text('${status.streakDays}', style: sans(11.5, color: Paper.accentDark, weight: FontWeight.w600)),
                ],
              ]),
              const SizedBox(height: 4),
              // plain boxes (not a LinearProgressIndicator) so the pill can
              // size itself to its label
              Container(
                key: const ValueKey('sparks-chip-bar'),
                height: 3,
                alignment: Alignment.centerLeft,
                decoration: BoxDecoration(color: Paper.accentLine, borderRadius: BorderRadius.circular(2)),
                child: FractionallySizedBox(
                  widthFactor: status.fill,
                  child: Container(
                    decoration: BoxDecoration(color: Paper.accent, borderRadius: BorderRadius.circular(2)),
                  ),
                ),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}

/// Opens the Sparks sheet whenever anything asks for it (a 402 from an API
/// client, a `paywall` chat frame), and shows reward notes as snackbars.
class SparksPaywallListener extends StatefulWidget {
  const SparksPaywallListener({super.key, required this.child});
  final Widget child;

  @override
  State<SparksPaywallListener> createState() => _SparksPaywallListenerState();
}

class _SparksPaywallListenerState extends State<SparksPaywallListener> {
  StreamSubscription<PaywallRequest>? _requests;
  StreamSubscription<String>? _notes;
  bool _open = false;

  @override
  void initState() {
    super.initState();
    _requests = PaywallHub.requests.listen(_show);
    _notes = context.read<SparksState>().notes.listen((note) {
      if (!mounted) return;
      ScaffoldMessenger.maybeOf(context)
        ?..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(duration: const Duration(seconds: 3), content: Text(note)));
    });
  }

  Future<void> _show(PaywallRequest request) async {
    if (!mounted || _open) return;
    _open = true;
    try {
      await showSparksSheet(context, request);
    } finally {
      _open = false;
    }
  }

  @override
  void dispose() {
    _requests?.cancel();
    _notes?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}

Future<void> showSparksSheet(BuildContext context, PaywallRequest request) {
  final sparks = context.read<SparksState>();
  sparks.refresh();
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    backgroundColor: Paper.surface,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
    builder: (_) => ChangeNotifierProvider<SparksState>.value(
      value: sparks,
      child: _SparksSheet(request: request),
    ),
  );
}

class _SparksSheet extends StatefulWidget {
  const _SparksSheet({required this.request});
  final PaywallRequest request;

  @override
  State<_SparksSheet> createState() => _SparksSheetState();
}

class _SparksSheetState extends State<_SparksSheet> {
  bool _busy = false;
  String? _message;
  final Map<String, String?> _prices = {};

  @override
  void initState() {
    super.initState();
    final billing = context.read<SparksState>().billing;
    if (billing.supported) {
      for (final id in const ['exam_pass', 'sparks']) {
        billing.priceOf(id).then((p) {
          if (mounted) setState(() => _prices[id] = p);
        });
      }
    }
  }

  Future<void> _run(Future<String> Function() job) async {
    setState(() {
      _busy = true;
      _message = null;
    });
    final message = await job();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _message = message;
    });
  }

  @override
  Widget build(BuildContext context) {
    final sparks = context.watch<SparksState>();
    final status = sparks.status;
    final billing = sparks.billing;
    final r = widget.request;
    final refused = r.action != 'upgrade';
    final next = r.nextRefillAt ?? status?.nextRefillAt;

    Widget option({
      required Key key,
      required IconData icon,
      required String title,
      required String subtitle,
      required VoidCallback? onTap,
      bool highlight = false,
    }) =>
        Padding(
          padding: const EdgeInsets.only(bottom: 10),
          child: Material(
            color: highlight ? Paper.accentSoft : Paper.card,
            shape: RoundedRectangleBorder(
              side: BorderSide(color: highlight ? Paper.accent : Paper.border),
              borderRadius: BorderRadius.circular(12),
            ),
            child: InkWell(
              key: key,
              borderRadius: BorderRadius.circular(12),
              onTap: _busy ? null : onTap,
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Row(children: [
                  Icon(icon, color: Paper.accent),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                      Text(title, style: sans(14.5, weight: FontWeight.w600)),
                      const SizedBox(height: 2),
                      Text(subtitle, style: sans(12.5, color: Paper.body)),
                    ]),
                  ),
                  if (onTap != null) const Icon(Icons.chevron_right_rounded, color: Paper.faint),
                ]),
              ),
            ),
          ),
        );

    final examPrice = _prices['exam_pass'];
    final sparksPrice = _prices['sparks'];

    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(22, 18, 22, 22),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Center(
            child: Container(
              width: 40, height: 4,
              decoration: BoxDecoration(color: Paper.border, borderRadius: BorderRadius.circular(2)),
            ),
          ),
          const SizedBox(height: 16),
          Row(children: [
            const Icon(Icons.bolt_rounded, color: Paper.accent, size: 26),
            const SizedBox(width: 6),
            Text(status == null ? 'Sparks' : '${status.balance} Sparks',
                key: const ValueKey('sparks-sheet-balance'), style: serif(24)),
            const Spacer(),
            if (status != null) Text(status.planName.toUpperCase(), style: mono(10.5, color: Paper.accent)),
          ]),
          const SizedBox(height: 8),
          Text(
            refused
                ? 'You need ${r.needed ?? 1} Spark${(r.needed ?? 1) == 1 ? '' : 's'} for ${_actionLabel(r.action)}'
                    '${r.balance != null ? ' and have ${r.balance}' : ''}.'
                : 'Versa charges for real work and never for clarifying questions. '
                    'Passing quizzes, finishing lessons and study streaks earn Sparks back.',
            style: sans(13.5, color: Paper.body),
          ),
          if (status?.examPassEndsAt != null) ...[
            const SizedBox(height: 6),
            Text('Exam Pass active until ${_day(status!.examPassEndsAt!)}.', style: sans(12.5, color: Paper.olive)),
          ],
          if (status != null) ...[
            const SizedBox(height: 16),
            SparksUsageMeter(status: status),
          ],
          const SizedBox(height: 18),
          if (next != null)
            option(
              key: const ValueKey('sparks-wait'),
              icon: Icons.schedule_rounded,
              title: 'Wait for the refill',
              subtitle: '+${status?.refillAmount ?? 10} Sparks at ${_clock(next)}, free.',
              onTap: () => Navigator.of(context).pop(),
            ),
          if (status?.tier != 'plus')
            option(
              key: const ValueKey('sparks-plus'),
              icon: Icons.workspace_premium_rounded,
              title: 'Versa Plus',
              subtitle: 'Refills of +50 up to 100, unlimited courses. First month free.',
              highlight: !r.forExam,
              onTap: billing.supported ? () => _run(() => sparks.buy(billing.showPlusPaywall)) : null,
            ),
          if (status?.isPaid != true)
            option(
              key: const ValueKey('sparks-exam-pass'),
              icon: Icons.event_available_rounded,
              title: 'Exam Pass${examPrice != null ? ' · $examPrice' : ''}',
              subtitle: 'Plus until your exam day, then it simply ends. Pay once.',
              highlight: r.forExam,
              onTap: billing.supported
                  ? () => _run(() => sparks.buy(() => billing.buyOffering('exam_pass'), examId: r.examId))
                  : null,
            ),
          option(
            key: const ValueKey('sparks-pack'),
            icon: Icons.bolt_rounded,
            title: '50 Sparks${sparksPrice != null ? ' · $sparksPrice' : ''}',
            subtitle: 'A one-off top-up. Never expires.',
            onTap: billing.supported ? () => _run(() => sparks.buy(() => billing.buyOffering('sparks'))) : null,
          ),
          if (!billing.supported)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(billing.unsupportedReason, style: sans(12.5, color: Paper.muted)),
            ),
          if (_busy) const Padding(padding: EdgeInsets.only(top: 8), child: LinearProgressIndicator(color: Paper.accent)),
          if (_message != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(_message!, key: const ValueKey('sparks-sheet-message'), style: sans(13, color: Paper.accentDark)),
            ),
          if (billing.supported) ...[
            const SizedBox(height: 8),
            Row(children: [
              TextButton(
                key: const ValueKey('sparks-restore'),
                onPressed: _busy ? null : () => _run(() => sparks.buy(billing.restore)),
                child: const Text('Restore purchases'),
              ),
              if (status?.tier == 'plus')
                TextButton(
                  onPressed: _busy ? null : billing.manageSubscription,
                  child: const Text('Manage subscription'),
                ),
            ]),
          ],
        ]),
      ),
    );
  }
}

/// Settings: the plan, the balance and what things cost.
class SparksPlanCard extends StatelessWidget {
  const SparksPlanCard({super.key});

  @override
  Widget build(BuildContext context) {
    final sparks = context.watch<SparksState>();
    final status = sparks.status;
    if (status == null) return const SizedBox.shrink();
    String cost(String action) => '${status.costs[action] ?? '?'}';
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(bottom: 16),
      padding: const EdgeInsets.all(22),
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text('PLAN & SPARKS', style: mono(11)),
        const SizedBox(height: 10),
        Row(children: [
          Expanded(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text(status.planName, key: const ValueKey('plan-name'), style: serif(21)),
              const SizedBox(height: 2),
              Text(
                '${status.balance} Sparks · +${status.refillAmount} every 12 hours up to ${status.cap}'
                '${status.nextRefillAt != null ? ' · next at ${_clock(status.nextRefillAt!)}' : ''}',
                style: sans(12.5, color: Paper.muted),
              ),
              if (status.examPassEndsAt != null)
                Text('Exam Pass until ${_day(status.examPassEndsAt!)}', style: sans(12.5, color: Paper.olive)),
            ]),
          ),
          FilledButton(
            key: const ValueKey('plan-upgrade'),
            style: FilledButton.styleFrom(backgroundColor: Paper.accent),
            onPressed: () => showSparksSheet(context, const PaywallRequest(action: 'upgrade')),
            child: Text(status.isPaid ? 'Sparks' : 'Upgrade'),
          ),
        ]),
        const SizedBox(height: 18),
        SparksUsageMeter(status: status),
        const SizedBox(height: 16),
        Text(
          'Clarifying questions: free · Answer: ${cost('answer')} · Course: ${cost('build_course')} · '
          'Unit quiz: ${cost('unit_quiz')} · Mock test: ${cost('mock_test')}',
          style: sans(12, color: Paper.body),
        ),
      ]),
    );
  }
}
