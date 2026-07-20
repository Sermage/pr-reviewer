"""LLM-backed review core: turn a diff into a verdict + markdown body.

Two wire protocols are supported (see ``providers``): ``openai`` — OpenAI-compatible
chat completions (DeepSeek, OpenAI, local Ollama/LM Studio/vLLM) — and ``anthropic``
(Claude). The model is asked to reply with strict JSON so we can map it onto a
GitHub review event.

*What* to review is decided by the selected review profile (see ``profiles``),
so this module stays domain-agnostic — switch android → kmp without touching it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import httpx

from .diff_index import commentable_lines
from .profiles import AUTO_PROFILE, Profile, get_profile

_SEVERITY_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🔵"}

# Map the model's verdict onto GitHub's review `event` values.
_VERDICT_TO_EVENT = {
    "approve": "APPROVE",
    "request_changes": "REQUEST_CHANGES",
    "comment": "COMMENT",
}

# DeepSeek/most providers cap context; keep the diff bounded.
_MAX_DIFF_CHARS = 30_000


@dataclass
class ReviewResult:
    event: str          # APPROVE | REQUEST_CHANGES | COMMENT
    body: str           # markdown, ready to post
    verdict: str        # raw verdict from the model
    comments: list[dict] = field(default_factory=list)  # inline review comments
    summary: str = ""                                   # model's one-liner (for the orchestrator)
    leftover: list[dict] = field(default_factory=list)  # non-inline issues (for the orchestrator)


def _truncate(diff: str) -> str:
    if len(diff) <= _MAX_DIFF_CHARS:
        return diff
    return diff[:_MAX_DIFF_CHARS] + "\n\n...[diff truncated for length]..."


def _user_message(diff: str) -> str:
    return f"Вот diff PR:\n\n```diff\n{_truncate(diff)}\n```"


def _extract_json(content: str) -> dict:
    """Parse the model's reply as JSON, tolerating ```json ...``` fences.

    OpenAI/DeepSeek honour ``response_format=json_object``, but Claude and many
    local models wrap JSON in a markdown code block — strip it before parsing.
    """
    s = content.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s.strip())
    return json.loads(s)


async def _call_openai(
    diff: str, *, system_prompt: str, api_key: str, base_url: str,
    model: str, json_mode: bool = True,
) -> dict:
    """OpenAI-compatible chat completions (DeepSeek, OpenAI, Ollama, LM Studio, vLLM)."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _user_message(diff)},
        ],
        "temperature": 0.2,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return _extract_json(data["choices"][0]["message"]["content"])


async def _call_anthropic(
    diff: str, *, system_prompt: str, api_key: str, base_url: str, model: str
) -> dict:
    """Anthropic (Claude) messages API — different endpoint, auth and shape."""
    url = f"{base_url.rstrip('/')}/v1/messages"
    payload = {
        "model": model,
        "max_tokens": 4096,
        "temperature": 0.2,
        "system": system_prompt,
        "messages": [{"role": "user", "content": _user_message(diff)}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    text = "".join(
        block.get("text", "")
        for block in data.get("content", [])
        if block.get("type") == "text"
    )
    return _extract_json(text)


async def _call_llm(
    diff: str, *, system_prompt: str, api_key: str, base_url: str, model: str,
    provider: str = "openai", json_mode: bool = True,
) -> dict:
    """Dispatch to the right wire protocol for the selected provider."""
    if provider == "anthropic":
        return await _call_anthropic(
            diff, system_prompt=system_prompt, api_key=api_key,
            base_url=base_url, model=model,
        )
    return await _call_openai(
        diff, system_prompt=system_prompt, api_key=api_key,
        base_url=base_url, model=model, json_mode=json_mode,
    )


def _split_issues(
    issues: list[dict], diff: str
) -> tuple[list[dict], list[dict]]:
    """Split issues into (inline comments, leftover issues).

    An issue becomes an inline comment only if its (file, line) points at a
    line that actually exists on the RIGHT side of the diff — otherwise GitHub
    would reject the whole review. Everything else stays in the body.
    """
    anchors = commentable_lines(diff)
    inline: list[dict] = []
    leftover: list[dict] = []
    for it in issues:
        file = it.get("file")
        line = it.get("line")
        note = (it.get("note") or "").strip()
        sev = it.get("severity", "low")
        if file in anchors and isinstance(line, int) and line in anchors[file]:
            inline.append(
                {
                    "path": file,
                    "line": line,
                    "side": "RIGHT",
                    "body": f"{_SEVERITY_EMOJI.get(sev, '🔵')} {note}",
                }
            )
        else:
            leftover.append(it)
    return inline, leftover


def _render_body(
    parsed: dict, profile_name: str, leftover: list[dict], inline_count: int
) -> str:
    summary = parsed.get("summary", "").strip() or "Автоматическое ревью выполнено."
    title = {
        "android": "Android",
        "compose": "Jetpack Compose",
        "kmp": "Kotlin Multiplatform",
    }.get(profile_name, profile_name)
    lines = [f"## 🤖 AI-ревью ({title})", "", summary, ""]
    if inline_count:
        lines.append(f"💬 {inline_count} замечани{'е' if inline_count == 1 else 'я/й'} оставлено прямо в коде.")
        lines.append("")
    if leftover:
        lines.append("### Прочие замечания")
        for it in leftover:
            sev = it.get("severity", "low")
            file = it.get("file", "?")
            note = (it.get("note") or "").strip()
            lines.append(f"- {_SEVERITY_EMOJI.get(sev, '🔵')} **`{file}`** — {note}")
    elif not inline_count:
        lines.append("Серьёзных проблем не найдено. 👍")
    lines += ["", "> Сгенерировано автоматически AI PR Reviewer. Это подсказка, а не замена ревью человеком."]
    return "\n".join(lines)


async def review_diff(
    diff: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    profile: Profile | str | None = None,
    provider: str = "openai",
    json_mode: bool = True,
) -> ReviewResult:
    """Run the LLM over a diff and return a postable review.

    `profile` selects the reviewing focus (android, kmp, ...). Accepts a
    Profile, a profile name, or None (falls back to the default profile).
    `provider` is the wire protocol ("openai" | "anthropic"); `json_mode`
    toggles OpenAI's response_format (off for models that don't support it).

    Special profile ``"auto"`` hands off to the orchestrator, which detects the
    PR's direction(s) and runs the matching profile(s).
    """
    name = profile.name if isinstance(profile, Profile) else (profile or "")
    if isinstance(name, str) and name.lower() == AUTO_PROFILE:
        from .orchestrator import orchestrate_review  # lazy: avoids import cycle
        return await orchestrate_review(
            diff, api_key=api_key, base_url=base_url, model=model,
            provider=provider, json_mode=json_mode,
        )

    prof = profile if isinstance(profile, Profile) else get_profile(profile)

    if not diff.strip():
        return ReviewResult("COMMENT", "Пустой diff — нечего ревьюить.", "comment")

    parsed = await _call_llm(
        diff,
        system_prompt=prof.system_prompt,
        api_key=api_key,
        base_url=base_url,
        model=model,
        provider=provider,
        json_mode=json_mode,
    )
    verdict = str(parsed.get("verdict", "comment")).lower()
    event = _VERDICT_TO_EVENT.get(verdict, "COMMENT")
    inline, leftover = _split_issues(parsed.get("issues") or [], diff)
    body = _render_body(parsed, prof.name, leftover, len(inline))
    return ReviewResult(
        event=event, body=body, verdict=verdict, comments=inline,
        summary=parsed.get("summary", "").strip(), leftover=leftover,
    )
