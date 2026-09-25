import 'script.dart';

/// A hand-written skit, kept as raw JSON on purpose: it is exactly the shape
/// a generator will send later, so if this parses and plays, a generated one
/// will too. It exists to judge the engine's feel (timing, faces, the ask)
/// before any LLM is involved -- not as content the app depends on.
const List<Map<String, dynamic>> forceSkitJson = [
  {'do': 'move', 'target': 'blob', 'x': 0.2, 'ms': 900},
  {'do': 'say', 'text': 'Ahem. Today: FORCE.'},
  {'do': 'spawn', 'id': 'box', 'kind': 'box', 'x': 0.55, 'y': kGroundY, 'label': 'box'},
  {'do': 'emote', 'mood': 'proud'},
  {'do': 'say', 'text': 'One tiny nudge and this thing will fly.'},
  {'do': 'approach', 'target': 'box'},
  {
    'do': 'together',
    'actions': [
      {'do': 'emote', 'mood': 'strain'},
      {'do': 'shake', 'target': 'box', 'ms': 900},
      {'do': 'shake', 'target': 'blob', 'ms': 900},
    ],
  },
  {'do': 'say', 'text': 'Hnnngh... it is not flying.'},
  {'do': 'emote', 'mood': 'thinking'},
  {'do': 'say', 'text': 'A small force barely moves a heavy box.'},
  {
    'do': 'ask',
    'question': 'So what should I change?',
    'choices': [
      {'id': 'harder', 'text': 'Push harder!'},
      {'id': 'lighter', 'text': 'Make the box lighter'},
    ],
    'then': {
      'harder': [
        {'do': 'emote', 'mood': 'excited'},
        {'do': 'say', 'text': 'MAXIMUM SLIME POWER!', 'ms': 1100},
        {'do': 'push', 'target': 'box', 'dx': 0.22, 'ms': 900},
        {
          'do': 'spawn', 'id': 'force', 'kind': 'arrow', 'label': 'Force',
          'x': 0.5, 'y': 0.3, 'x2': 0.72, 'y2': 0.3,
        },
        {'do': 'spawn', 'id': 'moves', 'kind': 'text', 'x': 0.86, 'y': 0.3, 'label': 'moves!'},
        {'do': 'emote', 'mood': 'happy'},
        {'do': 'say', 'text': 'Bigger push, bigger speed-up.'},
      ],
      'lighter': [
        {'do': 'emote', 'mood': 'surprised'},
        {'do': 'remove', 'id': 'box'},
        {'do': 'spawn', 'id': 'light', 'kind': 'box', 'x': 0.55, 'y': kGroundY, 'size': 0.55},
        {'do': 'say', 'text': 'Same little nudge...', 'ms': 1200},
        {'do': 'push', 'target': 'light', 'dx': 0.3, 'ms': 500},
        {'do': 'spawn', 'id': 'moves', 'kind': 'text', 'x': 0.86, 'y': 0.4, 'label': 'whoosh!'},
        {'do': 'emote', 'mood': 'excited'},
        {'do': 'say', 'text': 'Less mass, same force: way more speed.'},
      ],
    },
  },
  {'do': 'wait', 'ms': 400},
  {'do': 'spawn', 'id': 'law', 'kind': 'text', 'x': 0.5, 'y': 0.14, 'label': 'F = m × a', 'size': 1.5},
  {'do': 'look', 'target': 'law'},
  {'do': 'emote', 'mood': 'proud'},
  {'do': 'jump', 'times': 2},
  {'do': 'say', 'text': "Newton's second law. Nailed it."},
  {'do': 'emote', 'mood': 'neutral'},
  {'do': 'look'},
];

/// Gravity, Newton-style: exercises the wider vocabulary -- emoji objects,
/// a hat, carrying and throwing, falling, particle effects, a drawn arrow.
const List<Map<String, dynamic>> gravitySkitJson = [
  {'do': 'wear', 'label': '🎓'},
  {'do': 'move', 'target': 'blob', 'x': 0.25, 'ms': 800},
  {'do': 'say', 'text': 'Professor Slime, reporting for science.'},
  {'do': 'spawn', 'id': 'tree', 'kind': 'emoji', 'label': '🌳', 'x': 0.72, 'size': 2.6},
  {'do': 'spawn', 'id': 'apple', 'kind': 'emoji', 'label': '🍎', 'x': 0.7, 'y': 0.3, 'size': 0.7},
  {'do': 'move', 'target': 'blob', 'x': 0.66, 'ms': 900},
  {
    'do': 'together',
    'actions': [
      {'do': 'emote', 'mood': 'happy'},
      {'do': 'effect', 'kind': 'zzz', 'ms': 1800},
      {'do': 'say', 'text': 'Just a quick nap under this tree...', 'ms': 1800},
    ],
  },
  {'do': 'move', 'target': 'apple', 'x': 0.7, 'y': 0.47, 'style': 'fall', 'ms': 420},
  {'do': 'effect', 'kind': 'stars'},
  {'do': 'emote', 'mood': 'surprised'},
  {'do': 'shake', 'target': 'blob', 'ms': 400},
  {'do': 'move', 'target': 'apple', 'x': 0.78, 'y': kGroundY, 'style': 'leap', 'ms': 500},
  {'do': 'say', 'text': 'OW. Rude.', 'ms': 1000},
  {'do': 'emote', 'mood': 'thinking'},
  {'do': 'say', 'text': 'But wait... why did it fall DOWN? Why not up, or sideways?'},
  {
    'do': 'spawn', 'id': 'pull', 'kind': 'arrow', 'label': 'gravity', 'color': 'blue',
    'x': 0.86, 'y': 0.28, 'x2': 0.86, 'y2': 0.5,
  },
  {'do': 'carry', 'target': 'apple'},
  {'do': 'move', 'target': 'blob', 'x': 0.35, 'ms': 800},
  {'do': 'say', 'text': 'Experiment time!', 'ms': 900},
  {'do': 'throw', 'target': 'apple', 'x': 0.55, 'ms': 800},
  {'do': 'effect', 'kind': 'splash', 'x': 0.55, 'y': kGroundY},
  {'do': 'emote', 'mood': 'excited'},
  {'do': 'say', 'text': 'Up it goes... and down it comes. EVERY time.'},
  {
    'do': 'ask',
    'question': 'Would a feather fall just as fast?',
    'choices': [
      {'id': 'yes', 'text': 'Yes, same speed'},
      {'id': 'no', 'text': 'No, it floats down slower'},
    ],
    'then': {
      'yes': [
        {'do': 'emote', 'mood': 'proud'},
        {'do': 'say', 'text': 'Sneaky! In a vacuum, yes -- exactly the same.'},
      ],
      'no': [
        {'do': 'emote', 'mood': 'happy'},
        {'do': 'say', 'text': 'In air, yes -- but only because air pushes back on it.'},
      ],
    },
  },
  {'do': 'spawn', 'id': 'feather', 'kind': 'emoji', 'label': '🪶', 'x': 0.45, 'y': 0.2, 'size': 0.8},
  {'do': 'spawn', 'id': 'rock', 'kind': 'emoji', 'label': '🪨', 'x': 0.6, 'y': 0.2, 'size': 0.8},
  {
    'do': 'together',
    'actions': [
      {'do': 'move', 'target': 'rock', 'x': 0.6, 'y': kGroundY, 'style': 'fall', 'ms': 450},
      {'do': 'move', 'target': 'feather', 'x': 0.5, 'y': kGroundY, 'style': 'slide', 'ms': 2200},
    ],
  },
  {'do': 'spawn', 'id': 'earth', 'kind': 'emoji', 'label': '🌍', 'x': 0.15, 'y': 0.3, 'size': 1.2},
  {'do': 'spin', 'target': 'earth', 'turns': 1, 'ms': 1200},
  {'do': 'say', 'text': 'The Earth pulls everything toward it. That pull is gravity.'},
  {'do': 'effect', 'kind': 'confetti'},
  {'do': 'wear', 'label': '👑'},
  {'do': 'jump', 'times': 2},
  {'do': 'say', 'text': 'Nobel Prize, please. I accept cash.'},
  {'do': 'emote', 'mood': 'neutral'},
  {'do': 'look'},
];

List<StageAction> get forceSkit => parseScript(forceSkitJson);
List<StageAction> get gravitySkit => parseScript(gravitySkitJson);

/// The demo button cycles through these.
List<List<StageAction>> get demoSkits => [forceSkit, gravitySkit];
