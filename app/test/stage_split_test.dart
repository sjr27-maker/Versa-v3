import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:versa_app/widgets/stage_split.dart';

Widget _host(StageSplit split, Size size) => MaterialApp(
      home: MediaQuery(
        data: MediaQueryData(size: size),
        child: Scaffold(body: SizedBox(width: size.width, height: size.height, child: split)),
      ),
    );

void main() {
  testWidgets('on a phone, dragging the handle down makes the stage taller', (tester) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    double? kept;
    await tester.pumpWidget(_host(
      StageSplit(
        wide: false,
        stage: (h) => SizedBox(key: const ValueKey('stage'), height: h),
        chat: const SizedBox.expand(key: ValueKey('chat')),
        onHeight: (h) => kept = h,
      ),
      const Size(400, 800),
    ));
    expect(tester.getSize(find.byKey(const ValueKey('stage'))).height, 280);

    await tester.drag(find.byKey(const ValueKey('stage-resize')), const Offset(0, 80));
    await tester.pumpAndSettle();
    expect(tester.getSize(find.byKey(const ValueKey('stage'))).height, closeTo(360, 25));
    expect(kept, closeTo(360, 25));

    // never so tall the chat loses its composer, never less than a strip
    await tester.drag(find.byKey(const ValueKey('stage-resize')), const Offset(0, 900));
    await tester.pumpAndSettle();
    expect(tester.getSize(find.byKey(const ValueKey('stage'))).height, 800 - 260);
    await tester.drag(find.byKey(const ValueKey('stage-resize')), const Offset(0, -900));
    await tester.pumpAndSettle();
    expect(tester.getSize(find.byKey(const ValueKey('stage'))).height, 120);

    // a tap stretches it all the way (room for the options), a second brings it back
    await tester.tap(find.byKey(const ValueKey('stage-resize')));
    await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 50)); // a tap waits out a double-tap
    await tester.pumpAndSettle();
    expect(tester.getSize(find.byKey(const ValueKey('stage'))).height, 800 - 260);
    await tester.tap(find.byKey(const ValueKey('stage-resize')));
    await tester.pump(kDoubleTapTimeout + const Duration(milliseconds: 50)); // a tap waits out a double-tap
    await tester.pumpAndSettle();
    expect(tester.getSize(find.byKey(const ValueKey('stage'))).height, StageSplit.defaultHeight);
    expect(tester.takeException(), isNull);
  });

  testWidgets('on a wide screen, dragging the handle right widens the stage', (tester) async {
    tester.view.physicalSize = const Size(1220, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    double? kept;
    await tester.pumpWidget(_host(
      StageSplit(
        wide: true,
        stage: (_) => const SizedBox.expand(key: ValueKey('stage')),
        chat: const SizedBox.expand(key: ValueKey('chat')),
        onFraction: (f) => kept = f,
      ),
      const Size(1220, 800),
    ));
    expect(tester.getSize(find.byKey(const ValueKey('stage'))).width, 600);
    await tester.drag(find.byKey(const ValueKey('stage-resize')), const Offset(120, 0));
    await tester.pumpAndSettle();
    expect(tester.getSize(find.byKey(const ValueKey('stage'))).width, closeTo(720, 25));
    expect(kept, closeTo(0.6, 0.03));
  });
}
