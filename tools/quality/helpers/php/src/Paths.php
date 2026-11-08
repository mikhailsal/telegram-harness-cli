<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use function array_pop;
use function explode;
use function implode;

/** Absolute paths without `.` and `..` segments, which PHP resolves the same way. */
final class Paths
{
    public static function normalize(string $path): string
    {
        $parts = [];
        foreach (explode('/', $path) as $part) {
            if ($part === '..') {
                array_pop($parts);
            } elseif ($part !== '.' && $part !== '') {
                $parts[] = $part;
            }
        }
        return '/' . implode('/', $parts);
    }
}
