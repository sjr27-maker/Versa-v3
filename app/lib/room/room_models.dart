/// Rooms (src/versa/rooms/): the wire shapes and the live events a room's
/// WebSocket sends (hub.py's module docstring lists them).
library;

class RoomPart {
  const RoomPart({required this.title, required this.summary});
  final String title;
  final String summary;

  factory RoomPart.fromJson(Map<String, dynamic> j) =>
      RoomPart(title: j['title'] as String? ?? '', summary: j['summary'] as String? ?? '');
}

class RoomInfo {
  const RoomInfo({
    required this.id,
    required this.code,
    required this.title,
    required this.sourceKind,
    required this.query,
    required this.outline,
    required this.createdBy,
    this.resourceFilename,
    this.resourceUrl,
  });

  final String id;
  final String code;
  final String title;
  final String sourceKind; // search | pdf | link
  final String query;
  final List<RoomPart> outline;
  final String createdBy;
  final String? resourceFilename;
  final String? resourceUrl;

  factory RoomInfo.fromJson(Map<String, dynamic> j) => RoomInfo(
        id: j['id'] as String,
        code: j['code'] as String,
        title: j['title'] as String,
        sourceKind: j['source_kind'] as String? ?? 'search',
        query: j['query'] as String? ?? '',
        outline: [
          for (final p in (j['outline'] as List? ?? const [])) RoomPart.fromJson(p as Map<String, dynamic>),
        ],
        createdBy: j['created_by'] as String? ?? '',
        resourceFilename: j['resource_filename'] as String?,
        resourceUrl: j['resource_url'] as String?,
      );
}

class RoomMe {
  const RoomMe({required this.id, required this.name});
  final String id;
  final String name;

  factory RoomMe.fromJson(Map<String, dynamic> j) => RoomMe(id: j['id'] as String, name: j['name'] as String);
}

/// What create / join return: the room and who you are in it.
class RoomJoined {
  const RoomJoined({required this.room, required this.me});
  final RoomInfo room;
  final RoomMe me;

  factory RoomJoined.fromJson(Map<String, dynamic> j) => RoomJoined(
        room: RoomInfo.fromJson(j['room'] as Map<String, dynamic>),
        me: RoomMe.fromJson(j['member'] as Map<String, dynamic>),
      );
}

class RoomMessage {
  const RoomMessage({
    required this.id,
    required this.seq,
    required this.sender,
    required this.senderName,
    required this.kind,
    required this.text,
    required this.createdAt,
    this.memberId,
    this.toMemberId,
    this.toName,
    this.private = false,
    this.meta = const {},
  });

  final String id;
  final int seq;

  /// member | versa | system
  final String sender;
  final String? memberId;
  final String senderName;

  /// text | content | question | task | progress | pick | event
  final String kind;
  final String text;
  final String? toMemberId;
  final String? toName;
  final bool private;
  final Map<String, dynamic> meta;
  final DateTime createdAt;

  bool get fromVersa => sender == 'versa';
  bool get isSystem => sender == 'system';
  bool isMine(String meId) => sender == 'member' && memberId == meId;

  /// The option texts a question offered (clicked in the "For you" panel).
  List<String> get optionTexts => [for (final o in (meta['options'] as List? ?? const [])) '$o'];

  factory RoomMessage.fromJson(Map<String, dynamic> j) => RoomMessage(
        id: j['id'] as String,
        seq: j['seq'] as int,
        sender: j['sender'] as String,
        memberId: j['member_id'] as String?,
        senderName: j['sender_name'] as String? ?? '',
        kind: j['kind'] as String? ?? 'text',
        text: j['text'] as String? ?? '',
        toMemberId: j['to_member_id'] as String?,
        toName: j['to_name'] as String?,
        private: j['private'] as bool? ?? false,
        meta: (j['meta'] as Map?)?.cast<String, dynamic>() ?? const {},
        createdAt: DateTime.tryParse(j['created_at'] as String? ?? '')?.toLocal() ?? DateTime.now(),
      );
}

class RoomTask {
  const RoomTask({
    required this.id,
    required this.memberId,
    required this.memberName,
    required this.kind,
    required this.description,
    required this.done,
  });

  final String id;
  final String memberId;
  final String memberName;
  final String kind;
  final String description;
  final bool done;

  factory RoomTask.fromJson(Map<String, dynamic> j) => RoomTask(
        id: j['id'] as String,
        memberId: j['member_id'] as String,
        memberName: j['member_name'] as String? ?? '',
        kind: j['kind'] as String? ?? 'learn',
        description: j['description'] as String? ?? '',
        done: j['done'] as bool? ?? false,
      );
}

class RoomOption {
  const RoomOption({required this.id, required this.text});
  final String id;
  final String text;
}

class RoomOptionSet {
  const RoomOptionSet({
    required this.setId,
    required this.prompt,
    required this.forEveryone,
    required this.options,
    this.taskId,
    this.race = false,
  });

  final String setId;
  final String prompt;
  final bool forEveryone;
  final List<RoomOption> options;

  /// Set when these are the choices of this person's quiz task: one tap
  /// answers it (the server grades it).
  final String? taskId;

  /// One question for everyone: the first right tap wins.
  final bool race;

  factory RoomOptionSet.fromJson(Map<String, dynamic> j) => RoomOptionSet(
        race: j['race'] as bool? ?? false,
        setId: j['set_id'] as String,
        prompt: j['prompt'] as String? ?? '',
        forEveryone: j['for_everyone'] as bool? ?? false,
        taskId: j['task_id'] as String?,
        options: [
          for (final o in (j['options'] as List? ?? const []))
            RoomOption(id: (o as Map)['id'] as String, text: o['text'] as String? ?? ''),
        ],
      );
}

class RoomMemberInfo {
  const RoomMemberInfo({required this.id, required this.name, required this.online});
  final String id;
  final String name;
  final bool online;
}

/// One row of the scoreboard.
class RoomScore {
  const RoomScore({required this.name, required this.points, required this.wins});
  final String name;
  final int points;
  final int wins;
}

/// One part of the room's topic, in order: covered, the one the group is
/// on, or still ahead.
class RoomPartProgress {
  const RoomPartProgress({required this.title, required this.done, required this.current});
  final String title;
  final bool done;
  final bool current;
}

/// Everything around the chat: who's here, everyone's tasks, the topic's
/// parts, and the options still waiting for THIS person.
class RoomBoard {
  const RoomBoard({
    this.members = const [],
    this.tasks = const [],
    this.options = const [],
    this.parts = const [],
    this.scores = const [],
  });

  final List<RoomMemberInfo> members;
  final List<RoomTask> tasks;
  final List<RoomOptionSet> options;
  final List<RoomPartProgress> parts;

  /// The scoreboard, highest first (a race won is worth 3, a task right 1).
  final List<RoomScore> scores;

  /// The race still open for this person, if any.
  RoomOptionSet? get openRace => options.where((s) => s.race).firstOrNull;

  /// This person's open quiz task, as the choices to tap.
  RoomOptionSet? get myQuiz => options.where((s) => s.taskId != null).firstOrNull;

  /// The open option sets that aren't a quiz task.
  List<RoomOptionSet> get otherOptions => [for (final s in options) if (s.taskId == null) s];

  /// The set with this id, while it is still open for this person.
  RoomOptionSet? openSet(Object? setId) => options.where((s) => s.setId == setId).firstOrNull;

  static const empty = RoomBoard();

  factory RoomBoard.fromJson(Map<String, dynamic> j) => RoomBoard(
        members: [
          for (final m in (j['members'] as List? ?? const []))
            RoomMemberInfo(
              id: (m as Map)['id'] as String,
              name: m['name'] as String? ?? '',
              online: m['online'] as bool? ?? false,
            ),
        ],
        tasks: [for (final t in (j['tasks'] as List? ?? const [])) RoomTask.fromJson(t as Map<String, dynamic>)],
        options: [
          for (final s in (j['options'] as List? ?? const [])) RoomOptionSet.fromJson(s as Map<String, dynamic>),
        ],
        scores: [
          for (final s in (j['scores'] as List? ?? const []))
            RoomScore(
              name: (s as Map)['name'] as String? ?? '',
              points: (s['points'] as num?)?.toInt() ?? 0,
              wins: (s['wins'] as num?)?.toInt() ?? 0,
            ),
        ],
        parts: [
          for (final p in (j['parts'] as List? ?? const []))
            RoomPartProgress(
              title: (p as Map)['title'] as String? ?? '',
              done: p['done'] as bool? ?? false,
              current: p['current'] as bool? ?? false,
            ),
        ],
      );

  List<RoomTask> tasksOf(String memberId) => [for (final t in tasks) if (t.memberId == memberId) t];

  /// A person's current task: their oldest one still open.
  RoomTask? currentTaskOf(String memberId) =>
      tasks.where((t) => t.memberId == memberId && !t.done).firstOrNull;
}

/// One row of the rooms list.
class RoomSummary {
  const RoomSummary({
    required this.code,
    required this.title,
    required this.memberId,
    required this.name,
    required this.memberNames,
    required this.online,
    required this.unread,
    required this.createdAt,
    this.lastMessage,
  });

  final String code;
  final String title;
  final String memberId;
  final String name;
  final List<String> memberNames;
  final int online;
  final int unread;
  final DateTime createdAt;
  final RoomMessage? lastMessage;

  DateTime get activityAt => lastMessage?.createdAt ?? createdAt;

  factory RoomSummary.fromJson(Map<String, dynamic> j) => RoomSummary(
        code: j['code'] as String,
        title: j['title'] as String? ?? '',
        memberId: j['member_id'] as String,
        name: j['name'] as String? ?? '',
        memberNames: [for (final n in (j['member_names'] as List? ?? const [])) '$n'],
        online: j['online'] as int? ?? 0,
        unread: j['unread'] as int? ?? 0,
        createdAt: DateTime.tryParse(j['created_at'] as String? ?? '')?.toLocal() ?? DateTime.now(),
        lastMessage: j['last_message'] == null
            ? null
            : RoomMessage.fromJson(j['last_message'] as Map<String, dynamic>),
      );
}

// ------------------------------------------------------------ live events

sealed class RoomEvent {
  const RoomEvent();
}

class RoomStateEvent extends RoomEvent {
  const RoomStateEvent(this.room, this.me, this.messages, this.board);
  final RoomInfo room;
  final RoomMe me;
  final List<RoomMessage> messages;
  final RoomBoard board;
}

class RoomMessageEvent extends RoomEvent {
  const RoomMessageEvent(this.message);
  final RoomMessage message;
}

class RoomBoardEvent extends RoomEvent {
  const RoomBoardEvent(this.board);
  final RoomBoard board;
}

class RoomTypingEvent extends RoomEvent {
  const RoomTypingEvent({required this.name, required this.on, this.memberId});

  /// Null: Versa.
  final String? memberId;
  final String name;
  final bool on;
}

class RoomStageStart extends RoomEvent {
  const RoomStageStart(this.seq);
  final int seq;
}

class RoomStageAction extends RoomEvent {
  const RoomStageAction(this.seq, this.action);
  final int seq;
  final Map<String, dynamic> action;
}

class RoomStageEnd extends RoomEvent {
  const RoomStageEnd(this.seq);
  final int seq;
}

class RoomErrorEvent extends RoomEvent {
  const RoomErrorEvent(this.message);
  final String message;
}

RoomEvent? parseRoomEvent(Map<String, dynamic> j) {
  switch (j['type']) {
    case 'state':
      return RoomStateEvent(
        RoomInfo.fromJson(j['room'] as Map<String, dynamic>),
        RoomMe.fromJson(j['me'] as Map<String, dynamic>),
        [for (final m in (j['messages'] as List? ?? const [])) RoomMessage.fromJson(m as Map<String, dynamic>)],
        RoomBoard.fromJson(j['board'] as Map<String, dynamic>),
      );
    case 'message':
      return RoomMessageEvent(RoomMessage.fromJson(j['message'] as Map<String, dynamic>));
    case 'board':
      return RoomBoardEvent(RoomBoard.fromJson(j['board'] as Map<String, dynamic>));
    case 'typing':
      return RoomTypingEvent(
        memberId: j['member_id'] as String?,
        name: j['name'] as String? ?? '',
        on: j['on'] as bool? ?? true,
      );
    case 'stage_start':
      return RoomStageStart(j['seq'] as int? ?? 0);
    case 'stage':
      return RoomStageAction(j['seq'] as int? ?? 0, (j['action'] as Map).cast<String, dynamic>());
    case 'stage_end':
      return RoomStageEnd(j['seq'] as int? ?? 0);
    case 'error':
      return RoomErrorEvent(j['message'] as String? ?? 'something went wrong');
  }
  return null;
}
