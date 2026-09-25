import 'package:flutter/material.dart';

import '../theme.dart';
import 'topic_models.dart';

/// A rounded completion bar that animates to its new value.
class TopicProgressBar extends StatelessWidget {
  const TopicProgressBar({super.key, required this.percent, this.height = 8, this.color = Paper.accent});

  final int percent;
  final double height;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(height),
      child: SizedBox(
        height: height,
        child: TweenAnimationBuilder<double>(
          tween: Tween(end: percent.clamp(0, 100) / 100),
          duration: const Duration(milliseconds: 500),
          curve: Curves.easeOutCubic,
          builder: (context, value, _) => LinearProgressIndicator(
            value: value,
            backgroundColor: Paper.border,
            valueColor: AlwaysStoppedAnimation(percent >= 100 ? Paper.olive : color),
          ),
        ),
      ),
    );
  }
}

/// A bar with its percentage beside it.
class PercentRow extends StatelessWidget {
  const PercentRow({super.key, required this.percent, this.height = 8, this.labelKey});
  final int percent;
  final double height;
  final Key? labelKey;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(child: TopicProgressBar(percent: percent, height: height)),
        const SizedBox(width: 10),
        SizedBox(
          width: 42,
          child: Text('$percent%',
              key: labelKey,
              textAlign: TextAlign.right,
              style: sans(12.5, color: percent >= 100 ? Paper.olive : Paper.body, weight: FontWeight.w600)),
        ),
      ],
    );
  }
}

/// "Shaped by: your thinking style · your past chats" -- makes it visible
/// which things about the learner the server used. Nothing when it used none.
class ShapedByNote extends StatelessWidget {
  const ShapedByNote({super.key, required this.sources});
  final List<String> sources;

  @override
  Widget build(BuildContext context) {
    if (sources.isEmpty) return const SizedBox.shrink();
    return Row(
      key: const ValueKey('shaped-by'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Icon(Icons.person_pin_outlined, size: 15, color: Paper.faint),
        const SizedBox(width: 6),
        Expanded(
          child: Text('Shaped by: ${sources.join(' · ')}',
              style: sans(12, color: Paper.muted, height: 1.4)),
        ),
      ],
    );
  }
}

class LessonStatusIcon extends StatelessWidget {
  const LessonStatusIcon({super.key, required this.status, this.size = 20});
  final LessonStatus status;
  final double size;

  @override
  Widget build(BuildContext context) => switch (status) {
        LessonStatus.done => Icon(Icons.check_circle_rounded, size: size, color: Paper.olive),
        LessonStatus.inProgress => Icon(Icons.timelapse_rounded, size: size, color: Paper.accent),
        LessonStatus.notStarted =>
          Icon(Icons.radio_button_unchecked_rounded, size: size, color: Paper.faint),
      };
}

/// A section label + page title, the pattern every page here uses.
class PageHeading extends StatelessWidget {
  const PageHeading({super.key, required this.eyebrow, required this.title, this.onBack, this.backKey});
  final String eyebrow;
  final String title;
  final VoidCallback? onBack;
  final Key? backKey;

  @override
  Widget build(BuildContext context) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (onBack != null) ...[
          IconButton(
            key: backKey,
            tooltip: 'Back',
            onPressed: onBack,
            icon: const Icon(Icons.arrow_back_rounded, color: Paper.faint, size: 20),
          ),
          const SizedBox(width: 6),
        ],
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(eyebrow, style: mono(11)),
              const SizedBox(height: 6),
              Text(title, style: serif(30)),
            ],
          ),
        ),
      ],
    );
  }
}

/// A calm error line with a retry.
class RetryLine extends StatelessWidget {
  const RetryLine({super.key, required this.message, required this.onRetry});
  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        const Icon(Icons.error_outline_rounded, size: 18, color: Paper.danger),
        const SizedBox(width: 8),
        Expanded(child: Text(message, style: sans(13, color: Paper.danger))),
        TextButton(onPressed: onRetry, child: const Text('Try again')),
      ],
    );
  }
}
