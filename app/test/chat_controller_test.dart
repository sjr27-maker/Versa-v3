import 'package:flutter_test/flutter_test.dart';
import 'package:versa_app/chat_controller.dart';
import 'package:versa_app/models.dart';

import 'support/fakes.dart';

const _learner = Learner(id: 'l1', label: 'Asha');

Future<(ChatController, FakeTransport, FakeBackend)> _started() async {
  final backend = FakeBackend();
  final transport = FakeTransport();
  final chat = ChatController(
    api: backend.api,
    learner: _learner,
    transportFactory: (_) async => transport,
  );
  await chat.start();
  return (chat, transport, backend);
}

Future<void> _tick() => Future<void>.delayed(Duration.zero);

void main() {
  test('start creates a chat and connects', () async {
    final (chat, _, backend) = await _started();
    expect(chat.status, ChatStatus.ready);
    expect(chat.sessionId, 'session-1');
    expect(backend.sessionsCreated, 1);
    expect(chat.canSend, isTrue);
  });

  test('sending shows the message and a waiting reply, and tells the server', () async {
    final (chat, transport, _) = await _started();
    chat.send('  what is a derivative?  ');

    expect(transport.sent, [
      {'type': 'message', 'text': 'what is a derivative?'}
    ]);
    expect(chat.messages.map((m) => m.role), [Role.user, Role.tutor]);
    expect(chat.messages.last.pending, isTrue);
    expect(chat.status, ChatStatus.thinking);
    expect(chat.canSend, isFalse, reason: 'one turn at a time');
  });

  test('empty text and a busy chat do not send', () async {
    final (chat, transport, _) = await _started();
    chat.send('   ');
    expect(transport.sent, isEmpty);
    chat.send('first');
    chat.send('second, too soon');
    expect(transport.sent.length, 1);
  });

  test('words stream in, then the final text is authoritative', () async {
    final (chat, transport, _) = await _started();
    chat.send('hi');
    transport.emit(const TurnStart(0));
    transport.emit(const Delta('Binary '));
    await _tick();

    final reply = chat.messages.last;
    expect(reply.pending, isFalse);
    expect(reply.streaming, isTrue);
    expect(reply.text, 'Binary ');
    expect(chat.status, ChatStatus.streaming);

    transport.emit(const Delta('search halves the list.'));
    transport.emit(const Done(
        turnIndex: 0,
        kind: 'answer',
        text: 'Binary search halves the list.',
        firstOutputMs: 900,
        totalMs: 2100));
    await _tick();

    expect(reply.text, 'Binary search halves the list.');
    expect(reply.streaming, isFalse);
    expect(chat.status, ChatStatus.ready);
    expect(reply.timing, isNotNull);
    expect(reply.timing!.firstOutputMs, lessThanOrEqualTo(reply.timing!.totalMs));
    expect(reply.timing!.serverFirstOutputMs, 900);
    expect(reply.timing!.serverTotalMs, 2100);
    expect(chat.canSend, isTrue, reason: 'ready for the next question');
  });

  test('done.text replaces partial words when the answer failed part-way', () async {
    final (chat, transport, _) = await _started();
    chat.send('hi');
    transport.emit(const Delta('Half an ans'));
    transport.emit(const Done(
        turnIndex: 0,
        kind: 'answer',
        text: 'The tutor failed to respond this turn.',
        firstOutputMs: 1,
        totalMs: 2));
    await _tick();
    expect(chat.messages.last.text, 'The tutor failed to respond this turn.');
  });

  test('an ambiguous message shows readings; clicking one is a normal turn, once', () async {
    final (chat, transport, _) = await _started();
    chat.send('can you help me with derivatives?');
    transport.emit(const TurnStart(0));
    transport.emit(const OptionsEvent('Which of these did you mean?', [
      ChatOption(id: 'o1', text: 'Calculus derivatives'),
      ChatOption(id: 'o2', text: 'Financial derivatives'),
    ]));
    transport.emit(const Done(
        turnIndex: 0,
        kind: 'options',
        text: 'Which of these did you mean?',
        firstOutputMs: 4000,
        totalMs: 4000));
    await _tick();

    final question = chat.messages.last;
    expect(question.text, 'Which of these did you mean?');
    expect(question.hasOptions, isTrue);
    expect(question.optionsResolved, isFalse);
    expect(chat.status, ChatStatus.ready);

    chat.pickOption(question, question.options[0]);
    expect(transport.sent.last, {'type': 'select_option', 'option_id': 'o1'});
    expect(chat.messages[chat.messages.length - 2].text, 'Calculus derivatives',
        reason: 'the chosen reading shows as what the person said');
    expect(question.chosenOptionId, 'o1');
    expect(chat.status, ChatStatus.thinking);

    // The same buttons can never be used twice.
    transport.emit(const Done(
        turnIndex: 1, kind: 'answer', text: 'The power rule …', firstOutputMs: 1, totalMs: 2));
    await _tick();
    final before = transport.sent.length;
    chat.pickOption(question, question.options[1]);
    expect(transport.sent.length, before);
  });

  test('a server error ends the turn and leaves the chat usable', () async {
    final (chat, transport, _) = await _started();
    chat.send('hi');
    transport.emit(const ErrorEvent('the turn failed: boom'));
    await _tick();

    expect(chat.messages.last.isError, isTrue);
    expect(chat.messages.last.text, contains('boom'));
    expect(chat.status, ChatStatus.ready);
    chat.send('try again');
    expect(transport.sent.length, 2);
  });

  test('a dropped connection never leaves a spinner, and reconnect resumes the SAME chat',
      () async {
    final backend = FakeBackend();
    var transport = FakeTransport();
    final chat = ChatController(
      api: backend.api,
      learner: _learner,
      transportFactory: (_) async => transport,
    );
    await chat.start();
    chat.send('hi');
    transport.emit(const Delta('Part of an'));
    await _tick();

    await transport.drop();
    await _tick();

    expect(chat.status, ChatStatus.disconnected);
    expect(chat.problem, isNotNull);
    final reply = chat.messages.last;
    expect(reply.pending, isFalse);
    expect(reply.streaming, isFalse);
    expect(reply.isError, isTrue);
    expect(reply.text, contains('Part of an'));
    expect(chat.canSend, isFalse);

    transport = FakeTransport();
    await chat.reconnect();
    expect(chat.status, ChatStatus.ready);
    expect(backend.sessionsCreated, 1, reason: 'same session, so memory carries on');
    expect(chat.sessionId, 'session-1');
  });

  test('an unreachable server surfaces as disconnected with a reason', () async {
    final backend = FakeBackend(up: false);
    final chat = ChatController(
      api: backend.api,
      learner: _learner,
      transportFactory: (_) async => FakeTransport(),
    );
    await chat.start();
    expect(chat.status, ChatStatus.disconnected);
    expect(chat.problem, contains('Could not reach the server'));
  });

  test('disposing closes the connection and later events are ignored', () async {
    final (chat, transport, _) = await _started();
    chat.dispose();
    expect(transport.closed, isTrue);
  });
}
