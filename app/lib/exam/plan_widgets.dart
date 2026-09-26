import 'package:flutter/material.dart';

import '../theme.dart';
import '../topic/topic_widgets.dart';
import 'exam_models.dart';

const _weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/// "Today", "Tomorrow", "Yesterday", or "Mon 3 Jan".
String planDayLabel(DateTime day, DateTime today) => switch (day.difference(today).inDays) {
      0 => 'Today',
      1 => 'Tomorrow',
      -1 => 'Yesterday',
      _ => '${_weekdays[day.weekday - 1]} ${day.day} ${_months[day.month - 1]}',
    };

typedef PlanItemAction = void Function(PlanItem item);

/// One plan item: a tick box, what to do, and a Start button for a quiz or
/// a mock that isn't done yet.
class PlanItemTile extends StatelessWidget {
  const PlanItemTile({super.key, required this.item, required this.onToggle, required this.onStart, this.dayLabel});
  final PlanItem item;
  final PlanItemAction onToggle;
  final PlanItemAction onStart;
  final String? dayLabel;

  @override
  Widget build(BuildContext context) {
    final icon = switch (item.kind) {
      PlanKind.revise => Icons.menu_book_outlined,
      PlanKind.quiz || PlanKind.weakest => Icons.quiz_outlined,
      PlanKind.mock => Icons.timer_outlined,
    };
    return Padding(
      key: ValueKey('plan-item-${item.id}'),
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(children: [
        InkWell(
          key: ValueKey('plan-tick-${item.id}'),
          borderRadius: BorderRadius.circular(20),
          onTap: () => onToggle(item),
          child: Padding(
            padding: const EdgeInsets.all(4),
            child: Icon(
              item.done ? Icons.check_circle_rounded : Icons.radio_button_unchecked_rounded,
              size: 22,
              color: item.done ? Paper.olive : Paper.faint,
            ),
          ),
        ),
        const SizedBox(width: 8),
        Icon(icon, size: 17, color: item.done ? Paper.faint : Paper.accent),
        const SizedBox(width: 8),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(
              item.text,
              style: sans(14, color: item.done ? Paper.faint : Paper.ink, weight: FontWeight.w500).copyWith(
                decoration: item.done ? TextDecoration.lineThrough : null,
              ),
            ),
            if (dayLabel != null || item.autoDone)
              Text(
                [?dayLabel, if (item.autoDone) 'done by handing in a quiz'].join(' · '),
                style: sans(11.5, color: Paper.muted),
              ),
          ]),
        ),
        if (item.startable)
          TextButton(
            key: ValueKey('plan-start-${item.id}'),
            onPressed: () => onStart(item),
            style: TextButton.styleFrom(foregroundColor: Paper.accent),
            child: Text(item.kind == PlanKind.mock ? 'Start mock' : 'Start quiz'),
          ),
      ]),
    );
  }
}

/// The study plan on the exam page: today's items and anything overdue, or
/// an offer to make a plan.
class PlanCard extends StatelessWidget {
  const PlanCard({
    super.key,
    required this.plan,
    required this.hasExamDate,
    required this.onMake,
    required this.onReplan,
    required this.onOpenFull,
    required this.onToggle,
    required this.onStart,
    this.busy = false,
  });

  final StudyPlan? plan;
  final bool hasExamDate;
  final VoidCallback onMake;
  final VoidCallback onReplan;
  final VoidCallback onOpenFull;
  final PlanItemAction onToggle;
  final PlanItemAction onStart;
  final bool busy;

  static const _overdueShown = 4;

  @override
  Widget build(BuildContext context) {
    final p = plan;
    return Container(
      key: const ValueKey('plan-card'),
      padding: const EdgeInsets.all(22),
      decoration: BoxDecoration(
        color: Paper.card,
        border: Border.all(color: Paper.border),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            const Icon(Icons.calendar_month_outlined, color: Paper.accent),
            const SizedBox(width: 10),
            Expanded(child: Text('Study plan', style: serif(19))),
            if (p != null)
              TextButton(
                key: const ValueKey('plan-open-full'),
                onPressed: onOpenFull,
                child: const Text('Full plan'),
              ),
          ]),
          const SizedBox(height: 6),
          if (p == null) ..._empty() else ..._plan(context, p),
        ],
      ),
    );
  }

  List<Widget> _empty() => [
        Text(
          'Spread the units over the days until the exam: revise and quiz each one, then practise '
          'your weakest units and sit mock tests. Quizzes and mocks tick themselves off.',
          style: sans(13, color: Paper.muted, height: 1.45),
        ),
        const SizedBox(height: 14),
        FilledButton.icon(
          key: const ValueKey('plan-make'),
          onPressed: busy ? null : onMake,
          icon: const Icon(Icons.auto_awesome_outlined, size: 18),
          label: Text(busy ? 'Planning…' : (hasExamDate ? 'Make my study plan' : 'Pick the exam date and plan')),
          style: FilledButton.styleFrom(backgroundColor: Paper.accent),
        ),
      ];

  List<Widget> _plan(BuildContext context, StudyPlan p) {
    final finished = p.daysLeft <= 0;
    return [
      Text(
        finished
            ? (p.daysLeft == 0 ? 'Exam day -- good luck!' : 'The exam date has passed.')
            : '${p.daysLeft} day${p.daysLeft == 1 ? '' : 's'} to go · ${p.done} of ${p.total} done',
        key: const ValueKey('plan-summary'),
        style: sans(13, color: Paper.body, weight: FontWeight.w600),
      ),
      const SizedBox(height: 8),
      PercentRow(percent: p.percent, height: 7, labelKey: const ValueKey('plan-percent')),
      const SizedBox(height: 14),
      Text('TODAY', style: mono(10)),
      const SizedBox(height: 4),
      if (p.todayItems.isEmpty)
        Text(finished ? 'Nothing left on the plan.' : 'Nothing planned today.',
            key: const ValueKey('plan-today-empty'), style: sans(13, color: Paper.muted))
      else
        for (final item in p.todayItems) PlanItemTile(item: item, onToggle: onToggle, onStart: onStart),
      if (p.overdue.isNotEmpty) ...[
        const SizedBox(height: 12),
        Text('CATCH UP', style: mono(10, color: Paper.warn)),
        const SizedBox(height: 4),
        for (final item in p.overdue.take(_overdueShown))
          PlanItemTile(
            item: item,
            onToggle: onToggle,
            onStart: onStart,
            dayLabel: planDayLabel(item.day, p.today),
          ),
        if (p.overdue.length > _overdueShown)
          Padding(
            padding: const EdgeInsets.only(left: 34, top: 2),
            child: Text('+${p.overdue.length - _overdueShown} more in the full plan',
                style: sans(12, color: Paper.muted)),
          ),
      ],
      const SizedBox(height: 10),
      Align(
        alignment: Alignment.centerLeft,
        child: TextButton.icon(
          key: const ValueKey('plan-replan'),
          onPressed: busy ? null : onReplan,
          icon: const Icon(Icons.refresh_rounded, size: 16),
          label: const Text('Re-plan from today'),
          style: TextButton.styleFrom(foregroundColor: Paper.muted),
        ),
      ),
    ];
  }
}

/// Every day of the plan, today highlighted, past days dimmed.
class PlanScreen extends StatefulWidget {
  const PlanScreen({
    super.key,
    required this.examTitle,
    required this.initial,
    required this.onToggle,
    required this.onStart,
  });

  final String examTitle;
  final StudyPlan initial;

  /// Ticks an item and returns the updated plan.
  final Future<StudyPlan?> Function(PlanItem item) onToggle;
  final Future<void> Function(PlanItem item) onStart;

  @override
  State<PlanScreen> createState() => _PlanScreenState();
}

class _PlanScreenState extends State<PlanScreen> {
  late StudyPlan _plan = widget.initial;

  Future<void> _toggle(PlanItem item) async {
    final updated = await widget.onToggle(item);
    if (updated != null && mounted) setState(() => _plan = updated);
  }

  Future<void> _start(PlanItem item) async {
    await widget.onStart(item);
    // the exam page reloads the plan; close so it shows the fresh one
    if (mounted) Navigator.of(context).maybePop();
  }

  @override
  Widget build(BuildContext context) {
    final p = _plan;
    return Container(
      color: Paper.surface,
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(32, 32, 32, 60),
        child: Align(
          alignment: Alignment.topLeft,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 820),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                PageHeading(
                  eyebrow: widget.examTitle.toUpperCase(),
                  title: 'Study plan',
                  onBack: () => Navigator.of(context).maybePop(),
                  backKey: const ValueKey('plan-back'),
                ),
                const SizedBox(height: 14),
                PercentRow(percent: p.percent, height: 7),
                const SizedBox(height: 6),
                Text('${p.done} of ${p.total} done · exam on ${planDayLabel(p.endDate, p.today)}',
                    style: sans(12.5, color: Paper.muted)),
                const SizedBox(height: 20),
                for (final d in p.days) _day(d, p.today),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _day(PlanDay d, DateTime today) {
    final isToday = d.day == today;
    final past = d.day.isBefore(today);
    return Opacity(
      opacity: past && d.items.every((i) => i.done) ? 0.55 : 1,
      child: Container(
        key: ValueKey('plan-day-${d.day.toIso8601String().substring(0, 10)}'),
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.fromLTRB(16, 12, 12, 10),
        decoration: BoxDecoration(
          color: isToday ? Paper.accentSoft : Paper.card,
          border: Border.all(color: isToday ? Paper.accent : Paper.border, width: isToday ? 1.5 : 1),
          borderRadius: BorderRadius.circular(12),
        ),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(planDayLabel(d.day, today),
              style: sans(13, color: isToday ? Paper.accentDark : Paper.body, weight: FontWeight.w700)),
          const SizedBox(height: 4),
          for (final item in d.items) PlanItemTile(item: item, onToggle: _toggle, onStart: _start),
        ]),
      ),
    );
  }
}
