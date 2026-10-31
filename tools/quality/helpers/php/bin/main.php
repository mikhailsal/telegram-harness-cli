<?php

declare(strict_types=1);

// The command line of the helper: `main.php <command> <request.json>` (see Cli).
require_once __DIR__ . '/../vendor/autoload.php';

Quality\PhpHelper\Cli::main($argv);
