import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:versa_app/screens/directions_lab_screen.dart';

/// The design lab (directions_lab_screen.dart): three presentations of "where
/// this could go", design only. Each must render at phone and desktop width
/// without overflow, and respond the way the design describes.
void main() {
  Future<void> open(WidgetTester tester, Size size) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const MaterialApp(home: DirectionsLabScreen()));
    await tester.pumpAndSettle();
  }

  for (final (name, size) in const [('phone', Size(412, 915)), ('desktop', Size(1280, 900))]) {
    testWidgets('hand of 3 on a $name: three cards, one per family, and other directions deals more',
        (tester) async {
      await open(tester, size);
      Finder cards() => find.descendant(of: find.byKey(const ValueKey('lab-hand')), matching: find.byType(ActionChip));
      expect(cards(), findsNWidgets(3));
      final before = tester.widgetList<ActionChip>(cards()).map((c) => c.key).toSet();
      await tester.tap(find.byKey(const ValueKey('lab-more')));
      await tester.pumpAndSettle();
      final after = tester.widgetList<ActionChip>(cards()).map((c) => c.key).toSet();
      expect(after, hasLength(3));
      expect(after.intersection(before), isEmpty, reason: 'a fresh hand, not the same cards');

      await tester.tap(find.byKey(const ValueKey('lab-fork-switch')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('lab-hand-fork')), findsOneWidget);
      expect(find.textContaining('Continue with'), findsOneWidget);
    });

    testWidgets('compass on a $name: four ways out, one per family', (tester) async {
      await open(tester, size);
      await tester.tap(find.byKey(const ValueKey('lab-tab-compass')));
      await tester.pumpAndSettle();
      for (final f in ['real', 'deeper', 'simpler', 'wider']) {
        expect(find.byKey(ValueKey('lab-compass-$f')), findsOneWidget);
      }
      await tester.ensureVisible(find.byKey(const ValueKey('lab-compass-deeper')));
      await tester.tap(find.byKey(const ValueKey('lab-compass-deeper')));
      await tester.pumpAndSettle();
      expect(find.text('GO DEEPER'), findsOneWidget);
    });

    testWidgets('constellation on a $name: every card type is a star, a tap names it', (tester) async {
      await open(tester, size);
      await tester.tap(find.byKey(const ValueKey('lab-tab-constellation')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('lab-constellation')), findsOneWidget);
      expect(find.byKey(const ValueKey('lab-star-debate')), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('lab-star-debate')));
      await tester.pumpAndSettle();
      expect(find.textContaining('infinitesimals'), findsWidgets);
      expect(find.byKey(const ValueKey('lab-star-go')), findsOneWidget);
    });
  }
}
