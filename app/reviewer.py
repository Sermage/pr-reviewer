"""LLM-backed review core: turn a diff into a verdict + markdown body.

Provider is DeepSeek (OpenAI-compatible chat completions). The model is asked
to reply with strict JSON so we can map it onto a GitHub review event.

*What* to review is decided by the selected review profile (see ``profiles``),
so this module stays domain-agnostic — switch android → kmp without touching it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import httpx

from .profiles import Profile, get_profile

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


def _truncate(diff: str) -> str:
    if len(diff) <= _MAX_DIFF_CHARS:
        return diff
    return diff[:_MAX_DIFF_CHARS] + "\n\n...[diff truncated for length]..."


async def _call_llm(
    diff: str, *, system_prompt: str, api_key: str, base_url: str, model: str
) -> dict:
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Вот diff PR:\n\n```diff\n{_truncate(diff)}\n```"},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def _render_body(parsed: dict, profile_name: str) -> str:
    summary = parsed.get("summary", "").strip() or "Автоматическое ревью выполнено."
    issues = parsed.get("issues") or []
    title = {"android": "Android", "kmp": "Kotlin Multiplatform"}.get(
        profile_name, profile_name
    )
    lines = [f"## 🤖 AI-ревью ({title})", "", summary, ""]
    if issues:
        lines.append("### Замечания")
        emoji = {"high": "🔴", "medium": "🟡", "low": "🔵"}
        for it in issues:
            sev = it.get("severity", "low")
            file = it.get("file", "?")
            note = it.get("note", "").strip()
            lines.append(f"- {emoji.get(sev, '🔵')} **`{file}`** — {note}")
    else:
        lines.append("Серьёзных проблем не найдено. 👍")
    lines += ["", "> Сгенерировано автоматически Android PR Reviewer. Это подсказка, а не замена ревью человеком."]
    return "\n".join(lines)


async def review_diff(
    diff: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    profile: Profile | str | None = None,
) -> ReviewResult:
    """Run the LLM over a diff and return a postable review.

    `profile` selects the reviewing focus (android, kmp, ...). Accepts a
    Profile, a profile name, or None (falls back to the default profile).
    """
    prof = profile if isinstance(profile, Profile) else get_profile(profile)

    if not diff.strip():
        return ReviewResult("COMMENT", "Пустой diff — нечего ревьюить.", "comment")

    parsed = await _call_llm(
        diff,
        system_prompt=prof.system_prompt,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    verdict = str(parsed.get("verdict", "comment")).lower()
    event = _VERDICT_TO_EVENT.get(verdict, "COMMENT")
    return ReviewResult(event=event, body=_render_body(parsed, prof.name), verdict=verdict)
