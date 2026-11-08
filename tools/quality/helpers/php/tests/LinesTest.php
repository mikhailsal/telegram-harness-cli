<?php

declare(strict_types=1);

namespace Quality\PhpHelper\Tests;

final class LinesTest extends Measuring
{
    public function testCommentsAndBlankLinesDoNotCount(): void
    {
        $code = "function f() {\n    // a comment\n\n    /* another\n       one */\n    \$a = 1;\n    return \$a;\n}\n";
        self::assertSame(2, $this->only($code)->codeLines);
    }

    public function testATokenCountsEveryLineItSpans(): void
    {
        $code = "function f() {\n    return 'one\ntwo\nthree';\n}\n";
        self::assertSame(3, $this->only($code)->codeLines);
    }

    public function testAHeredocCountsItsLines(): void
    {
        $code = "function f() {\n    return <<<TEXT\n    one\n    two\n    TEXT;\n}\n";
        self::assertSame(4, $this->only($code)->codeLines);
    }

    public function testAnEmptyBodyHasNoLines(): void
    {
        self::assertSame(0, $this->only("function f() {\n}\n")->codeLines);
    }

    public function testAnArrowFunctionCountsItsExpression(): void
    {
        $functions = $this->functions("\$f = fn(\$a) =>\n    \$a\n    + 1;\n");
        self::assertSame(2, $functions['f']->codeLines);
    }

    public function testANestedFunctionCountsTowardTheEnclosingBody(): void
    {
        $functions = $this->functions("function f() {\n    \$g = function () {\n        return 1;\n    };\n}\n");
        self::assertSame(3, $functions['f']->codeLines);
        self::assertSame(1, $functions['f.g']->codeLines);
    }
}
