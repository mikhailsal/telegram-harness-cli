<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use PhpParser\Node;
use PhpParser\NodeTraverser;
use PhpParser\NodeVisitor\NameResolver;
use PhpParser\ParserFactory;
use PhpParser\Token;
use RuntimeException;

use function array_slice;
use function file_get_contents;
use function is_file;
use function sprintf;

/**
 * One parsed file: its statements, with every class name resolved, and its tokens.
 *
 * The file is parsed for the PHP version that runs the helper, the pinned
 * runtime. A syntax error raises `PhpParser\Error`.
 */
final readonly class Source
{
    /**
     * @param array<Node> $statements
     * @param array<Token> $tokens
     */
    private function __construct(
        public string $path,
        public array $statements,
        public array $tokens,
    ) {}

    public static function parse(string $path): self
    {
        $code = is_file($path) ? file_get_contents($path) : false;
        if ($code === false) {
            throw new RuntimeException(sprintf('cannot read %s', $path));
        }
        $parser = (new ParserFactory())->createForHostVersion();
        $statements = $parser->parse($code) ?? [];
        $resolved = (new NodeTraverser(new NameResolver()))->traverse($statements);
        return new self($path, $resolved, $parser->getTokens());
    }

    /** The position of the first token from `$position` on that is not whitespace or a comment. */
    public function next(int $position): int
    {
        while ($this->tokens[$position]->isIgnorable()) {
            $position++;
        }
        return $position;
    }

    /** The line of the first token from `$position` on that is not whitespace or a comment. */
    public function lineFrom(int $position): int
    {
        return $this->tokens[$this->next($position)]->line;
    }

    /**
     * Return the tokens of a node, from its first to its last.
     *
     * @return array<Token>
     */
    public function tokensOf(Node $node): array
    {
        $start = $node->getStartTokenPos();
        return array_slice($this->tokens, $start, $node->getEndTokenPos() - $start + 1);
    }
}
