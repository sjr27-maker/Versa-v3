import 'dart:math' as math;
import 'dart:ui';

/// The stage's 3D solids, drawn on the 2D canvas: each is a small mesh in
/// unit coordinates (y up, centred on the origin), turned by yaw/pitch, seen
/// from a camera tilted a little from above, lit from the upper left, and
/// painted back to front. Sphere-like things (sphere, planet, atom) are
/// shaded discs instead -- rounder and cheaper than a mesh.
class Vec3 {
  const Vec3(this.x, this.y, this.z);
  final double x, y, z;
  Vec3 operator +(Vec3 o) => Vec3(x + o.x, y + o.y, z + o.z);
  Vec3 operator -(Vec3 o) => Vec3(x - o.x, y - o.y, z - o.z);
  Vec3 operator *(double k) => Vec3(x * k, y * k, z * k);
  double dot(Vec3 o) => x * o.x + y * o.y + z * o.z;
  Vec3 cross(Vec3 o) => Vec3(y * o.z - z * o.y, z * o.x - x * o.z, x * o.y - y * o.x);
  double get length => math.sqrt(x * x + y * y + z * z);
  Vec3 get unit => length == 0 ? this : this * (1 / length);
}

class Mesh {
  const Mesh(this.vertices, this.faces);
  final List<Vec3> vertices;
  final List<List<int>> faces; // counter-clockwise seen from outside
}

enum SolidKind { cube, sphere, cylinder, cone, pyramid, prism, torus, planet, atom }

SolidKind? solidKindFromName(String? name) => SolidKind.values.where((k) => k.name == name).firstOrNull;

/// Seen from slightly above, so tops show.
const cameraTilt = 0.42;
final Vec3 _light = const Vec3(-0.45, 0.75, 0.55).unit;

Mesh? meshFor(SolidKind kind) => switch (kind) {
      SolidKind.cube => _cube,
      SolidKind.pyramid => _pyramid,
      SolidKind.prism => _prism,
      SolidKind.cylinder => _cylinder,
      SolidKind.cone => _cone,
      SolidKind.torus => _torus,
      _ => null,
    };

final Mesh _cube = Mesh(const [
  Vec3(-.5, -.5, -.5), Vec3(.5, -.5, -.5), Vec3(.5, .5, -.5), Vec3(-.5, .5, -.5), //
  Vec3(-.5, -.5, .5), Vec3(.5, -.5, .5), Vec3(.5, .5, .5), Vec3(-.5, .5, .5),
], const [
  [4, 5, 6, 7], [1, 0, 3, 2], [5, 1, 2, 6], [0, 4, 7, 3], [7, 6, 2, 3], [0, 1, 5, 4],
]);

final Mesh _pyramid = Mesh(const [
  Vec3(-.5, -.5, -.5), Vec3(.5, -.5, -.5), Vec3(.5, -.5, .5), Vec3(-.5, -.5, .5), Vec3(0, .5, 0),
], const [
  [3, 2, 4], [2, 1, 4], [1, 0, 4], [0, 3, 4], [0, 1, 2, 3],
]);

final Mesh _prism = Mesh(const [
  Vec3(-.5, -.5, -.5), Vec3(.5, -.5, -.5), Vec3(0, .5, -.5), //
  Vec3(-.5, -.5, .5), Vec3(.5, -.5, .5), Vec3(0, .5, .5),
], const [
  [3, 4, 5], [1, 0, 2], [4, 1, 2, 5], [0, 3, 5, 2], [0, 1, 4, 3],
]);

Mesh _ring(int n, {required double top, required double rTop, required double rBottom, bool apex = false}) {
  final v = <Vec3>[];
  for (var i = 0; i < n; i++) {
    final a = 2 * math.pi * i / n;
    v.add(Vec3(math.cos(a) * rBottom, -.5, math.sin(a) * rBottom));
  }
  if (apex) {
    v.add(Vec3(0, top, 0));
  } else {
    for (var i = 0; i < n; i++) {
      final a = 2 * math.pi * i / n;
      v.add(Vec3(math.cos(a) * rTop, top, math.sin(a) * rTop));
    }
  }
  final f = <List<int>>[];
  for (var i = 0; i < n; i++) {
    final j = (i + 1) % n;
    f.add(apex ? [j, i, n] : [j, i, n + i, n + j]);
  }
  f.add([for (var i = 0; i < n; i++) i]); // bottom
  if (!apex) f.add([for (var i = n - 1; i >= 0; i--) n + i]); // top
  return Mesh(v, f);
}

final Mesh _cylinder = _ring(20, top: .5, rTop: .45, rBottom: .45);
final Mesh _cone = _ring(20, top: .5, rTop: 0, rBottom: .5, apex: true);

final Mesh _torus = () {
  const nu = 20, nv = 10, big = .34, small = .15;
  final v = <Vec3>[];
  for (var i = 0; i < nu; i++) {
    final u = 2 * math.pi * i / nu;
    for (var j = 0; j < nv; j++) {
      final w = 2 * math.pi * j / nv;
      final r = big + small * math.cos(w);
      v.add(Vec3(r * math.cos(u), small * math.sin(w), r * math.sin(u)));
    }
  }
  final f = <List<int>>[];
  for (var i = 0; i < nu; i++) {
    for (var j = 0; j < nv; j++) {
      final a = i * nv + j, b = ((i + 1) % nu) * nv + j;
      final c = ((i + 1) % nu) * nv + (j + 1) % nv, d = i * nv + (j + 1) % nv;
      f.add([a, d, c, b]);
    }
  }
  return Mesh(v, f);
}();

/// Rotate a point by yaw (around y), then pitch (around x), then the camera's tilt.
Vec3 turnPoint(Vec3 p, double yaw, double pitch) {
  var cy = math.cos(yaw), sy = math.sin(yaw);
  var q = Vec3(p.x * cy + p.z * sy, p.y, -p.x * sy + p.z * cy);
  for (final a in [pitch, cameraTilt]) {
    final ca = math.cos(a), sa = math.sin(a);
    q = Vec3(q.x, q.y * ca - q.z * sa, q.y * sa + q.z * ca);
  }
  return q;
}

Color _shade(Color base, double k, double alpha) => Color.fromARGB(
      (alpha * 255).round().clamp(0, 255),
      (base.r * 255 * k).round().clamp(0, 255),
      (base.g * 255 * k).round().clamp(0, 255),
      (base.b * 255 * k).round().clamp(0, 255),
    );

/// Paint one solid centred at [c] (its middle, in pixels), [size] pixels tall.
void paintSolid(
  Canvas canvas,
  Offset c,
  double size,
  SolidKind kind, {
  required double yaw,
  required double pitch,
  required Color color,
  required double time,
  double alpha = 1,
}) {
  switch (kind) {
    case SolidKind.sphere:
      _sphere(canvas, c, size / 2, color, alpha, yaw);
    case SolidKind.planet:
      _planet(canvas, c, size / 2, color, alpha, yaw);
    case SolidKind.atom:
      _atom(canvas, c, size / 2, color, alpha, time);
    default:
      final mesh = meshFor(kind);
      if (mesh == null) return;
      final pts = [for (final v in mesh.vertices) turnPoint(v, yaw, pitch)];
      final faces = <(double, Path, Color)>[];
      for (final face in mesh.faces) {
        final a = pts[face[0]], b = pts[face[1]], d = pts[face[2]];
        final n = (b - a).cross(d - a).unit;
        if (n.z <= 0) continue; // facing away from the camera
        final light = n.dot(_light).clamp(0.0, 1.0);
        final path = Path();
        for (var i = 0; i < face.length; i++) {
          final q = pts[face[i]];
          final o = c + Offset(q.x * size, -q.y * size);
          i == 0 ? path.moveTo(o.dx, o.dy) : path.lineTo(o.dx, o.dy);
        }
        path.close();
        final depth = face.map((i) => pts[i].z).reduce((x, y) => x + y) / face.length;
        faces.add((depth, path, _shade(color, 0.45 + 0.65 * light, alpha)));
      }
      faces.sort((x, y) => x.$1.compareTo(y.$1)); // far first
      final edge = Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..strokeJoin = StrokeJoin.round
        ..color = _shade(color, 0.35, alpha * 0.5);
      for (final (_, path, fill) in faces) {
        canvas.drawPath(path, Paint()..color = fill);
        canvas.drawPath(path, edge);
      }
  }
}

void _sphere(Canvas canvas, Offset c, double r, Color color, double alpha, double yaw) {
  final rect = Rect.fromCircle(center: c, radius: r);
  canvas.drawCircle(
    c,
    r,
    Paint()
      ..shader = Gradient.radial(
        c + Offset(-r * 0.35, -r * 0.4),
        r * 1.35,
        [_shade(color, 1.35, alpha), _shade(color, 0.95, alpha), _shade(color, 0.45, alpha)],
        const [0, 0.55, 1],
      ),
  );
  // two meridians that travel with the spin, so a turning sphere looks turned
  final line = Paint()
    ..style = PaintingStyle.stroke
    ..strokeWidth = 1
    ..color = _shade(color, 0.6, alpha * 0.45);
  for (final k in [0.0, 0.5]) {
    final w = math.cos(2 * math.pi * (yaw / (2 * math.pi) + k)).abs() * r;
    canvas.drawOval(Rect.fromCenter(center: c, width: w * 2, height: r * 2), line);
  }
  canvas.drawOval(Rect.fromCenter(center: c, width: r * 2, height: r * 0.5), line);
  canvas.drawCircle(c + Offset(-r * 0.38, -r * 0.42), r * 0.16,
      Paint()..color = Color.fromRGBO(255, 255, 255, 0.45 * alpha));
  canvas.drawOval(rect, Paint()
    ..style = PaintingStyle.stroke
    ..strokeWidth = 1
    ..color = _shade(color, 0.4, alpha * 0.6));
}

void _planet(Canvas canvas, Offset c, double r, Color color, double alpha, double yaw) {
  final ring = Rect.fromCenter(center: c, width: r * 3.4, height: r * 0.95);
  final paint = Paint()
    ..style = PaintingStyle.stroke
    ..strokeWidth = r * 0.16
    ..color = Color.fromRGBO(226, 179, 60, 0.85 * alpha);
  canvas.drawArc(ring, math.pi, math.pi, false, paint); // back half, behind
  _sphere(canvas, c, r, color, alpha, yaw);
  canvas.drawArc(ring, 0, math.pi, false, paint); // front half, over the planet
}

void _atom(Canvas canvas, Offset c, double r, Color color, double alpha, double time) {
  final orbit = Paint()
    ..style = PaintingStyle.stroke
    ..strokeWidth = 1.4
    ..color = _shade(color, 0.8, alpha * 0.7);
  const tilts = [0.0, math.pi / 3, -math.pi / 3];
  final electrons = <(double, Offset)>[];
  for (final (i, tilt) in tilts.indexed) {
    canvas.save();
    canvas.translate(c.dx, c.dy);
    canvas.rotate(tilt);
    canvas.drawOval(Rect.fromCenter(center: Offset.zero, width: r * 2, height: r * 0.7), orbit);
    canvas.restore();
    final a = time * (1.6 + i * 0.4) + i * 2.1;
    final local = Offset(math.cos(a) * r, math.sin(a) * r * 0.35);
    final rotated = Offset(local.dx * math.cos(tilt) - local.dy * math.sin(tilt),
        local.dx * math.sin(tilt) + local.dy * math.cos(tilt));
    electrons.add((math.sin(a), c + rotated));
  }
  for (final (depth, o) in electrons) {
    if (depth < 0) canvas.drawCircle(o, r * 0.1, Paint()..color = Color.fromRGBO(79, 124, 172, 0.7 * alpha));
  }
  _sphere(canvas, c, r * 0.28, color, alpha, 0);
  for (final (depth, o) in electrons) {
    if (depth >= 0) canvas.drawCircle(o, r * 0.1, Paint()..color = Color.fromRGBO(79, 124, 172, alpha));
  }
}
