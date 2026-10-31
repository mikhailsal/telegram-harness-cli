<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use PhpParser\Node;
use PhpParser\Token;

use function array_fill_keys;
use function count;
use function range;
use function substr;
use function substr_count;

/**
 * Code lines of a function body: physical lines that hold one of its tokens.
 *
 * Whitespace and comments are not counted, so comment-only and blank lines
 * never count, and a token that spans lines, such as a multi-line string,
 * counts every line it spans.
 */
final class Lines
{
    /** @param list<Node> $body */
    public static function count(Source $source, array $body): int
    {
        $found = [];
        foreach ($body as $node) {
            foreach ($source->tokensOf($node) as $token) {
                $found += self::spanned($token);
            }
        }
        return count($found);
    }

    /** @return array<int, true> */
    private static function spanned(Token $token): array
    {
        if ($token->isIgnorable()) {
            return [];
        }
        $last = $token->line + substr_count(substr($token->text, 0, -1), "\n");
        return array_fill_keys(range($token->line, $last), true);
    }
}
