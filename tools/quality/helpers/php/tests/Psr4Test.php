<?php

declare(strict_types=1);

namespace Quality\PhpHelper\Tests;

use Quality\PhpHelper\Paths;
use Quality\PhpHelper\Psr4;

final class Psr4Test extends Measuring
{
    public function testTheLongestPrefixWithAnExistingFileWins(): void
    {
        $first = $this->write('one/Thing.php', "<?php\n");
        $nested = $this->write('nested/Thing.php', "<?php\n");
        $autoload = Psr4::from([
            ['prefix' => 'App\\', 'directories' => [$this->directory . '/missing', $this->directory . '/one']],
            ['prefix' => 'App\\Deep\\', 'directories' => [$this->directory . '/nested']],
            ['prefix' => 'App\\', 'directories' => [$this->directory . '/../' . basename($this->directory) . '/two']],
        ]);
        self::assertSame($first, $autoload->resolve('App\\Thing'));
        self::assertSame($nested, $autoload->resolve('App\\Deep\\Thing'));
        self::assertNull($autoload->resolve('App\\Other'));
        self::assertNull($autoload->resolve('Vendor\\Thing'));
    }

    public function testPathsLoseDotSegments(): void
    {
        self::assertSame('/a/c', Paths::normalize('/a/./b/../c//'));
        self::assertSame('/', Paths::normalize('/..'));
    }
}
