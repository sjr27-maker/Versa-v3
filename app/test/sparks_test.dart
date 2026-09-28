import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:provider/provider.dart';
import 'package:versa_app/billing/billing.dart';
import 'package:versa_app/billing/sparks.dart';
import 'package:versa_app/billing/sparks_widgets.dart';
import 'package:versa_app/chat_controller.dart';
import 'package:versa_app/models.dart';

import 'support/fakes.dart';

const _learner = Learner(id: 'l1', label: 'Asha');

Map<String, dynamic> _sparksJson(int balance, {String tier = 'free'}) => {
      'learner_id': 'l1',
      'enabled': true,
      'tier': tier,
      'balance': balance,
      'cap': 20,
      'refill_amount': 10,
      'refill_hours': 12,
      'next_refill_at': '2030-01-01T12:00:00+00:00',
      'costs': {'answer': 1, 'build_course': 5, 'unit_quiz': 3, 'mock_test': 8},
      'rewards': {'unit_quiz_passed': 2},
      'recent': [],
    };

/// A server that only knows the Sparks endpoints.
class _SparksServer {
  int balance = 20;
  String tier = 'free';
  final List<String> calls = [];
  List<Map<String, dynamic>> grantOnSync = [];

  late final http.Client client = MockClient((request) async {
    calls.add('${request.method} ${request.url.path}');
    final path = request.url.path;
    if (path == '/api/learners/l1/sparks') {
      return http.Response(jsonEncode(_sparksJson(balance, tier: tier)), 200);
    }
    if (path == '/api/learners/l1/billing') {
      return http.Response(jsonEncode({'learner_id': 'l1', 'enabled': true, 'tier': tier}), 200);
    }
    if (path == '/api/learners/l1/billing/sync') {
      for (final g in grantOnSync) {
        if (g['kind'] == 'sparks') balance += g['amount'] as int;
      }
      return http.Response(jsonEncode({
        'granted': grantOnSync,
        'billing': {'learner_id': 'l1', 'enabled': true, 'tier': tier},
        'sparks': _sparksJson(balance, tier: tier),
      }), 200);
    }
    return http.Response('not found', 404);
  });

  SparksState state({Billing billing = const NoBilling()}) =>
      SparksState(api: SparksApi('http://x', client: client), billing: billing);
}

class _FakeBilling implements Billing {
  PurchaseOutcome next = PurchaseOutcome.purchased;
  final List<String> bought = [];

  @override
  bool get supported => true;
  @override
  String get unsupportedReason => '';
  @override
  Future<void> identify(String learnerId) async {}
  @override
  Future<void> reset() async {}
  @override
  Future<PurchaseOutcome> showPlusPaywall() async {
    bought.add('plus');
    return next;
  }

  @override
  Future<PurchaseOutcome> buyOffering(String offeringId) async {
    bought.add(offeringId);
    return next;
  }

  @override
  Future<PurchaseOutcome> restore() async => next;
  @override
  Future<void> manageSubscription() async {}
  @override
  Future<String?> priceOf(String offeringId) async => offeringId == 'sparks' ? r'$0.99' : r'$2.99';
}

void main() {
  test('the chat frames parse', () {
    final paywall = parseServerEvent({'type': 'paywall', 'reason': 'sparks', 'needed': 1, 'balance': 0});
    expect(paywall, isA<PaywallEvent>());
    final sparks = parseServerEvent({'type': 'sparks', 'balance': 7, 'spent': 1}) as SparksEvent;
    expect((sparks.balance, sparks.spent), (7, 1));
    final reward = parseServerEvent({'type': 'sparks_reward', 'reason': 'lesson_completed', 'amount': 3})
        as SparksRewardEvent;
    expect((reward.reason, reward.amount), ('lesson_completed', 3));
  });

  test('a paywall frame ends the turn with a note and is handed on', () async {
    final backend = FakeBackend();
    final transport = FakeTransport();
    final chat = ChatController(api: backend.api, learner: _learner, transportFactory: (_) async => transport);
    await chat.start();
    final seen = <ServerEvent>[];
    final sub = chat.sparkEvents.listen(seen.add);

    chat.send('what is a derivative?');
    transport.emit(const PaywallEvent({'reason': 'sparks', 'action': 'answer', 'needed': 1, 'balance': 0}));
    await Future<void>.delayed(Duration.zero);

    expect(chat.status, ChatStatus.ready, reason: 'no spinner is left behind');
    expect(chat.messages.last.text, contains('out of Sparks'));
    expect(chat.messages.last.isError, isFalse);
    expect(seen.single, isA<PaywallEvent>());

    // an answer's charge is not chat content
    chat.send('again');
    transport.emit(const TurnStart(1));
    transport.emit(const SparksEvent(balance: 4, spent: 1));
    transport.emit(const Done(turnIndex: 1, kind: 'answer', text: 'ok', firstOutputMs: 1, totalMs: 2));
    await Future<void>.delayed(Duration.zero);
    expect(seen.last, isA<SparksEvent>());
    expect(chat.messages.last.text, 'ok');
    await sub.cancel();
    chat.dispose();
  });

  test('SparksState follows the server, charges and rewards', () async {
    final server = _SparksServer();
    final state = server.state();
    final notes = <String>[];
    state.notes.listen(notes.add);
    await state.signedIn('l1');
    expect(state.status!.balance, 20);
    expect(state.status!.planName, 'Free');

    state.charged(19);
    expect(state.status!.balance, 19);

    server.balance = 21;
    state.rewarded('unit_quiz_passed', 2);
    await Future<void>.delayed(Duration.zero);
    expect(notes.single, '+2 Sparks: quiz passed');

    // a successful POST elsewhere re-reads the balance and announces a gain
    server.balance = 25;
    PaywallHub.changed();
    await Future<void>.delayed(const Duration(milliseconds: 10));
    expect(state.status!.balance, 25);
    expect(notes.last, '+4 Sparks: learning paid back');
    state.dispose();
  });

  test('a purchase asks the server to apply it right away', () async {
    final server = _SparksServer()..grantOnSync = [{'kind': 'sparks', 'amount': 50}];
    final billing = _FakeBilling();
    final state = server.state(billing: billing);
    await state.signedIn('l1');

    final line = await state.buy(() => billing.buyOffering('sparks'));
    expect(line, '+50 Sparks added.');
    expect(state.status!.balance, 70);
    expect(server.calls, contains('POST /api/learners/l1/billing/sync'));

    billing.next = PurchaseOutcome.cancelled;
    expect(await state.buy(billing.showPlusPaywall), 'No purchase made.');
    state.dispose();
  });

  test('a 402 becomes an OutOfSparksException and a paywall request', () async {
    final requests = <PaywallRequest>[];
    final sub = PaywallHub.requests.listen(requests.add);
    final response = http.Response(
      jsonEncode({'detail': {'reason': 'sparks', 'action': 'mock_test', 'needed': 8, 'balance': 3}}),
      402,
    );
    expect(() => PaywallHub.check(response), throwsA(isA<OutOfSparksException>()));
    await Future<void>.delayed(Duration.zero);
    expect(requests.single.action, 'mock_test');
    expect(requests.single.forExam, isTrue);
    PaywallHub.check(http.Response('{}', 200)); // anything else passes through
    await sub.cancel();
  });

  testWidgets('a paywall request opens the Sparks sheet', (tester) async {
    final server = _SparksServer()..balance = 0;
    final state = server.state(billing: _FakeBilling());
    await tester.runAsync(() => state.signedIn('l1'));

    await tester.pumpWidget(ChangeNotifierProvider<SparksState>.value(
      value: state,
      child: const MaterialApp(
        home: SparksPaywallListener(child: Scaffold(body: Center(child: SparksChip()))),
      ),
    ));
    await tester.pump();
    expect(find.byKey(const ValueKey('sparks-chip')), findsOneWidget);

    PaywallHub.request(const PaywallRequest(action: 'answer', needed: 1, balance: 0));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('sparks-sheet-balance')), findsOneWidget);
    expect(find.text('You need 1 Spark for an answer and have 0.'), findsOneWidget);
    expect(find.byKey(const ValueKey('sparks-plus')), findsOneWidget);
    expect(find.byKey(const ValueKey('sparks-exam-pass')), findsOneWidget);
    expect(find.byKey(const ValueKey('sparks-pack')), findsOneWidget);
    expect(find.byKey(const ValueKey('sparks-wait')), findsOneWidget);
  });

  testWidgets('the usage meter shows what is left, the refill and the streak', (tester) async {
    final now = DateTime(2030, 1, 1, 9, 0);
    final status = SparksStatus.fromJson({
      ..._sparksJson(69, tier: 'plus'),
      'cap': 100,
      'refill_amount': 50,
      'next_refill_at': now.add(const Duration(hours: 3, minutes: 12)).toUtc().toIso8601String(),
      'spent_today': 1,
      'streak_days': 3,
      'studied_today': true,
      'streak_goal': 5,
      'rewards': {'study_streak': 5},
    });
    expect(status.fill, closeTo(0.69, 1e-9));
    expect(status.streakProgress, 3);

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: SparksUsageMeter(status: status, now: now)),
    ));
    expect(find.text('69 of 100 left'), findsOneWidget);
    expect(find.text('Refills +50 in 3 hr 12 min · 1 used today'), findsOneWidget);
    expect(find.text('3-day study streak'), findsOneWidget);
    expect(find.text('2 more days for +5 Sparks.'), findsOneWidget);

    // a spend today on a streak that was only alive from yesterday counts the day
    final before = SparksStatus.fromJson({..._sparksJson(10), 'streak_days': 2, 'studied_today': false});
    final after = before.withBalance(9);
    expect((after.streakDays, after.studiedToday, after.spentToday), (3, true, 1));
  });
}
