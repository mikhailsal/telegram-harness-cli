<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use JsonSerializable;

/** The metrics of one function, as the protocol reports them. */
final readonly class FunctionMetrics implements JsonSerializable
{
    public function __construct(
        public string $name,
        public int $line,
        public int $codeLines,
        public int $complexity,
        public int $nesting,
        public int $parameters,
    ) {}

    /** @return array<string, int|string> */
    public function jsonSerialize(): array
    {
        return [
            'name' => $this->name,
            'line' => $this->line,
            'code_lines' => $this->codeLines,
            'complexity' => $this->complexity,
            'nesting' => $this->nesting,
            'parameters' => $this->parameters,
        ];
    }
}
