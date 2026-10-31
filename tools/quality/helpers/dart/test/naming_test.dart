import 'package:test/test.dart';

import 'support.dart';

void main() {
  group('declarations', _declarations);
  group('closures', _closures);
  group('scopes', _scopes);
  group('lines and parameters', _linesAndParameters);
  group('skipped declarations', _skipped);
}

void _declarations() {
  test('functions, accessors, and operators are named', () {
    const source = '''
int f() => 1;
int get g => 1;
set g(int value) {}
class C {
  void m() {}
  int get v => 1;
  set v(int value) {}
  C operator +(C other) => this;
  int operator [](int index) => index;
}''';
    expect(measured(source).keys, [
      'f',
      'get g',
      'set g',
      'C.m',
      'C.get v',
      'C.set v',
      'C.operator +',
      'C.operator []',
    ]);
  });
  test('the unnamed constructor is new, others take their name', () {
    const source = '''
class C {
  C() { print(1); }
  C.named() { print(2); }
  factory C.make() => C();
}''';
    expect(measured(source).keys, ['C.new', 'C.named', 'C.make']);
  });
}

void _closures() {
  test('a closure takes the name of what it initializes or is passed as', () {
    const source = '''
final top = () {};
class C {
  final field = () {};
  void m(void Function() cb) {
    final local = () {};
    m(() {});
    call(onTap: () {});
  }
  void call({void Function()? onTap}) {}
}''';
    expect(measured(source).keys, [
      'top',
      'C.field',
      'C.m',
      'C.m.local',
      'C.m.<anonymous>',
      'C.m.onTap',
      'C.call',
    ]);
  });
  test('a closure takes the last name of its assignment target', () {
    const source = '''
class C {
  void Function()? handler;
  void m(C other, List<void Function()> all) {
    handler = () {};
    this.handler = () {};
    other.handler = () {};
    all[0] = () {};
  }
}''';
    expect(measuredList(source).map((f) => f.name), [
      'C.m',
      'C.m.handler',
      'C.m.handler',
      'C.m.handler',
      'C.m.<anonymous>',
    ]);
  });
}

void _scopes() {
  test('types and extensions qualify their members', () {
    const source = '''
mixin M { void a() {} }
enum E { one; void b() {} }
extension type T(int value) { void c() {} }
extension X on int { void d() {} }
extension on String { void e() {} }''';
    expect(measured(source).keys, [
      'M.a',
      'E.b',
      'T.c',
      'X.d',
      '<anonymous>.e',
    ]);
  });
  test('functions qualify what they contain', () {
    const source = 'void f() { void g() { final h = () {}; h(); } g(); }';
    expect(measured(source).keys, ['f', 'f.g', 'f.g.h']);
  });
}

void _linesAndParameters() {
  test('a declaration starts after its doc comment and annotations', () {
    const source = '''
/// Doc.
@Deprecated('x')
void f() {}
class C {
  /// Doc.
  @override
  String toString() => '';
}
final g =
    () {};''';
    final found = measured(source);
    expect(found['f']!.line, 3);
    expect(found['C.toString']!.line, 7);
    expect(found['g']!.line, 10);
  });
  test('every formal parameter counts once, type parameters do not', () {
    expect(only('void f<T>(int a, [int b = 0]) {}').parameters, 2);
    expect(only('void f(int a, {required int b, int c = 0}) {}').parameters, 3);
    expect(only('void f(void g(int x), int Function(int) h) {}').parameters, 2);
  });
  test('initializing formals count and getters have none', () {
    const source = '''
class B { B({int? key}); }
class C extends B {
  C(this.a, {super.key}) : assert(a > 0);
  final int a;
  int get v => a;
  set v(int value) {}
}''';
    expect(only(source, 'C.new').parameters, 2);
    expect(only(source, 'C.get v').parameters, 0);
    expect(only(source, 'C.set v').parameters, 1);
  });
}

void _skipped() {
  test('declarations without a body are not measured', () {
    const source = '''
abstract class A {
  void m();
  external void e();
  A(this.x);
  factory A.other() = B;
  final int x;
}
class B implements A {
  B();
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}
external int f();''';
    expect(measured(source).keys, ['B.noSuchMethod']);
  });
}
