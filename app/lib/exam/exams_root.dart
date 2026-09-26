import 'package:flutter/material.dart';

import '../topic/topics_root.dart' show topicRoute;
import 'exam_models.dart';
import 'exam_screen.dart';
import 'exams_home_screen.dart';
import 'quiz_screen.dart';

/// Exam preparation lives inside the Modes tab with its own navigation stack
/// (home -> exam -> quiz), so switching tabs never loses where the person was.
class ExamsRoot extends StatelessWidget {
  const ExamsRoot({super.key});

  @override
  Widget build(BuildContext context) => Navigator(
        onGenerateRoute: (_) => topicRoute((_) => const ExamsHomeScreen()),
      );
}

Future<void> pushExam(BuildContext context, String examId, {Exam? initial}) =>
    Navigator.of(context).push(topicRoute((_) => ExamScreen(examId: examId, initial: initial)));

Future<void> pushQuiz(BuildContext context, {required String label, required Future<Quiz> Function() load}) =>
    Navigator.of(context).push(topicRoute((_) => QuizScreen(label: label, load: load)));
