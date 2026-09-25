import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import 'explorer_screen.dart';
import 'lesson_chat_screen.dart';
import 'path_screen.dart';
import 'topic_models.dart';
import 'topic_screen.dart';
import 'topics_home_screen.dart';

/// Learn a topic lives inside the Modes tab with its own navigation stack
/// (home -> explorer / topic -> path / lesson), so switching tabs never loses
/// where the person was.
class TopicsRoot extends StatefulWidget {
  const TopicsRoot({super.key});

  @override
  State<TopicsRoot> createState() => _TopicsRootState();
}

class _TopicsRootState extends State<TopicsRoot> {
  final _navigator = GlobalKey<NavigatorState>();
  ShellState? _shell;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final shell = context.read<ShellState>();
    if (shell != _shell) {
      _shell?.removeListener(_openPendingLesson);
      _shell = shell..addListener(_openPendingLesson);
      _openPendingLesson();
    }
  }

  @override
  void dispose() {
    _shell?.removeListener(_openPendingLesson);
    super.dispose();
  }

  /// A lesson asked for from elsewhere (a History row).
  void _openPendingLesson() {
    if (_shell?.pendingLessonId == null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final nav = _navigator.currentState;
      final id = _shell?.takePendingLesson();
      if (nav == null || id == null || !mounted) return;
      nav.push(topicRoute((_) => LessonChatScreen(lessonId: id)));
    });
  }

  @override
  Widget build(BuildContext context) {
    return Navigator(
      key: _navigator,
      onGenerateRoute: (_) => topicRoute((_) => const TopicsHomeScreen()),
    );
  }
}

/// A calm fade-and-rise page transition used by every topic screen.
PageRoute<T> topicRoute<T>(WidgetBuilder builder) => PageRouteBuilder<T>(
      pageBuilder: (context, _, _) => builder(context),
      transitionDuration: const Duration(milliseconds: 260),
      reverseTransitionDuration: const Duration(milliseconds: 200),
      transitionsBuilder: (context, animation, _, child) {
        final curved = CurvedAnimation(parent: animation, curve: Curves.easeOutCubic);
        return FadeTransition(
          opacity: curved,
          child: SlideTransition(
            position: Tween(begin: const Offset(0, 0.03), end: Offset.zero).animate(curved),
            child: child,
          ),
        );
      },
    );

Future<void> pushExplorer(BuildContext context, {required String label, required Future<Exploration> Function() load}) =>
    Navigator.of(context).push(topicRoute((_) => ExplorerScreen(label: label, load: load)));

Future<void> pushTopic(BuildContext context, String topicId, {Topic? initial}) =>
    Navigator.of(context).push(topicRoute((_) => TopicScreen(topicId: topicId, initial: initial)));

Future<void> pushPath(BuildContext context, String topicId, {Topic? initial}) =>
    Navigator.of(context).push(topicRoute((_) => PathScreen(topicId: topicId, initial: initial)));

Future<void> pushLesson(BuildContext context, String lessonId) =>
    Navigator.of(context).push(topicRoute((_) => LessonChatScreen(lessonId: lessonId)));
