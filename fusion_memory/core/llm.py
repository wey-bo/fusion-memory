from __future__ import annotations

import json
import re
import socket
import threading
import time
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib import request


class LLMClient(Protocol):
    def structured(self, prompt: str, schema: dict[str, Any], input: dict[str, Any]) -> dict[str, Any]:
        ...


class StaticLLMClient:
    """Test/dry-run LLM client returning a fixed structured payload."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def structured(self, prompt: str, schema: dict[str, Any], input: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"prompt": prompt, "schema": schema, "input": input})
        return self.response


class LLMEndpointError(RuntimeError):
    def __init__(self, status_code: int, body: str, retry_after_seconds: float | None = None) -> None:
        self.status_code = status_code
        self.body = body
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"LLM endpoint returned HTTP {status_code}: {_short_body(body)}")


class OpenAICompatibleLLMClient:
    """Dependency-free structured LLM client for OpenAI-compatible endpoints.

    The endpoint is expected to accept chat-completions shaped JSON and return
    either a JSON object in `choices[0].message.content` or an already
    structured object under `structured`.
    """

    _rate_limit_lock = threading.Lock()
    _last_request_at: dict[tuple[str, str], float] = {}

    def __init__(
        self,
        endpoint: str,
        *,
        api_key: str | None = None,
        model: str = "local-structured-extractor",
        timeout_seconds: float = 30.0,
        retry_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
        retry_max_backoff_seconds: float = 30.0,
        min_interval_seconds: float = 0.0,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = max(1, retry_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self.retry_max_backoff_seconds = max(self.retry_backoff_seconds, retry_max_backoff_seconds)
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.calls: list[dict[str, Any]] = []
        self.version = f"openai-compatible:{model}"

    def structured(self, prompt: str, schema: dict[str, Any], input: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": "JSON only.\n"
                    + json.dumps({"prompt": prompt, "schema": schema, "input": input}, ensure_ascii=False),
                },
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        attempts = self.retry_attempts
        parsed: dict[str, Any] | None = None
        last_error: Exception | None = None
        data: dict[str, Any] = {}
        attempts_used = 0
        for attempt in range(attempts):
            attempts_used = attempt + 1
            try:
                self._throttle()
                data = _post_json(self.endpoint, payload, api_key=self.api_key, timeout_seconds=self.timeout_seconds)
                if _is_empty_chat_completion(data) and not payload.get("stream"):
                    stream_payload = dict(payload)
                    stream_payload["stream"] = True
                    self._throttle()
                    data = _post_json(
                        self.endpoint,
                        stream_payload,
                        api_key=self.api_key,
                        timeout_seconds=self.timeout_seconds,
                    )
                parsed = _extract_structured_response(data)
                break
            except LLMEndpointError as exc:
                last_error = exc
                if not _is_retryable_endpoint_error(exc) or attempt == attempts - 1:
                    break
                time.sleep(self._retry_delay(attempt, exc))
            except URLError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                time.sleep(self._retry_delay(attempt, exc))
            except ValueError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                time.sleep(self._retry_delay(attempt, exc))
        latency_ms = (time.perf_counter() - started) * 1000
        call = {
            "prompt": prompt,
            "prompt_version": prompt.splitlines()[0] if prompt else "",
            "schema": schema,
            "input": input,
            "model": self.model,
            "latency_ms": latency_ms,
            "usage": data.get("usage", {}) if isinstance(data, dict) else {},
            "attempts": attempts_used,
        }
        if parsed is None and last_error is not None:
            call["error"] = _short_body(str(last_error))
        self.calls.append(call)
        if parsed is not None:
            return parsed
        if last_error is not None:
            raise last_error
        return _extract_structured_response(data)

    def _throttle(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        key = (self.endpoint, self.model)
        with self._rate_limit_lock:
            now = time.monotonic()
            last = self._last_request_at.get(key)
            if last is not None:
                wait_seconds = self.min_interval_seconds - (now - last)
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                    now = time.monotonic()
            self._last_request_at[key] = now

    def _retry_delay(self, attempt: int, error: Exception) -> float:
        if isinstance(error, LLMEndpointError) and error.retry_after_seconds is not None:
            return min(self.retry_max_backoff_seconds, max(0.0, error.retry_after_seconds))
        delay = self.retry_backoff_seconds * (2**attempt)
        return min(self.retry_max_backoff_seconds, delay)


def _post_json(endpoint: str, payload: dict[str, Any], *, api_key: str | None, timeout_seconds: float) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with _urlopen_with_ipv4_fallback(req, timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            status = response.status
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise LLMEndpointError(
            exc.code,
            body,
            retry_after_seconds=_retry_after_seconds(exc.headers.get("Retry-After")),
        ) from exc
    data = _decode_json_or_sse(body, status)
    if not isinstance(data, dict):
        raise ValueError("LLM endpoint must return a JSON object")
    return data


def _urlopen_with_ipv4_fallback(req: request.Request, timeout_seconds: float):
    try:
        return request.urlopen(req, timeout=timeout_seconds)
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        if not _looks_like_ipv6_network_failure(reason):
            raise
        original_getaddrinfo = socket.getaddrinfo

        def ipv4_getaddrinfo(*args, **kwargs):
            host = args[0]
            port = args[1]
            socktype = args[3] if len(args) > 3 else kwargs.get("type", 0)
            proto = args[4] if len(args) > 4 else kwargs.get("proto", 0)
            flags = args[5] if len(args) > 5 else kwargs.get("flags", 0)
            return original_getaddrinfo(host, port, socket.AF_INET, socktype, proto, flags)

        socket.getaddrinfo = ipv4_getaddrinfo
        try:
            return request.urlopen(req, timeout=timeout_seconds)
        finally:
            socket.getaddrinfo = original_getaddrinfo


def _looks_like_ipv6_network_failure(reason: Any) -> bool:
    errno = getattr(reason, "errno", None)
    return errno == 101 or "Network is unreachable" in str(reason)


def _is_retryable_endpoint_error(error: LLMEndpointError) -> bool:
    return error.status_code in {429, 500, 502, 503, 504}


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _decode_json_or_sse(body: str, status: int) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        if body.lstrip().startswith("data:"):
            return _decode_sse_chat_completion(body)
        raise ValueError(f"LLM endpoint returned non-JSON response with HTTP {status}: {_short_body(body)}") from exc
    if not isinstance(data, dict):
        raise ValueError("LLM endpoint must return a JSON object")
    return data


def _decode_sse_chat_completion(body: str) -> dict[str, Any]:
    content_parts: list[str] = []
    usage: dict[str, Any] = {}
    last_event: dict[str, Any] | None = None
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if not payload or payload == "[DONE]":
            continue
        event = json.loads(payload)
        if isinstance(event, dict):
            last_event = event
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            choices = event.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
                message = first.get("message") if isinstance(first.get("message"), dict) else {}
                piece = delta.get("content") or message.get("content")
                if isinstance(piece, str):
                    content_parts.append(piece)
    content = "".join(content_parts).strip()
    if content:
        return {"choices": [{"message": {"content": content}}], "usage": usage}
    if last_event is not None:
        return last_event
    raise ValueError("LLM endpoint returned an empty SSE response")


def _extract_structured_response(data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("structured"), dict):
        return data["structured"]
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        for content in _message_content_candidates(message):
            if isinstance(content, dict):
                return content
            if isinstance(content, str):
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return parsed
    if any(key in data for key in ["facts", "events", "relations", "answer", "matched"]):
        return data
    raise ValueError("LLM endpoint did not return a structured JSON object")


def _message_content_candidates(message: dict[str, Any]) -> list[Any]:
    candidates: list[Any] = [message.get("content")]
    candidates.append(message.get("reasoning_content"))
    provider_fields = message.get("provider_specific_fields")
    if isinstance(provider_fields, dict):
        candidates.append(provider_fields.get("reasoning_content"))
        candidates.append(provider_fields.get("content"))
    return [candidate for candidate in candidates if candidate is not None]


def _is_empty_chat_completion(data: dict[str, Any]) -> bool:
    choices = data.get("choices")
    if choices:
        return False
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return data.get("object") in {"chat.completion", "chat.completion.chunk"} and usage.get("completion_tokens") == 0


def _short_body(body: str, limit: int = 300) -> str:
    clean = " ".join(body.split())
    clean = sanitize_error_text(clean, limit=None)
    return clean[:limit] + ("..." if len(clean) > limit else "")


def sanitize_error_text(text: str, limit: int | None = 300) -> str:
    clean = " ".join(str(text).split())
    clean = re.sub(r"sk-[A-Za-z0-9_.-]{3,}", "sk-...", clean)
    clean = re.sub(r"(api[_ -]?key\s*[=:]\s*)[A-Za-z0-9._~+/=-]{8,}", r"\1...", clean, flags=re.I)
    clean = re.sub(r"(token\s*[=:]\s*)[A-Za-z0-9._~+/=-]{8,}", r"\1...", clean, flags=re.I)
    clean = re.sub(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}", r"\1...", clean, flags=re.I)
    if limit is None:
        return clean
    return clean[:limit] + ("..." if len(clean) > limit else "")
