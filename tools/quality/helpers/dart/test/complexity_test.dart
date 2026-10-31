import 'package:test/test.dart';

import 'support.dart';

int complexityOf(String body) =>
    only('void f(int a, bool b) {\n$body\n}').complexity;

void main() {
  group('statements', _statements);
  group('expressions', _expressions);
  group('switches', _switches);
  group('nested functions', _nested);
}

void _statements() {
  test('a straight body is one', () {
    expect(complexityOf('print(a);'), 1);
  });
  test('each statement decision adds one', () {
    expect(complexityOf('if (b) {} else {}'), 2);
    expect(complexityOf('if (a case 1) {}'), 2);
    expect(complexityOf('if (b) {} else if (!b) {} else {}'), 3);
    expect(complexityOf('for (var i = 0; i < a; i++) {}'), 2);
    expect(complexityOf('for (final x in [a]) { print(x); }'), 2);
    expect(complexityOf('while (b) {}'), 2);
    expect(complexityOf('do {} while (b);'), 2);
  });
  test('each catch clause adds one, try and finally nothing', () {
    const body = '''
try {
  print(a);
} on StateError {
  print(b);
} catch (error) {
  print(error);
} finally {
  print(a);
}''';
    expect(complexityOf(body), 3);
  });
  test('await for counts like any loop', () {
    const source =
        'Future<void> f(Stream<int> s) async { await for (final x in s) { print(x); } }';
    expect(only(source).complexity, 2);
  });
}

void _expressions() {
  test('conditional expressions and logical operators add one each', () {
    expect(complexityOf('print(b ? a : 0);'), 2);
    expect(complexityOf('print(b && a > 0 || !b);'), 3);
    expect(complexityOf('int? n; print(n ?? a);'), 2);
    expect(complexityOf('int? n; n ??= a; n += 1;'), 2);
  });
  test('collection if and for elements add one each', () {
    expect(complexityOf('print([if (b) 1, for (final x in [a]) x]);'), 3);
  });
  test('logical patterns add one each', () {
    expect(complexityOf('if (a case 1 || 2) {}'), 3);
    expect(complexityOf('if (a case > 0 && < 9) {}'), 3);
  });
  test('null-aware access, assertions, and other operators add nothing', () {
    expect(
      complexityOf('String? s; print(s?.length); assert(b); print(a + 1);'),
      1,
    );
  });
}

void _switches() {
  test('each case adds one and default nothing', () {
    expect(
      complexityOf('switch (a) { case 1: case 2: print(a); default: }'),
      3,
    );
  });
  test('a final wildcard or untyped variable case is irrefutable', () {
    expect(complexityOf('switch (a) { case 1: print(a); case _: }'), 2);
    expect(
      complexityOf('switch (a) { case 1: print(a); case var x: print(x); }'),
      2,
    );
    expect(complexityOf('switch (a) { case 1: print(a); case final _: }'), 2);
  });
  test('a typed, guarded, or earlier catch-all case still counts', () {
    expect(
      complexityOf('switch (a) { case 1: print(a); case int x: print(x); }'),
      3,
    );
    expect(complexityOf('switch (a) { case 1: print(a); case _ when b: }'), 3);
    expect(complexityOf('switch (a) { case _: print(a); case 1: }'), 3);
  });
  test('switch expression arms follow the same rules', () {
    expect(complexityOf('print(switch (a) { 1 => 0, 2 => 1, _ => 2 });'), 3);
    expect(complexityOf('print(switch (a) { 1 => 0, var x => x });'), 2);
    expect(complexityOf('print(switch (a) { 1 => 0, int x => x });'), 3);
  });
  test('cases of language versions before patterns count too', () {
    const source =
        '// @dart=2.19\nvoid f(int a) { switch (a) { case 1: break; default: } }';
    expect(only(source).complexity, 2);
  });
}

void _nested() {
  test('decisions of a closure belong to the closure', () {
    final found = measured(
      'void f(bool b) { if (b) {} final g = () { if (b) {} if (!b) {} }; g(); }',
    );
    expect(found['f']!.complexity, 2);
    expect(found['f.g']!.complexity, 3);
  });
  test('decisions of a local function belong to it', () {
    final found = measured(
      'void f(bool b) { int g() => b ? 1 : 0; print(g()); }',
    );
    expect(found['f']!.complexity, 1);
    expect(found['f.g']!.complexity, 2);
  });
  test('constructor initializers count with the constructor', () {
    const source =
        'class C { C(int a) : b = a > 0 ? a : 0, c = a; final int b; final int c; }';
    expect(only(source, 'C.new').complexity, 2);
  });
}
