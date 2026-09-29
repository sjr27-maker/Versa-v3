import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../api.dart';
import '../app_state.dart';
import '../auth/auth_widgets.dart';
import '../models.dart';
import '../theme.dart';

/// The sign-up questions (src/versa/profiles.py), asked once right after
/// the first sign-in and editable later from Settings: who they are, what
/// they're doing, where and at what level, what they're working towards --
/// and consent (a parent's or guardian's too, under 18).
///
/// Anywhere in the world: India gets its states, boards and classes to pick
/// from; everywhere else is typed, and the server's reading of the answers
/// tidies it up. Age is checked for sense (5 to 100 here, and an "are you
/// sure?" when it can't match what they said) but never sets their level on
/// its own -- what they say they're doing does.
class ProfileScreen extends StatefulWidget {
  const ProfileScreen({super.key, this.editing = false, this.initial, this.onSaved});

  /// From Settings: prefilled, and it closes when saved.
  final bool editing;
  final LearnerProfile? initial;
  final VoidCallback? onSaved;

  @override
  State<ProfileScreen> createState() => _ProfileScreenState();
}

const kMinAge = 5;
const kMaxAge = 100;
const kAdultAge = 18;

const indianStates = [
  'Andhra Pradesh', 'Arunachal Pradesh', 'Assam', 'Bihar', 'Chhattisgarh', 'Goa', 'Gujarat', 'Haryana',
  'Himachal Pradesh', 'Jharkhand', 'Karnataka', 'Kerala', 'Madhya Pradesh', 'Maharashtra', 'Manipur',
  'Meghalaya', 'Mizoram', 'Nagaland', 'Odisha', 'Punjab', 'Rajasthan', 'Sikkim', 'Tamil Nadu', 'Telangana',
  'Tripura', 'Uttar Pradesh', 'Uttarakhand', 'West Bengal',
  // union territories
  'Andaman and Nicobar Islands', 'Chandigarh', 'Dadra and Nagar Haveli and Daman and Diu', 'Delhi',
  'Jammu and Kashmir', 'Ladakh', 'Lakshadweep', 'Puducherry',
];

const indianSchoolBoards = [
  'CBSE', 'CISCE (ICSE / ISC)', 'State Board', 'IB', 'Cambridge (IGCSE / A Level)', 'NIOS', 'Other',
];

const universityYears = ['1st year', '2nd year', '3rd year', '4th year', '5th year', 'Postgraduate', 'PhD'];

const countries = [
  'India', 'United States', 'United Kingdom', 'Canada', 'Australia', 'United Arab Emirates', 'Saudi Arabia',
  'Qatar', 'Oman', 'Kuwait', 'Bahrain', 'Singapore', 'Malaysia', 'Sri Lanka', 'Nepal', 'Bangladesh',
  'Pakistan', 'Indonesia', 'Philippines', 'Thailand', 'Vietnam', 'China', 'Japan', 'South Korea', 'Germany',
  'France', 'Netherlands', 'Ireland', 'Italy', 'Spain', 'Sweden', 'Switzerland', 'Poland', 'Russia',
  'Turkey', 'Egypt', 'Nigeria', 'Kenya', 'South Africa', 'Ghana', 'Brazil', 'Mexico', 'Argentina', 'Chile',
  'Colombia', 'New Zealand',
];

class _ProfileScreenState extends State<ProfileScreen> {
  static const _steps = 4;
  int _step = 0;
  bool _busy = false;
  String? _error;

  final _name = TextEditingController();
  final _age = TextEditingController();
  final _country = TextEditingController(text: 'India');
  final _region = TextEditingController();
  final _institution = TextEditingController();
  final _curriculum = TextEditingController();
  final _level = TextEditingController();
  final _course = TextEditingController();
  final _subjects = TextEditingController();
  final _goals = TextEditingController();
  String? _occupation;
  String? _state; // an Indian state, picked
  String? _board; // an Indian school board, picked
  String? _classLevel; // Class 1-12, picked
  String? _year; // university year, picked
  bool _consentData = false;
  bool _consentGuardian = false;

  @override
  void initState() {
    super.initState();
    final app = context.read<AppState>();
    final a = widget.initial?.answers ?? const <String, dynamic>{};
    _name.text = (a['name'] as String?) ?? app.learner?.label ?? '';
    if (a['age'] != null) _age.text = '${a['age']}';
    if (a['country'] is String) _country.text = a['country'] as String;
    _occupation = a['occupation'] as String?;
    final region = a['region'] as String?;
    if (_isIndia && indianStates.contains(region)) {
      _state = region;
    } else {
      _region.text = region ?? '';
    }
    _institution.text = (a['institution'] as String?) ?? '';
    final curriculum = a['curriculum'] as String?;
    if (_isIndia && _occupation == 'school' && indianSchoolBoards.contains(curriculum)) {
      _board = curriculum;
    } else {
      _curriculum.text = curriculum ?? '';
    }
    final level = a['level'] as String?;
    if (_occupation == 'school' && _isIndia && _classes.contains(level)) {
      _classLevel = level;
    } else if (_occupation == 'university' && universityYears.contains(level)) {
      _year = level;
    } else {
      _level.text = level ?? '';
    }
    _course.text = (a['course'] as String?) ?? '';
    _subjects.text = (a['subjects'] as String?) ?? '';
    _goals.text = (a['goals'] as String?) ?? '';
    final consent = widget.initial?.consent ?? const <String, dynamic>{};
    _consentData = consent['data'] == true;
    _consentGuardian = consent['guardian'] == true;
  }

  @override
  void dispose() {
    for (final c in [_name, _age, _country, _region, _institution, _curriculum, _level, _course, _subjects, _goals]) {
      c.dispose();
    }
    super.dispose();
  }

  static final _classes = [for (var i = 1; i <= 12; i++) 'Class $i'];

  bool get _isIndia => _country.text.trim().toLowerCase() == 'india';
  int? get _ageValue => int.tryParse(_age.text.trim());
  bool get _minor => (_ageValue ?? kAdultAge) < kAdultAge;

  String? _checkStep() {
    switch (_step) {
      case 0:
        if (_name.text.trim().isEmpty) return 'What should Versa call you?';
        final age = _ageValue;
        if (age == null) return 'Enter your age in years.';
        if (age < kMinAge) return 'Versa is for learners aged $kMinAge and up.';
        if (age > kMaxAge) return 'Please enter your real age -- it helps Versa pitch things right.';
        return null;
      case 1:
        return _occupation == null ? 'Pick the one that fits best.' : null;
      case 2:
        if (_country.text.trim().isEmpty) return 'Which country are you in?';
        if (_isIndia && _state == null) return 'Pick your state.';
        if (_occupation == 'school') {
          if (_isIndia && _board == null) return 'Pick your board.';
          if (_isIndia && _classLevel == null) return 'Pick your class.';
          if (!_isIndia && _level.text.trim().isEmpty) return 'Which grade or year are you in?';
        }
        if (_occupation == 'university') {
          if (_institution.text.trim().isEmpty) return 'Which college or university?';
          if (_course.text.trim().isEmpty) return 'What are you studying?';
          if (_year == null) return 'Which year are you in?';
        }
        if (_occupation == 'working' && _course.text.trim().isEmpty) return 'What field do you work in?';
        return null;
      case 3:
        if (!_consentData) return 'Versa needs your OK to keep what you tell it.';
        if (_minor && !_consentGuardian) return 'Under $kAdultAge, a parent or guardian needs to agree too.';
        return null;
    }
    return null;
  }

  Map<String, dynamic> _answers() {
    String? t(TextEditingController c) => c.text.trim().isEmpty ? null : c.text.trim();
    final school = _occupation == 'school';
    final uni = _occupation == 'university';
    return {
      'name': _name.text.trim(),
      'age': _ageValue,
      'occupation': _occupation,
      'country': _country.text.trim(),
      'region': _isIndia ? _state : t(_region),
      'institution': (school || uni) ? t(_institution) : null,
      'curriculum': school ? (_isIndia ? _board : t(_curriculum)) : null,
      'level': school ? (_isIndia ? _classLevel : t(_level)) : (uni ? _year : null),
      'course': (uni || _occupation == 'working' || _occupation == 'other') ? t(_course) : null,
      'subjects': t(_subjects),
      'goals': t(_goals),
      'consent': {'data': _consentData, 'guardian': _minor ? _consentGuardian : false},
    };
  }

  void _next() {
    final problem = _checkStep();
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    setState(() {
      _error = null;
      _step += 1;
    });
  }

  void _back() => setState(() {
        _error = null;
        _step -= 1;
      });

  Future<void> _save() async {
    final problem = _checkStep();
    if (problem != null) {
      setState(() => _error = problem);
      return;
    }
    final app = context.read<AppState>();
    final learnerId = app.learner!.id;
    final answers = _answers();
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final warning = await app.api.checkProfile(learnerId, answers);
      if (warning != null && mounted) {
        setState(() => _busy = false);
        final sure = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Just checking'),
            content: Text('$warning Is that right?'),
            actions: [
              TextButton(
                key: const ValueKey('age-fix'),
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Let me fix it'),
              ),
              FilledButton(
                key: const ValueKey('age-confirm'),
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Yes, that\'s right'),
              ),
            ],
          ),
        );
        if (sure != true) {
          if (mounted) setState(() => _step = 0);
          return;
        }
        if (mounted) setState(() => _busy = true);
      }
      final profile = await app.api.saveProfile(learnerId, answers);
      app.profileSaved(profile);
      widget.onSaved?.call();
      if (widget.editing && mounted) Navigator.of(context).pop(profile);
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (e) {
      if (mounted) setState(() => _error = 'Could not save: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final body = Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(children: [
          if (_step > 0)
            IconButton(
              key: const ValueKey('profile-back'),
              tooltip: 'Back',
              onPressed: _busy ? null : _back,
              icon: const Icon(Icons.arrow_back, color: Paper.ink),
            )
          else if (widget.editing)
            IconButton(
              tooltip: 'Close',
              onPressed: () => Navigator.of(context).pop(),
              icon: const Icon(Icons.close, color: Paper.ink),
            ),
          Expanded(
            child: Padding(
              padding: EdgeInsets.only(left: _step > 0 || widget.editing ? 4 : 0),
              child: ClipRRect(
                borderRadius: BorderRadius.circular(100),
                child: LinearProgressIndicator(
                  value: (_step + 1) / _steps,
                  minHeight: 6,
                  backgroundColor: Paper.border,
                  color: Paper.accent,
                ),
              ),
            ),
          ),
          const SizedBox(width: 12),
          Text('${_step + 1} of $_steps', style: mono(12)),
        ]),
        const SizedBox(height: 22),
        ...switch (_step) {
          0 => _aboutYou(),
          1 => _whatYouDo(),
          2 => _details(),
          _ => _goalsAndConsent(),
        },
        if (_error != null) FormMessage(_error!),
        const SizedBox(height: 20),
        if (_step < _steps - 1)
          PrimaryButton(key: const ValueKey('profile-next'), label: 'Next', onPressed: _next)
        else
          PrimaryButton(
            key: const ValueKey('profile-save'),
            label: widget.editing ? 'Save' : 'Start learning',
            busy: _busy,
            onPressed: _save,
          ),
        if (_busy && _step == _steps - 1)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: Center(child: Text('Reading your answers…', style: sans(13, color: Paper.muted))),
          ),
      ],
    );
    return AuthScaffold(maxWidth: 520, child: body);
  }

  Widget _heading(String title, String subtitle) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: serif(26)),
          const SizedBox(height: 6),
          Text(subtitle, style: sans(14.5, color: Paper.muted, height: 1.45)),
          const SizedBox(height: 20),
        ],
      );

  List<Widget> _aboutYou() => [
        _heading(widget.editing ? 'Your profile' : 'Tell Versa about you',
            'So every explanation starts at your level and builds up from there.'),
        TextField(
          key: const ValueKey('profile-name'),
          controller: _name,
          textCapitalization: TextCapitalization.words,
          textInputAction: TextInputAction.next,
          style: sans(15),
          decoration: paperInput('Your name', label: 'What should Versa call you?'),
        ),
        const SizedBox(height: 12),
        TextField(
          key: const ValueKey('profile-age'),
          controller: _age,
          keyboardType: TextInputType.number,
          inputFormatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(3)],
          onChanged: (_) => setState(() {}),
          style: sans(15),
          decoration: paperInput('e.g. 16', label: 'How old are you?'),
        ),
      ];

  List<Widget> _whatYouDo() => [
        _heading('What are you doing right now?', 'Pick the one that fits best.'),
        for (final (value, label, detail, icon) in const [
          ('school', 'At school', 'Any class or grade', Icons.backpack_outlined),
          ('university', 'At college or university', 'A degree, diploma or postgraduate course', Icons.school_outlined),
          ('working', 'Working', 'Learning for work or a career change', Icons.work_outline),
          ('other', 'Something else', 'Preparing for an exam, a gap year, just curious...', Icons.explore_outlined),
        ])
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: _ChoiceCard(
              key: ValueKey('occupation-$value'),
              selected: _occupation == value,
              icon: icon,
              label: label,
              detail: detail,
              onTap: () => setState(() {
                _occupation = value;
                _error = null;
              }),
            ),
          ),
      ];

  List<Widget> _details() {
    final school = _occupation == 'school';
    final uni = _occupation == 'university';
    return [
      _heading(
        school ? 'Your school' : uni ? 'Your course' : 'Where you are',
        'Anywhere in the world -- type it the way you\'d say it.',
      ),
      Autocomplete<String>(
        key: const ValueKey('profile-country'),
        initialValue: TextEditingValue(text: _country.text),
        optionsBuilder: (value) {
          final q = value.text.trim().toLowerCase();
          if (q.isEmpty) return countries;
          return countries.where((c) => c.toLowerCase().startsWith(q));
        },
        onSelected: (value) => setState(() => _country.text = value),
        fieldViewBuilder: (context, controller, focus, onSubmit) => TextField(
          key: const ValueKey('profile-country-field'),
          controller: controller,
          focusNode: focus,
          onChanged: (v) => setState(() => _country.text = v),
          style: sans(15),
          decoration: paperInput('Country', label: 'Country'),
        ),
      ),
      const SizedBox(height: 12),
      if (_isIndia)
        _Picker(
          key: const ValueKey('profile-state'),
          label: 'State or union territory',
          value: _state,
          items: indianStates,
          onChanged: (v) => setState(() => _state = v),
        )
      else
        TextField(
          key: const ValueKey('profile-region'),
          controller: _region,
          textCapitalization: TextCapitalization.words,
          style: sans(15),
          decoration: paperInput('e.g. Texas, Ontario, Dubai', label: 'State, province or region'),
        ),
      const SizedBox(height: 12),
      if (school) ...[
        if (_isIndia) ...[
          _Picker(
            key: const ValueKey('profile-board'),
            label: 'Board',
            value: _board,
            items: indianSchoolBoards,
            onChanged: (v) => setState(() => _board = v),
          ),
          const SizedBox(height: 12),
          _Picker(
            key: const ValueKey('profile-class'),
            label: 'Class',
            value: _classLevel,
            items: _classes,
            onChanged: (v) => setState(() => _classLevel = v),
          ),
        ] else ...[
          TextField(
            key: const ValueKey('profile-curriculum'),
            controller: _curriculum,
            style: sans(15),
            decoration: paperInput('e.g. GCSE, IB, US high school, Common Core',
                label: 'Curriculum or board (if you know it)'),
          ),
          const SizedBox(height: 12),
          TextField(
            key: const ValueKey('profile-level'),
            controller: _level,
            style: sans(15),
            decoration: paperInput('e.g. Grade 8, Year 11', label: 'Grade or year'),
          ),
        ],
        const SizedBox(height: 12),
        TextField(
          key: const ValueKey('profile-institution'),
          controller: _institution,
          textCapitalization: TextCapitalization.words,
          style: sans(15),
          decoration: paperInput('Optional', label: 'School name'),
        ),
      ],
      if (uni) ...[
        TextField(
          key: const ValueKey('profile-institution'),
          controller: _institution,
          textCapitalization: TextCapitalization.words,
          style: sans(15),
          decoration: paperInput('e.g. Anna University, IIT Madras, University of Toronto',
              label: 'College or university'),
        ),
        const SizedBox(height: 12),
        TextField(
          key: const ValueKey('profile-course'),
          controller: _course,
          style: sans(15),
          decoration: paperInput('e.g. B.Tech Computer Science, MBBS, BA Economics', label: 'What are you studying?'),
        ),
        const SizedBox(height: 12),
        _Picker(
          key: const ValueKey('profile-year'),
          label: 'Year',
          value: _year,
          items: universityYears,
          onChanged: (v) => setState(() => _year = v),
        ),
      ],
      if (_occupation == 'working')
        TextField(
          key: const ValueKey('profile-course'),
          controller: _course,
          style: sans(15),
          decoration: paperInput('e.g. Software engineer, nurse, accountant', label: 'What do you do?'),
        ),
      if (_occupation == 'other')
        TextField(
          key: const ValueKey('profile-course'),
          controller: _course,
          maxLines: 2,
          style: sans(15),
          decoration: paperInput('e.g. Preparing for NEET after Class 12', label: 'Tell Versa a little (optional)'),
        ),
    ];
  }

  List<Widget> _goalsAndConsent() => [
        _heading('What are you working towards?', 'Optional -- but it helps Versa aim.'),
        TextField(
          key: const ValueKey('profile-subjects'),
          controller: _subjects,
          maxLines: 2,
          style: sans(15),
          decoration: paperInput('e.g. Physics, Chemistry, Maths', label: 'Subjects you\'re studying'),
        ),
        const SizedBox(height: 12),
        TextField(
          key: const ValueKey('profile-goals'),
          controller: _goals,
          maxLines: 3,
          style: sans(15),
          decoration: paperInput('e.g. JEE Main in January, my semester exams, learning to code',
              label: 'Exams or goals'),
        ),
        const SizedBox(height: 18),
        _ConsentBox(
          key: const ValueKey('consent-data'),
          value: _consentData,
          onChanged: (v) => setState(() => _consentData = v),
          text: 'I agree that Versa keeps what I tell it and what I learn here, to shape how it teaches me. '
              'Nothing is sold or shown to anyone else.',
        ),
        if (_minor)
          _ConsentBox(
            key: const ValueKey('consent-guardian'),
            value: _consentGuardian,
            onChanged: (v) => setState(() => _consentGuardian = v),
            text: 'I\'m under $kAdultAge, and my parent or guardian knows I\'m using Versa and agrees to this.',
          ),
      ];
}

class _ChoiceCard extends StatelessWidget {
  const _ChoiceCard({
    super.key,
    required this.selected,
    required this.icon,
    required this.label,
    required this.detail,
    required this.onTap,
  });

  final bool selected;
  final IconData icon;
  final String label;
  final String detail;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Material(
        color: selected ? Paper.accentSoft : Paper.card,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: BorderSide(color: selected ? Paper.accent : Paper.borderStrong, width: selected ? 1.5 : 1),
        ),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Row(children: [
              Icon(icon, color: selected ? Paper.accent : Paper.muted),
              const SizedBox(width: 14),
              Expanded(
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text(label, style: sans(15, weight: FontWeight.w600)),
                  const SizedBox(height: 2),
                  Text(detail, style: sans(13, color: Paper.muted)),
                ]),
              ),
              if (selected) const Icon(Icons.check_circle, color: Paper.accent, size: 20),
            ]),
          ),
        ),
      );
}

/// A field that opens a bottom sheet to pick from -- searchable when the
/// list is long (36 states and territories), thumb-friendly on a phone.
class _Picker extends StatelessWidget {
  const _Picker({super.key, required this.label, required this.value, required this.items, required this.onChanged});

  final String label;
  final String? value;
  final List<String> items;
  final ValueChanged<String?> onChanged;

  Future<void> _open(BuildContext context) async {
    final picked = await showModalBottomSheet<String>(
      context: context,
      isScrollControlled: true,
      backgroundColor: Paper.surface,
      showDragHandle: true,
      builder: (context) => _PickerSheet(label: label, items: items, selected: value),
    );
    if (picked != null) onChanged(picked);
  }

  @override
  Widget build(BuildContext context) => InkWell(
        onTap: () => _open(context),
        borderRadius: BorderRadius.circular(10),
        child: InputDecorator(
          decoration: paperInput('', label: label, suffix: const Icon(Icons.expand_more, color: Paper.muted)),
          isEmpty: value == null,
          child: Text(value ?? '', style: sans(15)),
        ),
      );
}

class _PickerSheet extends StatefulWidget {
  const _PickerSheet({required this.label, required this.items, required this.selected});

  final String label;
  final List<String> items;
  final String? selected;

  @override
  State<_PickerSheet> createState() => _PickerSheetState();
}

class _PickerSheetState extends State<_PickerSheet> {
  String _query = '';

  @override
  Widget build(BuildContext context) {
    final searchable = widget.items.length > 8;
    final q = _query.trim().toLowerCase();
    final shown = q.isEmpty ? widget.items : widget.items.where((i) => i.toLowerCase().contains(q)).toList();
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(bottom: MediaQuery.viewInsetsOf(context).bottom),
        child: ConstrainedBox(
          constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.75),
          child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
              child: Text(widget.label, style: serif(20)),
            ),
            if (searchable)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
                child: TextField(
                  key: const ValueKey('picker-search'),
                  autofocus: false,
                  onChanged: (v) => setState(() => _query = v),
                  style: sans(15),
                  decoration: paperInput('Search', suffix: const Icon(Icons.search, color: Paper.muted)),
                ),
              ),
            Flexible(
              child: ListView(
                shrinkWrap: true,
                children: [
                  for (final item in shown)
                    ListTile(
                      key: ValueKey('pick-$item'),
                      title: Text(item, style: sans(15)),
                      trailing: item == widget.selected ? const Icon(Icons.check, color: Paper.accent) : null,
                      onTap: () => Navigator.of(context).pop(item),
                    ),
                ],
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

class _ConsentBox extends StatelessWidget {
  const _ConsentBox({super.key, required this.value, required this.onChanged, required this.text});

  final bool value;
  final ValueChanged<bool> onChanged;
  final String text;

  @override
  Widget build(BuildContext context) => InkWell(
        onTap: () => onChanged(!value),
        borderRadius: BorderRadius.circular(10),
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Checkbox(
              value: value,
              onChanged: (v) => onChanged(v ?? false),
              activeColor: Paper.accent,
              visualDensity: VisualDensity.compact,
            ),
            const SizedBox(width: 4),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.only(top: 10),
                child: Text(text, style: sans(13.5, color: Paper.body, height: 1.45)),
              ),
            ),
          ]),
        ),
      );
}
