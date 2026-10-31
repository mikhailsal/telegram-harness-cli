<?php

declare(strict_types=1);

namespace Quality\PhpHelper\Tests;

final class StructureTest extends Measuring
{
    public function testAFileThatDoesNotParseIsAnsweredWithItsError(): void
    {
        $path = $this->write('Broken.php', "<?php\n\nfunction f( {\n");
        self::assertSame(
            [
                'file' => $path,
                'functions' => [],
                'imports' => [],
                'loadable' => false,
                'units' => 0,
                'error' => ['line' => 3, 'message' => "Syntax error, unexpected '{', expecting T_VARIABLE"],
            ],
            (new \Quality\PhpHelper\Structure($this->autoload()))->file($path),
        );
    }

    public function testAFileIsAnsweredWithItsMetrics(): void
    {
        $answer = $this->measure("<?php\nfunction f() {}\n");
        self::assertSame(['file', 'functions', 'imports', 'loadable', 'units', 'error'], array_keys($answer));
        self::assertTrue($answer['loadable']);
        self::assertSame(1, $answer['units']);
        self::assertNull($answer['error']);
    }
}
