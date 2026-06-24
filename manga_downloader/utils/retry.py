"""Retry utilities with exponential backoff."""

from __future__ import annotations

import asyncio
import functools
import random
import time
from collections.abc import Callable
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def retry(
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """Decorator for retrying a function with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay cap in seconds.
        backoff_factor: Multiplier for each retry.
        jitter: Add random jitter to avoid thundering herd.
        exceptions: Exception types to catch and retry.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        raise
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    if jitter:
                        delay *= random.uniform(0.75, 1.25)
                    time.sleep(delay)
            if last_exception:
                raise last_exception
            raise RuntimeError("Retry failed with no exception")

        return wrapper  # type: ignore[return-value]

    return decorator


def async_retry(
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """Async decorator for retrying a coroutine with exponential backoff."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt == max_retries:
                        raise
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    if jitter:
                        delay *= random.uniform(0.75, 1.25)
                    await asyncio.sleep(delay)
            if last_exception:
                raise last_exception
            raise RuntimeError("Retry failed with no exception")

        return wrapper  # type: ignore[return-value]

    return decorator


class RetryableError(Exception):
    """Exception that should trigger a retry."""


class NonRetryableError(Exception):
    """Exception that should NOT trigger a retry."""
