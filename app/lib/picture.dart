import 'dart:convert';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:image_picker/image_picker.dart';

import 'api.dart';
import 'theme.dart';

/// A picture attached to a message (server: images.py). It is read once on
/// upload; [reading] is what it shows, in words (maths as LaTeX), and is what
/// the tutor -- and the slime -- are given. The app keeps [bytes] to show it.
class AttachedPicture {
  const AttachedPicture({required this.id, required this.reading, required this.bytes, this.name = ''});

  final String id;
  final String reading;
  final Uint8List bytes;
  final String name;
}

typedef PickedPicture = ({String name, Uint8List bytes});

/// Opens the platform's picker for one picture. Replaceable so tests never
/// open a real dialog.
Future<PickedPicture?> Function() picturePicker = () async {
  final file = await FilePicker.pickFile(type: FileType.image);
  if (file == null) return null;
  return (name: file.name, bytes: await file.readAsBytes());
};

/// Opens the phone's camera for one photo. Scaled down on the phone (a
/// photo of a page reads fine at 2000 px and uploads far faster).
/// Replaceable so tests never open a real camera.
Future<PickedPicture?> Function() cameraPicker = () async {
  final shot = await ImagePicker().pickImage(source: ImageSource.camera, maxWidth: 2000, imageQuality: 85);
  if (shot == null) return null;
  return (name: shot.name, bytes: await shot.readAsBytes());
};

/// Whether this device can take a photo: the Android and iOS app only (a
/// browser or a desktop picks a file instead). Replaceable in tests.
bool Function() canTakePhoto =
    () => !kIsWeb && (defaultTargetPlatform == TargetPlatform.android || defaultTargetPlatform == TargetPlatform.iOS);

/// The picture button: on a phone, take a photo or choose one; elsewhere,
/// choose a file straight away.
Future<PickedPicture?> choosePicture(BuildContext context) async {
  if (!canTakePhoto()) return picturePicker();
  final camera = await showModalBottomSheet<bool>(
    context: context,
    backgroundColor: Paper.sliver,
    shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
    builder: (sheet) => SafeArea(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          ListTile(
            key: const ValueKey('picture-camera'),
            leading: Icon(Icons.photo_camera_outlined, color: Paper.accent),
            title: Text('Take a photo', style: sans(15)),
            onTap: () => Navigator.of(sheet).pop(true),
          ),
          ListTile(
            key: const ValueKey('picture-gallery'),
            leading: Icon(Icons.photo_library_outlined, color: Paper.accent),
            title: Text('Choose a picture', style: sans(15)),
            onTap: () => Navigator.of(sheet).pop(false),
          ),
        ]),
      ),
    ),
  );
  if (camera == null) return null;
  return camera ? cameraPicker() : picturePicker();
}

/// Uploads [bytes] for [learnerId]; the server reads it before answering.
typedef PictureUploader = Future<AttachedPicture> Function(Uint8List bytes, String name);

extension PictureApi on VersaApi {
  /// [asResource]: the picture is what a course, an exam or a room will be
  /// built from (a page, a syllabus, notes), so the server writes all of it
  /// down, not the short reading a message gets.
  Future<AttachedPicture> uploadPicture(String learnerId, Uint8List bytes, String name,
      {bool asResource = false}) async {
    final request = http.MultipartRequest('POST', Uri.parse('$baseUrl/api/images'))
      ..fields['learner_id'] = learnerId
      ..files.add(http.MultipartFile.fromBytes('file', bytes, filename: name));
    if (asResource) request.fields['purpose'] = 'resource';
    final streamed = await httpClient.send(request).timeout(Duration(seconds: asResource ? 120 : 60));
    final r = await http.Response.fromStream(streamed);
    if (r.statusCode != 200) {
      var detail = 'could not read that picture';
      try {
        detail = (jsonDecode(r.body) as Map<String, dynamic>)['detail'] as String? ?? detail;
      } catch (_) {}
      throw ApiException(detail);
    }
    final j = jsonDecode(r.body) as Map<String, dynamic>;
    return AttachedPicture(id: j['id'] as String, reading: j['reading'] as String, bytes: bytes, name: name);
  }
}

/// The same shape the server gives a turn (images.py `with_image`): the
/// words, then what the picture shows. Used where the app sends the reading
/// itself -- a room message, an exam answer.
String withPicture(String text, String reading) {
  final words = text.trim().isEmpty ? '(They sent only this picture: help with what it shows.)' : text.trim();
  return '$words\n\n[Attached picture -- what it shows: ${reading.trim()}]';
}

final _attached = RegExp(r'\n*\[Attached picture -- what it shows: ([\s\S]*)\]\s*$');

/// A message written by [withPicture] (or kept by the server that way) ->
/// the words, and the picture's reading if there is one -- so a chat loaded
/// from history shows a picture chip, not the bracketed text.
(String, String?) splitPicture(String text) {
  final m = _attached.firstMatch(text);
  if (m == null) return (text, null);
  var words = text.substring(0, m.start);
  if (words.startsWith('(They sent only this picture')) words = '';
  return (words, m.group(1));
}
