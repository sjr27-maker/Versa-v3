import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:versa_app/main.dart';

void main() {
  testWidgets('the app starts on the logo and the motto', (tester) async {
    await tester.pumpWidget(const MaterialApp(home: LoadingScreen()));
    expect(find.byType(Image), findsOneWidget);
    expect(find.text('Versa'), findsOneWidget);
    expect(find.text('Learn, how you think.'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
