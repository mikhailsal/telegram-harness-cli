<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

/** A `structure` request: the component's PSR-4 prefixes and the files to measure. */
final readonly class StructureRequest
{
    /** @param list<string> $files */
    private function __construct(
        public Psr4 $autoload,
        public array $files,
    ) {}

    /**
     * Validate a `structure` request.
     *
     * @param array<mixed> $document
     */
    public static function from(array $document): self
    {
        $autoload = Psr4::from($document['autoload'] ?? null);
        return new self($autoload, Request::paths($document['files'] ?? null, 'files'));
    }
}
