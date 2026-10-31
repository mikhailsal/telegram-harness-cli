import 'package:test/test.dart';

import 'support.dart';

void main() {
  group('block bodies', _blocks);
  group('expression bodies', _expressions);
  group('constructors', _constructors);
}

void _blocks() {
  test('lines between the braces count, the braces do not', () {
    expect(only('void f() {\n  print(1);\n  print(2);\n}').codeLines, 2);
    expect(only('void f() {}').codeLines, 0);
    expect(only('void f() {\n}').codeLines, 0);
  });
  test('comment-only and blank lines do not count', () {
    const source = '''
void f() {
  // a comment

  /* a block
     comment */
  print(1); // trailing
  /// doc
}''';
    expect(only(source).codeLines, 1);
  });
  test('a multi-line string counts every line it spans', () {
    const source = """
void f(int a) {
  print('''
one
\${a}
two''');
}""";
    expect(only(source).codeLines, 4);
  });
  test('a statement spread over lines counts each line', () {
    expect(only('void f() {\n  print(\n    1,\n  );\n}').codeLines, 3);
  });
  test('a nested function counts toward the enclosing body', () {
    const source = 'void f() {\n  void g() {\n    print(1);\n  }\n  g();\n}';
    expect(measured(source)['f']!.codeLines, 4);
    expect(measured(source)['f.g']!.codeLines, 1);
  });
}

void _expressions() {
  test('the expression counts, the arrow and semicolon do not', () {
    expect(only('int f() => 1;').codeLines, 1);
    expect(only('int f() =>\n    1 +\n    2;').codeLines, 2);
  });
  test('an async expression body counts its expression', () {
    expect(only('Future<int> f() async =>\n    1;').codeLines, 1);
  });
}

void _constructors() {
  test('initializers and the body both count', () {
    const source = '''
class C {
  C(int a)
    : b = a,
      c = a {
    print(a);
  }
  final int b;
  final int c;
}''';
    expect(only(source, 'C.new').codeLines, 3);
  });
  test('initializers alone count', () {
    const source = 'class C {\n  C(int a)\n    : b = a;\n  final int b;\n}';
    expect(only(source, 'C.new').codeLines, 1);
  });
  test('a factory with an expression body counts it', () {
    const source = 'class C {\n  C._();\n  factory C() =>\n      C._();\n}';
    expect(only(source, 'C.new').codeLines, 1);
  });
}
