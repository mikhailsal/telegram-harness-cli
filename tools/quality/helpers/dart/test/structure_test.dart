import 'dart:io';

import 'package:quality_dart_helper/imports.dart';
import 'package:quality_dart_helper/structure.dart';
import 'package:test/test.dart';

late Directory scratch;

Package get package => (name: 'pkg', root: scratch.path);

String write(String name, String content) {
  final file = File('${scratch.path}/$name')..writeAsStringSync(content);
  return file.path;
}

void main() {
  setUp(() => scratch = Directory.systemTemp.createTempSync('dart-helper-'));
  tearDown(() => scratch.deleteSync(recursive: true));
  group('failures', _failures);
  group('files', _files);
}

void _failures() {
  test('an unreadable file reports an error without a line', () {
    final missing = '${scratch.path}/missing.dart';
    final result = measure(missing, package);
    expect(result, {
      'file': missing,
      'part': false,
      'functions': isEmpty,
      'imports': isEmpty,
      'error': {'line': null, 'message': startsWith('cannot read $missing')},
    });
  });
  test('a file that does not parse reports its first error', () {
    final broken = write(
      'broken.dart',
      'void f() {}\n\nint x = ;\nvoid g() {}\n',
    );
    final error = measure(broken, package)['error'] as Map<String, Object?>;
    expect(error['line'], 3);
    expect(error['message'], isNotEmpty);
    expect(measure(broken, package)['functions'], isEmpty);
  });
  test('a part that does not parse is still reported as a part', () {
    final broken = write('part.dart', "part of 'a.dart';\nvoid g( {\n");
    expect(measure(broken, package)['part'], isTrue);
  });
}

void _files() {
  test('a part reports its functions and no references', () {
    final part = write('part.dart', "part of 'a.dart';\nvoid g() {}\n");
    final result = measure(part, package);
    expect(result['part'], isTrue);
    expect(result['imports'], isEmpty);
    expect((result['functions'] as List<Object?>).length, 1);
    expect(result['error'], isNull);
  });
  test('the response lists the files in request order', () {
    final a = write('a.dart', 'void a() {}\n');
    final b = write('b.dart', 'void b() {}\n');
    final response = structure((package: package, files: [b, a, b]));
    expect(response['protocol'], 1);
    expect(
      [
        for (final file in response['files'] as List<Object?>)
          (file as Map<String, Object?>)['file'],
      ],
      [b, a, b],
    );
  });
}
