import 'package:test/test.dart';

import 'support.dart';

int nestingOf(String body) => only('void f(int a, bool b) {\n$body\n}').nesting;

void main() {
  group('statements', _statements);
  group('chains and blocks', _chains);
  group('nested functions', _nested);
}

void _statements() {
  test('a flat body is zero', () {
    expect(nestingOf('print(a);'), 0);
  });
  test('each nesting statement opens a level', () {
    expect(nestingOf('if (b) { print(a); }'), 1);
    expect(nestingOf('for (;;) { while (b) { do {} while (b); } }'), 3);
    expect(
      nestingOf('for (final x in [a]) { switch (x) { case 1: if (b) {} } }'),
      3,
    );
  });
  test('catch and finally blocks are at the level of their try', () {
    const body = '''
try {
  if (b) {}
} catch (error) {
  if (b) {}
} finally {
  if (b) {}
}''';
    expect(nestingOf(body), 2);
  });
}

void _chains() {
  test('an else-if chain stays at one level', () {
    expect(
      nestingOf('if (b) {} else if (!b) {} else if (a > 0) {} else {}'),
      1,
    );
  });
  test('an if inside an else block opens a level', () {
    expect(nestingOf('if (b) {} else { if (!b) {} }'), 2);
  });
  test('an if in the then branch of an if opens a level', () {
    expect(nestingOf('if (b) if (!b) print(a);'), 2);
  });
  test('blocks, labels, and expressions add nothing', () {
    expect(nestingOf('{ { print(a); } }'), 0);
    expect(nestingOf('outer: for (;;) { break outer; }'), 1);
    expect(nestingOf('print([if (b) 1]); print(switch (a) { _ => 0 });'), 0);
  });
}

void _nested() {
  test('a nested function starts at zero', () {
    final found = measured(
      'void f(bool b) { if (b) { void g() { if (b) {} } g(); } }',
    );
    expect(found['f']!.nesting, 1);
    expect(found['f.g']!.nesting, 1);
  });
  test('a closure starts at zero', () {
    final found = measured(
      'void f(bool b) { if (b) { print(() { while (b) {} }); } }',
    );
    expect(found['f']!.nesting, 1);
    expect(found['f.<anonymous>']!.nesting, 1);
  });
}
