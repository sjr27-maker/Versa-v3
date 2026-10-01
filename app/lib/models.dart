// Data shapes shared by the API client, the chat controller and the screens.

import 'dart:typed_data';

class Learner {
  const Learner({required this.id, required this.label});
  final String id;
  final String label;
}

/// What a learner told Versa at sign-up (src/versa/profiles.py): their
/// answers as given, and what the server read from them.
class LearnerProfile {
  const LearnerProfile({required this.answers, this.consent = const {}, this.extracted});

  /// name, age, occupation (school / university / working / other),
  /// country, region, institution, curriculum, level, course, subjects, goals.
  final Map<String, dynamic> answers;
  final Map<String, dynamic> consent;

  /// stage, education_system, level, location, subjects, working_towards,
  /// starting_point, age_fits_stage... Null if the reading failed.
  final Map<String, dynamic>? extracted;

  String? get name => answers['name'] as String?;
  int? get age => (answers['age'] as num?)?.toInt();
  String? get occupation => answers['occupation'] as String?;

  /// One line for Settings, e.g. "Class 11 (CBSE (India)) · Kerala, India".
  String get summary {
    final x = extracted;
    final level = (x?['level'] as String?) ?? answers['level'] as String?;
    final system = (x?['education_system'] as String?) ?? answers['curriculum'] as String?;
    final place = (x?['location'] as String?) ??
        [answers['region'], answers['country']].whereType<String>().where((s) => s.isNotEmpty).join(', ');
    final what = [if (level != null && level.isNotEmpty) level, if (system != null && system.isNotEmpty) '($system)']
        .join(' ');
    return [if (what.isNotEmpty) what, if (place.isNotEmpty) place].join(' · ');
  }

  factory LearnerProfile.fromJson(Map<String, dynamic> j) => LearnerProfile(
        answers: Map<String, dynamic>.from(j['answers'] as Map),
        consent: Map<String, dynamic>.from((j['consent'] as Map?) ?? const {}),
        extracted: j['extracted'] is Map ? Map<String, dynamic>.from(j['extracted'] as Map) : null,
      );
}

class ChatOption {
  const ChatOption({required this.id, required this.text});
  final String id;
  final String text;
}

/// A chat's style controls (server: session_knobs.py): answer length, depth
/// and breadth as 0-100 slider levels. A new chat starts with length at 40
/// and depth and breadth at 50.
class SessionKnobs {
  const SessionKnobs({this.answerLength = 40, this.depth = 50, this.breadth = 50});

  final int answerLength;
  final int depth;

  /// Focused (0) to wide (100): how far an answer reaches beyond the question.
  final int breadth;

  factory SessionKnobs.fromJson(Map<String, dynamic> j) => SessionKnobs(
        answerLength: (j['answer_length'] as num?)?.toInt() ?? 40,
        depth: (j['depth'] as num?)?.toInt() ?? 50,
        breadth: (j['breadth'] as num?)?.toInt() ?? 50,
      );

  SessionKnobs copyWith({int? answerLength, int? depth, int? breadth}) => SessionKnobs(
        answerLength: answerLength ?? this.answerLength,
        depth: depth ?? this.depth,
        breadth: breadth ?? this.breadth,
      );

  @override
  bool operator ==(Object other) =>
      other is SessionKnobs &&
      other.answerLength == answerLength &&
      other.depth == depth &&
      other.breadth == breadth;

  @override
  int get hashCode => Object.hash(answerLength, depth, breadth);
}

/// A claim update the sandbox-chat background flow made (or is offering to
/// undo) -- the inline "Noted: ..." note under a tutor turn (server:
/// reviews.apply_stated_preference_to_claims / session_history.py's replay
/// of it). `action` is one of created/approve/edit/archive/supported.
class ClaimUpdate {
  const ClaimUpdate({
    required this.action,
    required this.claimId,
    required this.statement,
    this.reviewId,
  });

  final String action;
  final String claimId;
  final String? statement;
  final String? reviewId;

  factory ClaimUpdate.fromJson(Map<String, dynamic> json) => ClaimUpdate(
        action: json['action'] as String,
        claimId: json['claim_id'] as String,
        statement: json['statement'] as String?,
        reviewId: json['review_id'] as String?,
      );

  String get noteText => switch (action) {
        'created' => 'Noted: "$statement" (added to your thinking style)',
        'approve' => 'Noted: confirmed "$statement"',
        'edit' => 'Noted: updated to "$statement"',
        'archive' => 'Noted: retired that preference',
        'supported' => 'Noted: this matches what you\'ve said before',
        _ => 'Noted.',
      };
}

/// Versa's guess at which direction the learner would take, revealed right
/// after they took one (server: pick_prediction.py, the `guess` frame). The
/// guess was made before the directions were shown; this is how the learner
/// sees Versa learning them. `because` is what the guess rested on, in plain
/// words, read off the stored numbers.
class DirectionGuess {
  const DirectionGuess({
    required this.hit,
    required this.predicted,
    required this.picked,
    required this.hits,
    required this.guesses,
    required this.picksSeen,
    this.because = const [],
  });

  final bool hit;
  final String predicted;
  final String picked;

  /// Versa's record over the learner's last few picks.
  final int hits;
  final int guesses;

  /// How many of their picks the guess had to go on.
  final int picksSeen;
  final List<String> because;

  factory DirectionGuess.fromJson(Map<String, dynamic> json) => DirectionGuess(
        hit: json['hit'] == true,
        predicted: json['predicted'] as String? ?? '',
        picked: json['picked'] as String? ?? '',
        hits: (json['hits'] as num?)?.toInt() ?? 0,
        guesses: (json['guesses'] as num?)?.toInt() ?? 0,
        picksSeen: (json['picks_seen'] as num?)?.toInt() ?? 0,
        because: [for (final line in (json['because'] as List? ?? const [])) line.toString()],
      );

  /// Too little to go on yet for a record to mean much.
  bool get stillLearning => guesses < 3;

  String get headline => hit
      ? "Versa guessed you'd pick this"
      : 'Versa expected “$predicted” — you surprised it';

  String get record => stillLearning ? 'still learning you' : '$hits of your last $guesses';
}

/// The answer was shaped to how this learner usually moves through an idea,
/// learned from their own direction picks (server: the `adapted` frame,
/// pick_prediction.approach_profile). `path` is the order it opens with.
class AnswerShaping {
  const AnswerShaping({required this.path, this.because = const []});

  final List<String> path;
  final List<String> because;

  factory AnswerShaping.fromJson(Map<String, dynamic> json) => AnswerShaping(
        path: [for (final p in (json['path'] as List? ?? const [])) p.toString()],
        because: [for (final line in (json['because'] as List? ?? const [])) line.toString()],
      );

  String get headline => 'Shaped to how you explore: ${path.join(' → ')}';
}

/// One check a pattern has to pass before Versa calls it your thinking style
/// (server: style_patterns.py) -- e.g. "topics: 3 different topics (needs >= 3)".
class StyleGate {
  const StyleGate({required this.name, required this.ok, required this.have, required this.need});

  final String name;
  final bool ok;
  final String have;
  final String need;

  /// "away_from_default" -> "away from default"
  String get label => name.replaceAll('_', ' ');
}

/// A way this learner moves through ideas, read off their own choices
/// (server: GET /api/learners/{id}/style-patterns). `status` is confirmed
/// (every gate passed), emerging (clear, not yet through every gate) or
/// fading (clear before, not lately).
/// Experimenting on a miss (server: style_patterns.miss_follow_through):
/// how often the cards missed and the learner asked their own question, how
/// many of those read as one of the ways out, and whether that way was taken
/// when a later hand offered it -- and taken again in a later chat.
class MissFollowThrough {
  const MissFollowThrough({
    this.misses = 0,
    this.read = 0,
    this.inHand = 0,
    this.offeredLater = 0,
    this.taken = 0,
    this.held = 0,
  });

  final int misses, read, inHand, offeredLater, taken, held;

  factory MissFollowThrough.fromJson(Map<String, dynamic> j) => MissFollowThrough(
        misses: (j['misses'] as num?)?.toInt() ?? 0,
        read: (j['read'] as num?)?.toInt() ?? 0,
        inHand: (j['in_hand'] as num?)?.toInt() ?? 0,
        offeredLater: (j['offered_later'] as num?)?.toInt() ?? 0,
        taken: (j['taken'] as num?)?.toInt() ?? 0,
        held: (j['held'] as num?)?.toInt() ?? 0,
      );
}

/// A move this learner asked for that no card type covers (server:
/// StyleReader.learner_moves) -- a way of thinking the cards don't offer yet.
class NewMove {
  const NewMove({required this.label, this.times = 0, this.chats = 0});
  final String label;
  final int times, chats;

  factory NewMove.fromJson(Map<String, dynamic> j) => NewMove(
        label: j['label'] as String? ?? '',
        times: (j['times'] as num?)?.toInt() ?? 0,
        chats: (j['chats'] as num?)?.toInt() ?? 0,
      );
}

/// Everything `GET .../style-patterns` returns.
class StyleReport {
  const StyleReport({
    this.patterns = const [],
    this.misses = const MissFollowThrough(),
    this.newMoves = const [],
  });
  final List<StylePattern> patterns;
  final MissFollowThrough misses;
  final List<NewMove> newMoves;
}

/// Where a card type sits in the space (server: directions.COORDS): its
/// family and its place on four axes -- concrete, depth, breadth, practical.
class SpacePoint {
  const SpacePoint({required this.family, required this.coords, required this.label});
  final String family;
  final List<double> coords;
  final String label;

  double get concrete => coords.isNotEmpty ? coords[0] : 0;
  double get depth => coords.length > 1 ? coords[1] : 0;
  double get breadth => coords.length > 2 ? coords[2] : 0;

  factory SpacePoint.fromJson(Map<String, dynamic> json) => SpacePoint(
        family: json['family'] as String? ?? '',
        coords: [for (final c in (json['coords'] as List? ?? const [])) (c as num).toDouble()],
        label: json['label'] as String? ?? '',
      );
}

/// One thing a pattern rests on: a pick (the card taken, the hand, whether
/// it supports the pattern, which chat) or, for a range, a session's level.
class StyleEvidence {
  const StyleEvidence({this.card, this.offered = const [], this.supports = false, this.session, this.value});
  final String? card;
  final List<String> offered;
  final bool supports;
  final String? session;
  final int? value;

  factory StyleEvidence.fromJson(Map<String, dynamic> json) => StyleEvidence(
        card: json['card'] as String?,
        offered: [for (final o in (json['offered'] as List? ?? const [])) o.toString()],
        supports: json['supports'] == true,
        session: json['session'] as String?,
        value: (json['value'] as num?)?.toInt(),
      );
}

class StylePattern {
  const StylePattern({
    required this.kind,
    required this.key,
    required this.statement,
    required this.status,
    this.gates = const [],
    this.evidence = const [],
    this.space = const {},
    this.hits = 0,
    this.trials = 0,
    this.rate,
    this.facetOf,
  });

  final String kind;
  final String key;
  final String statement;
  final String status;
  final List<StyleGate> gates;

  /// What it rests on, oldest first -- drawn as a sky when it is opened.
  final List<StyleEvidence> evidence;

  /// Where every card type sits (shared by all patterns of one response).
  final Map<String, SpacePoint> space;

  /// Later picks guessed from earlier ones, and how many were right.
  final int hits;
  final int trials;

  /// A lean's signed value on its axis (+ concrete / deeper / wider /
  /// practical); for other kinds, how often it was taken when offered.
  final double? rate;

  /// Set when this is a facet of a stronger fact pointing the same way:
  /// that fact's [id]. Null for a fact of its own.
  final String? facetOf;

  /// "<kind>:<key>" -- what a facet points back to.
  String get id => '$kind:$key';

  factory StylePattern.fromJson(Map<String, dynamic> json, {Map<String, SpacePoint> space = const {}}) =>
      StylePattern(
        space: space,
        hits: (json['hits'] as num?)?.toInt() ?? 0,
        trials: (json['trials'] as num?)?.toInt() ?? 0,
        rate: (json['rate'] as num?)?.toDouble(),
        facetOf: json['facet_of'] as String?,
        evidence: [
          for (final e in (json['evidence'] as List? ?? const [])) StyleEvidence.fromJson(e as Map<String, dynamic>),
        ],
        kind: json['kind'] as String? ?? '',
        key: json['key'] as String? ?? '',
        statement: json['statement'] as String? ?? '',
        status: json['status'] as String? ?? 'emerging',
        gates: [
          for (final e in ((json['gates'] as Map?) ?? const {}).entries)
            StyleGate(
              name: e.key as String,
              ok: (e.value as Map)['ok'] == true,
              have: (e.value as Map)['have']?.toString() ?? '',
              need: (e.value as Map)['need']?.toString() ?? '',
            ),
        ],
      );

  int get gatesPassed => gates.where((g) => g.ok).length;
}

enum Role { user, tutor }

/// What the person experienced for one answer, measured on the device (so it
/// includes the network), plus the server's own numbers for comparison.
class Timing {
  const Timing({
    required this.firstOutputMs,
    required this.totalMs,
    this.serverFirstOutputMs,
    this.serverTotalMs,
  });
  final int firstOutputMs;
  final int totalMs;
  final int? serverFirstOutputMs;
  final int? serverTotalMs;
}

class ChatMessage {
  ChatMessage({
    required this.id,
    required this.role,
    this.text = '',
    this.pending = false,
    this.streaming = false,
    this.isError = false,
    this.options = const [],
    this.chosenOptionId,
    this.optionsOpen = true,
    this.timing,
  });

  final int id;
  final Role role;
  String text;

  /// Waiting for the first word (shows the typing indicator).
  bool pending;

  /// Words are arriving.
  bool streaming;
  bool isError;

  /// Clickable readings offered instead of an answer ("which did you mean?").
  List<ChatOption> options;
  String? chosenOptionId;

  /// Set on a tutor turn the sandbox-chat claim-update flow touched -- see
  /// [ClaimUpdate].
  ClaimUpdate? claimUpdate;

  /// Set on the reply to a taken direction: whether Versa saw it coming.
  DirectionGuess? guess;

  /// Set when the answer was shaped to the learner's usual way into an idea.
  AnswerShaping? adapted;

  /// A short reply to chatter ("ok", "thanks"): no turn behind it.
  bool chatter = false;

  /// The picture sent with this (learner's) message, as picked on this
  /// device. A chat loaded from history has only the words and what the
  /// picture showed (picture.dart splitPicture).
  Uint8List? picture;

  /// Being rewritten at new slider levels (the text streams in place).
  bool rewriting = false;

  /// Still awaiting a decision: at least one reading is clickable. False the
  /// moment ANY of a click, typing past instead (see ChatController.send),
  /// or (for a chat loaded from history) the server already recording every
  /// reading as resolved settles it — a stale button must never look live.
  bool optionsOpen;
  Timing? timing;

  /// The server's turn index, once the turn is done (matches a directions
  /// set to the answer it follows).
  int? turnIndex;

  /// "Where this could go" (server: directions.py): the standard set of
  /// directions offered under this answer, in the order they are SHOWN.
  /// Cleared once the learner moves on (picks one or asks their own).
  List<DirectionCard> directions = const [];

  /// The hand those directions are (server: a set dealt from a pool) --
  /// what "other directions" asks to replace.
  String? directionsSetId;

  /// "Other directions" was asked for and the next hand hasn't come yet.
  bool moreDirectionsPending = false;

  /// The server has no other directions left for this answer.
  bool directionsExhausted = false;

  /// This reply continues the one before it: the learner took the fork
  /// link with this text (shown as a small "->" heading, no user bubble).
  String? continuationOf;

  /// Memory already knew what the student meant (server "recalled" frame,
  /// IDEAS.md "oh wait..."): the turn answered from it, taking back any
  /// options it had already shown.
  bool recalled = false;

  bool get hasOptions => options.isNotEmpty;
  bool get optionsResolved => chosenOptionId != null;
}

/// One card of a "where this could go" strip.
class DirectionCard {
  const DirectionCard({required this.id, required this.text, this.family});
  final String id;
  final String text;

  /// real / deeper / simpler / wider (server: directions.FAMILY_OF) -- where
  /// the card sits on the compass. Null from an older server.
  final String? family;
}

// --------------------------------------------------------- server events

/// One frame from the server's chat WebSocket (see src/versa/server.py).
sealed class ServerEvent {
  const ServerEvent();
}

class TurnStart extends ServerEvent {
  const TurnStart(this.turnIndex);
  final int turnIndex;
}

class Delta extends ServerEvent {
  const Delta(this.text);
  final String text;
}

class OptionsEvent extends ServerEvent {
  const OptionsEvent(this.message, this.options);
  final String message;
  final List<ChatOption> options;
}

class Done extends ServerEvent {
  const Done({
    required this.turnIndex,
    required this.kind,
    required this.text,
    required this.firstOutputMs,
    required this.totalMs,
  });
  final int turnIndex;
  final String kind; // "answer" | "options"
  final String text;
  final int firstOutputMs;
  final int totalMs;
}

class ErrorEvent extends ServerEvent {
  const ErrorEvent(this.message);
  final String message;
}

/// "ok", "thanks!", "haha": a reaction, not a question (server chatter.py).
/// A short reply with no turn behind it -- nothing was analysed or stored,
/// and whatever cards or options were open are still open.
class ChatterEvent extends ServerEvent {
  const ChatterEvent(this.text);
  final String text;
}

/// Live rewrite of the latest answer at new slider levels. Every frame
/// carries the client's request id so pieces of a superseded rewrite can be
/// dropped.
class RegenStart extends ServerEvent {
  const RegenStart(this.requestId);
  final int requestId;
}

class RegenDelta extends ServerEvent {
  const RegenDelta(this.requestId, this.text);
  final int requestId;
  final String text;
}

class RegenDone extends ServerEvent {
  const RegenDone(this.requestId, this.text);
  final int requestId;
  final String text;
}

/// The rewrite ended without a new answer: nothing to rewrite, or it failed.
class RegenEnded extends ServerEvent {
  const RegenEnded(this.requestId);
  final int requestId;
}

/// The answer on its way is shaped to the learner's usual way in.
class AdaptedEvent extends ServerEvent {
  const AdaptedEvent(this.shaping);
  final AnswerShaping shaping;
}

/// Whether Versa guessed the direction just taken (after `turn_start`).
class GuessEvent extends ServerEvent {
  const GuessEvent(this.guess);
  final DirectionGuess guess;
}

class ClaimUpdateEvent extends ServerEvent {
  const ClaimUpdateEvent(this.update);
  final ClaimUpdate update;
}

/// The stage's performance for an answer turn (server.py "stage" frames):
/// `stage_start`, then one [StageActionEvent] per action as the director
/// writes it, then `stage_end`. Raw action JSON -- the stage parses it
/// (stage/script.dart), the chat doesn't need to understand it.
class StageStart extends ServerEvent {
  const StageStart(this.turnIndex);
  final int turnIndex;
}

class StageActionEvent extends ServerEvent {
  const StageActionEvent(this.turnIndex, this.action);
  final int turnIndex;
  final Map<String, dynamic> action;
}

/// Memory knew what the student meant (server.py "recalled" frame). With
/// [retracted], the options already shown this turn are taken back and the
/// answer's deltas follow.
class RecalledEvent extends ServerEvent {
  const RecalledEvent({required this.retracted});
  final bool retracted;
}

class StageEnd extends ServerEvent {
  const StageEnd(this.turnIndex);
  final int turnIndex;
}

/// A lesson task was judged complete (lesson chats only; server `progress`
/// frame, may arrive after `done`). Not chat content: it goes to
/// ChatController.progressEvents.
/// The directions offered under an answered turn (after its `done`).
class DirectionsEvent extends ServerEvent {
  const DirectionsEvent(this.turnIndex, this.cards, {this.setId});
  final int turnIndex;
  final List<DirectionCard> cards;

  /// The hand's id: "other directions" names it. Null from an older server.
  final String? setId;
}

/// No other directions are left for this answer ("other directions" asked
/// once the server had nothing more to deal).
class DirectionsExhaustedEvent extends ServerEvent {
  const DirectionsExhaustedEvent(this.turnIndex);
  final int turnIndex;
}

class ProgressEvent extends ServerEvent {
  const ProgressEvent({
    required this.lessonId,
    required this.taskId,
    required this.lessonPercent,
    required this.chapterPercent,
    required this.topicPercent,
    required this.lessonStatus,
  });

  final String lessonId;
  final String taskId;
  final int lessonPercent;
  final int chapterPercent;
  final int topicPercent;
  final String lessonStatus;
}

/// Out of Sparks (server `paywall` frame, src/versa/sparks.py): the turn did
/// not run. [detail] is the server's reason (needed, balance, next_refill_at).
class PaywallEvent extends ServerEvent {
  const PaywallEvent(this.detail);
  final Map<String, dynamic> detail;
}

/// An answer was charged (server `sparks` frame, just before `done`).
class SparksEvent extends ServerEvent {
  const SparksEvent({required this.balance, required this.spent});
  final int balance;
  final int spent;
}

/// Learning paid back: a finished lesson or a study streak.
class SparksRewardEvent extends ServerEvent {
  const SparksRewardEvent({required this.reason, required this.amount});
  final String reason;
  final int amount;
}

ServerEvent? parseServerEvent(Map<String, dynamic> json) {
  switch (json['type']) {
    case 'turn_start':
      return TurnStart((json['turn_index'] as num).toInt());
    case 'delta':
      return Delta(json['text'] as String? ?? '');
    case 'options':
      return OptionsEvent(
        json['message'] as String? ?? '',
        [
          for (final o in (json['options'] as List? ?? const []))
            ChatOption(id: o['id'] as String, text: o['text'] as String),
        ],
      );
    case 'done':
      final timing = (json['timing'] as Map?) ?? const {};
      return Done(
        turnIndex: (json['turn_index'] as num).toInt(),
        kind: json['kind'] as String? ?? 'answer',
        text: json['text'] as String? ?? '',
        firstOutputMs: (timing['first_output_ms'] as num?)?.toInt() ?? 0,
        totalMs: (timing['total_ms'] as num?)?.toInt() ?? 0,
      );
    case 'error':
      return ErrorEvent(json['message'] as String? ?? 'something went wrong');
    case 'chatter':
      return ChatterEvent(json['text'] as String? ?? '');
    case 'paywall':
      return PaywallEvent(json);
    case 'sparks':
      return SparksEvent(
        balance: (json['balance'] as num?)?.toInt() ?? 0,
        spent: (json['spent'] as num?)?.toInt() ?? 0,
      );
    case 'sparks_reward':
      return SparksRewardEvent(
        reason: json['reason'] as String? ?? '',
        amount: (json['amount'] as num?)?.toInt() ?? 0,
      );
    case 'adapted':
      return AdaptedEvent(AnswerShaping.fromJson(json));
    case 'guess':
      return GuessEvent(DirectionGuess.fromJson(json));
    case 'claim_update':
      return ClaimUpdateEvent(ClaimUpdate.fromJson(json));
    case 'regen_start':
      return RegenStart((json['request_id'] as num).toInt());
    case 'regen_delta':
      return RegenDelta((json['request_id'] as num).toInt(), json['text'] as String? ?? '');
    case 'regen_done':
      return RegenDone((json['request_id'] as num).toInt(), json['text'] as String? ?? '');
    case 'regen_skipped':
    case 'regen_error':
      return RegenEnded((json['request_id'] as num?)?.toInt() ?? -1);
    case 'stage_start':
      return StageStart((json['turn_index'] as num?)?.toInt() ?? -1);
    case 'stage':
      final action = json['action'];
      if (action is! Map) return null;
      return StageActionEvent((json['turn_index'] as num?)?.toInt() ?? -1, action.cast<String, dynamic>());
    case 'recalled':
      return RecalledEvent(retracted: json['retracted'] == true);
    case 'stage_end':
      return StageEnd((json['turn_index'] as num?)?.toInt() ?? -1);
    case 'directions':
      return DirectionsEvent(
        (json['turn_index'] as num?)?.toInt() ?? -1,
        [
          for (final c in (json['cards'] as List? ?? const []))
            DirectionCard(id: c['id'] as String, text: c['text'] as String, family: c['family'] as String?),
        ],
        setId: json['set_id'] as String?,
      );
    case 'directions_exhausted':
      return DirectionsExhaustedEvent((json['turn_index'] as num?)?.toInt() ?? -1);
    case 'progress':
      return ProgressEvent(
        lessonId: json['lesson_id'] as String? ?? '',
        taskId: json['task_id'] as String? ?? '',
        lessonPercent: (json['lesson_percent'] as num?)?.toInt() ?? 0,
        chapterPercent: (json['chapter_percent'] as num?)?.toInt() ?? 0,
        topicPercent: (json['topic_percent'] as num?)?.toInt() ?? 0,
        lessonStatus: json['lesson_status'] as String? ?? 'in_progress',
      );
  }
  return null; // unknown frame types are ignored, never fatal
}

// ------------------------------------------------------------- chat history

/// One row of the chat-history sidebar (`GET /api/learners/{id}/sessions`) --
/// this learner's chats within ONE app mode, newest-active first. `preview`
/// is null for a chat with no messages yet ("New chat" in the UI).
class ChatSummary {
  const ChatSummary({
    required this.sessionId,
    required this.appMode,
    required this.turnCount,
    required this.createdAt,
    required this.lastActivityAt,
    this.preview,
    this.title,
    this.about,
    this.scene,
    this.lessonId,
  });

  final String sessionId;
  final String appMode;
  final int turnCount;
  final DateTime createdAt;
  final DateTime lastActivityAt;
  final String? preview;

  /// What the chat is about, in clean words (server: chat_titles.py): a
  /// short title and one sentence. Null for a chat that was never described.
  final String? title;
  final String? about;

  /// The chat's latest stage performance (a stage script), which the Home
  /// feed plays as the card's picture. Only the feed sends it.
  final List<Map<String, dynamic>>? scene;

  /// The chat's opening message as a caption: the words alone, without the
  /// bracketed reading of a picture sent with them.
  String? get cleanPreview {
    final text = preview;
    if (text == null) return null;
    final cut = text.indexOf('[Attached picture -- what it shows:');
    var words = (cut < 0 ? text : text.substring(0, cut)).replaceAll(RegExp(r'\s+'), ' ').trim();
    if (words.startsWith('(They sent only this picture')) words = '';
    return words.isEmpty ? 'A picture you sent' : words;
  }

  /// Set on a Learn-a-topic chat: the lesson it belongs to.
  final String? lessonId;

  factory ChatSummary.fromJson(Map<String, dynamic> json) => ChatSummary(
        sessionId: json['session_id'] as String,
        appMode: json['app_mode'] as String,
        turnCount: (json['turn_count'] as num).toInt(),
        createdAt: DateTime.parse(json['created_at'] as String),
        lastActivityAt: DateTime.parse(json['last_activity_at'] as String),
        preview: json['preview'] as String?,
        title: json['title'] as String?,
        about: json['about'] as String?,
        scene: (json['scene'] as List?)?.whereType<Map>().map((a) => a.cast<String, dynamic>()).toList(),
        lessonId: json['lesson_id'] as String?,
      );
}

const _noResponseText = 'No response was recorded for this message.';

/// `GET /api/sessions/{id}/history`'s turn-by-turn record, expanded into the
/// same `ChatMessage` shape the live chat builds turn by turn (see
/// ChatController._onEvent) -- one rendering path either way, not two.
List<ChatMessage> parseSessionHistory(List<dynamic> json) {
  final messages = <ChatMessage>[];
  var id = 0;
  for (final raw in json) {
    final turn = raw as Map<String, dynamic>;
    // null (not just empty) on a resumed click-resolution answer turn --
    // the live chat shows no bubble for a click either (see
    // ChatController.pickOption), so a resumed chat must not add one just
    // because the option's own copy is sitting in student_text.
    final studentText = turn['student_text'] as String?;
    if (studentText != null) {
      messages.add(ChatMessage(id: id++, role: Role.user, text: studentText));
    }
    final claimUpdateJson = turn['claim_update'] as Map<String, dynamic>?;
    final claimUpdate = claimUpdateJson == null ? null : ClaimUpdate.fromJson(claimUpdateJson);
    switch (turn['kind']) {
      case 'answer':
        messages.add(
          ChatMessage(id: id++, role: Role.tutor, text: turn['tutor_text'] as String? ?? '')
            ..claimUpdate = claimUpdate,
        );
      case 'options':
        final rows = (turn['options'] as List? ?? const []).cast<Map<String, dynamic>>();
        final options = [
          for (final o in rows) ChatOption(id: o['id'] as String, text: o['text'] as String),
        ];
        final selected = rows.where((o) => o['status'] == 'selected');
        messages.add(
          ChatMessage(
            id: id++,
            role: Role.tutor,
            text: turn['options_message'] as String? ?? '',
            options: options,
            chosenOptionId: selected.isEmpty ? null : selected.first['id'] as String,
            optionsOpen: rows.any((o) => o['status'] == 'open'),
          ),
        );
      case 'pending':
        messages.add(
          ChatMessage(id: id++, role: Role.tutor, text: _noResponseText, isError: true),
        );
    }
  }
  return messages;
}

// ------------------------------------------------------------ thinking style

/// One row in the Thinking-style page's Confirmed/Emerging/Retired sections
/// or Claims list (`GET /api/learners/{id}/thinking-style`).
class ThinkingStyleItem {
  const ThinkingStyleItem({
    required this.id,
    required this.summary,
    required this.status,
    required this.confirmations,
    required this.sessions,
    required this.edited,
    required this.archived,
    this.test,
    this.confidence,
    this.evidenceCount,
  });

  final String id;
  final String summary;
  final String status;
  final int confirmations;
  final int sessions;
  final bool edited;
  final bool archived;
  // Claims only.
  final String? test;
  final double? confidence;
  final int? evidenceCount;

  factory ThinkingStyleItem.fromJson(Map<String, dynamic> j) => ThinkingStyleItem(
        id: j['id'] as String,
        summary: (j['summary'] ?? j['statement']) as String,
        status: j['status'] as String,
        confirmations: (j['confirmations'] as num?)?.toInt() ?? 0,
        sessions: (j['sessions'] as num).toInt(),
        edited: j['edited'] as bool,
        archived: j['archived'] as bool,
        test: j['test'] as String?,
        confidence: (j['confidence'] as num?)?.toDouble(),
        evidenceCount: (j['evidence_count'] as num?)?.toInt(),
      );
}

class ThinkingStyleOverview {
  const ThinkingStyleOverview({
    required this.promotionThreshold,
    required this.confirmed,
    required this.emerging,
    required this.retired,
    required this.claims,
  });

  final int promotionThreshold;
  final List<ThinkingStyleItem> confirmed;
  final List<ThinkingStyleItem> emerging;
  final List<ThinkingStyleItem> retired;
  final List<ThinkingStyleItem> claims;

  factory ThinkingStyleOverview.fromJson(Map<String, dynamic> j) => ThinkingStyleOverview(
        promotionThreshold: (j['promotion_threshold'] as num).toInt(),
        confirmed: [for (final r in j['confirmed'] as List) ThinkingStyleItem.fromJson(r)],
        emerging: [for (final r in j['emerging'] as List) ThinkingStyleItem.fromJson(r)],
        retired: [for (final r in j['retired'] as List) ThinkingStyleItem.fromJson(r)],
        claims: [for (final r in j['claims'] as List) ThinkingStyleItem.fromJson(r)],
      );
}

/// One entry in an item's review history (`ReviewOut`).
class ReviewEntry {
  const ReviewEntry({
    required this.id,
    required this.reviewType,
    required this.source,
    this.revisedStatement,
  });

  final String id;
  final String reviewType;
  final String source;
  final String? revisedStatement;

  factory ReviewEntry.fromJson(Map<String, dynamic> j) => ReviewEntry(
        id: j['id'] as String,
        reviewType: j['review_type'] as String,
        source: j['source'] as String,
        revisedStatement: j['revised_statement'] as String?,
      );
}

/// `GET /api/claims/{id}` -- the View action's full detail.
class ClaimDetail {
  const ClaimDetail({
    required this.id,
    required this.statement,
    required this.edited,
    required this.archived,
    required this.test,
    required this.status,
    required this.confidence,
    required this.evidenceCount,
    required this.reviews,
    this.evidenceLines = const [],
  });

  /// One readable line per evidence episode (date, topic, supports/contradicts).
  final List<String> evidenceLines;
  final String id;
  final String statement;
  final bool edited;
  final bool archived;
  final String test;
  final String status;
  final double confidence;
  final int evidenceCount;
  final List<ReviewEntry> reviews;

  factory ClaimDetail.fromJson(Map<String, dynamic> j) => ClaimDetail(
        id: j['id'] as String,
        statement: j['statement'] as String,
        edited: j['edited'] as bool,
        archived: j['archived'] as bool,
        test: j['test'] as String,
        status: j['status'] as String,
        confidence: (j['confidence'] as num).toDouble(),
        evidenceCount: (j['evidence'] as List).length,
        reviews: [for (final r in j['reviews'] as List) ReviewEntry.fromJson(r)],
        evidenceLines: [
          for (final e in j['evidence'] as List)
            '${(e['created_at'] as String).substring(0, 10)} · ${e['topic']} · ${e['direction']}',
        ],
      );
}

/// `GET /api/thinking-style/candidates/{id}` -- the View action's full detail.
class ThinkingStyleDetail {
  const ThinkingStyleDetail({
    required this.id,
    required this.summary,
    required this.edited,
    required this.archived,
    required this.status,
    required this.confirmations,
    required this.sessionCount,
    required this.reviews,
    this.sessionLines = const [],
  });

  /// One readable line per confirming session (its opening message, and
  /// whether it matched the pattern).
  final List<String> sessionLines;

  final String id;
  final String summary;
  final bool edited;
  final bool archived;
  final String status;
  final int confirmations;
  final int sessionCount;
  final List<ReviewEntry> reviews;

  factory ThinkingStyleDetail.fromJson(Map<String, dynamic> j) => ThinkingStyleDetail(
        id: j['id'] as String,
        summary: j['summary'] as String,
        edited: j['edited'] as bool,
        archived: j['archived'] as bool,
        status: j['status'] as String,
        confirmations: (j['confirmations'] as num).toInt(),
        sessionCount: (j['sessions'] as List).length,
        reviews: [for (final r in j['reviews'] as List) ReviewEntry.fromJson(r)],
        sessionLines: [
          for (final x in j['sessions'] as List)
            'Session ${(x['index'] as num).toInt() + 1}: "${x['topic_preview']}"'
                '${x['confirms'] == null ? ' (started this pattern)' : x['confirms'] == true ? ' (matched)' : ' (did not match)'}',
        ],
      );
}

class QnAEntry {
  const QnAEntry({
    required this.id,
    required this.question,
    required this.answer,
    required this.intent,
    this.appliedReviewId,
  });

  final String id;
  final String question;
  final String answer;
  final String intent;
  final String? appliedReviewId;

  factory QnAEntry.fromJson(Map<String, dynamic> j) => QnAEntry(
        id: j['id'] as String,
        question: j['question'] as String,
        answer: j['answer'] as String,
        intent: j['intent'] as String,
        appliedReviewId: j['applied_review_id'] as String?,
      );
}
