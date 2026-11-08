<?php

declare(strict_types=1);

namespace Quality\PhpHelper;

use RuntimeException;

/** A request the helper cannot accept. */
final class RequestError extends RuntimeException {}
