"""Profile orchestrator: pick the right review profile(s) for a PR.

Activated by the meta-profile ``auto`` (``REVIEW_PROFILE=auto`` or
``pr-reviewer review --profile auto``). The flow:

1. **Detect** the PR's direction(s) with a fast, dependency-free heuristic over
   the diff (file paths + added/context lines) — no extra LLM call.
2. **Plan**: run one agent (``review_diff``) per detected direction that has a
   matching profile. If a direction has no profile, fall back to the default
   profile and warn in the conclusion that accuracy for that direction is reduced.
   If nothing is detected, just run the default profile.
3. **Merge** the agents' results into a single review: worst verdict wins,
   inline comments are unioned (tagged by profile), summaries are stacked.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from .profiles import DEFAULT_PROFILE, available_profiles
from .reviewer import (
    _SEVERITY_EMOJI,
    _VERDICT_TO_EVENT,
    ReviewResult,
    review_diff,
)

# Cap parallel agents so a PR touching everything can't fan out unbounded.
MAX_AGENTS = 3


@dataclass(frozen=True)
class Topic:
    name: str            # matches a profile name when one exists (built-in or custom)
    label: str           # human-readable, used in the conclusion
    signals: tuple[str, ...]  # regexes that hint this direction is present


# Order matters only for readability; "android" is the broad baseline and comes last.
TOPICS: tuple[Topic, ...] = (
    Topic("compose", "Jetpack Compose", (
        r"@Composable", r"androidx\.compose", r"\bsetContent\b",
        r"\bremember\s*\{", r"\brememberSaveable\b", r"\bLaunchedEffect\b",
        r"\bMutableState\b", r"\bmutableStateOf\b",
    )),
    Topic("kmp", "Kotlin Multiplatform", (
        r"\bexpect\s+(?:fun|class|object|val|interface)",
        r"\bactual\s+(?:fun|class|object|val)",
        r"\bcommonMain\b", r"\bandroidMain\b", r"\biosMain\b",
        r"kotlinx\.cinterop", r"\bDispatchers\.Main\b",
    )),
    Topic("network", "Сеть / API", (
        r"\bRetrofit\b", r"\bOkHttp", r"\bHttpURLConnection\b",
        r"io\.ktor\.client", r"@GET\b", r"@POST\b", r"@Headers\b",
    )),
    Topic("database", "База данных", (
        r"\bRoom\b", r"@Entity\b", r"@Dao\b", r"@Query\b",
        r"SQLiteOpenHelper", r"\bSELECT\b.*\bFROM\b",
    )),
    Topic("security", "Безопасность", (
        r"\bKeyStore\b", r"EncryptedSharedPreferences", r"\bCipher\b",
        r"\bMessageDigest\b", r"android\.permission", r"\bBiometric",
    )),
    Topic("android", "Android", (
        r"\bandroidx?\.", r"\bActivity\b", r"\bFragment\b", r"AndroidManifest",
        r"\bContext\b", r"\blifecycleScope\b", r"\bViewModel\b", r"\.kt\b",
    )),
)

_LABEL = {t.name: t.label for t in TOPICS}
_TITLE = {
    "android": "Android",
    "compose": "Jetpack Compose",
    "kmp": "Kotlin Multiplatform",
}

_SEVERITY_RANK = {"APPROVE": 0, "COMMENT": 1, "REQUEST_CHANGES": 2}
_EVENT_TO_VERDICT = {v: k for k, v in _VERDICT_TO_EVENT.items()}


def label(name: str) -> str:
    return _LABEL.get(name, name)


def detect_topics(diff: str) -> dict[str, int]:
    """Return {topic: match_count} for every direction present in the diff."""
    scores: dict[str, int] = {}
    for topic in TOPICS:
        n = sum(len(re.findall(sig, diff, flags=re.IGNORECASE)) for sig in topic.signals)
        if n:
            scores[topic.name] = n
    return scores


@dataclass(frozen=True)
class Plan:
    chosen: list[str]        # profiles to actually run (highest score first)
    missing: list[str]       # detected directions with no matching profile
    scores: dict[str, int]   # all detected directions → score
    default: str             # profile used as fallback


def plan_review(diff: str, available: list[str]) -> Plan:
    """Decide which profiles to run for `diff` given the `available` profiles."""
    scores = detect_topics(diff)
    ranked = sorted(scores, key=lambda d: (-scores[d], d))
    default = DEFAULT_PROFILE if DEFAULT_PROFILE in available else (
        available[0] if available else DEFAULT_PROFILE
    )
    chosen = [d for d in ranked if d in available][:MAX_AGENTS]
    missing = [d for d in ranked if d not in available]
    if not chosen:
        chosen = [default]
    return Plan(chosen=chosen, missing=missing, scores=scores, default=default)


def _worst_event(events: list[str]) -> str:
    return max(events, key=lambda e: _SEVERITY_RANK.get(e, 1))


def _render_body(results: list[tuple[str, ReviewResult]], plan: Plan, inline_count: int) -> str:
    lines = ["## 🤖 AI-ревью (оркестратор профилей)", ""]

    ranked = sorted(plan.scores, key=lambda d: -plan.scores[d])
    dirs = ", ".join(f"{label(d)} ({plan.scores[d]})" for d in ranked) or "—"
    lines.append(f"**Определённые направления:** {dirs}")
    lines.append(f"**Запущенные профили:** {', '.join(name for name, _ in results)}")
    lines.append("")

    for m in plan.missing:
        lines.append(
            f"> ⚠️ Нет профиля для направления «{label(m)}» — ревью по нему сделано "
            f"дефолтным профилем `{plan.default}`; точность по этому направлению снижена. "
            f"Добавить профиль: `pr-reviewer profile --add {m}`."
        )
    if plan.missing:
        lines.append("")

    for name, r in results:
        lines.append(f"### Профиль `{name}` — {_TITLE.get(name, label(name))}")
        lines.append(r.summary or "Замечаний нет.")
        for it in r.leftover:
            sev = it.get("severity", "low")
            file = it.get("file", "?")
            note = (it.get("note") or "").strip()
            lines.append(f"- {_SEVERITY_EMOJI.get(sev, '🔵')} **`{file}`** — {note}")
        lines.append("")

    if inline_count:
        word = "замечание" if inline_count == 1 else "замечания/й"
        lines.append(f"💬 {inline_count} {word} оставлено прямо в коде.")
        lines.append("")
    lines.append(
        "> Сгенерировано автоматически Android PR Reviewer (оркестратор). "
        "Это подсказка, а не замена ревью человеком."
    )
    return "\n".join(lines)


def _merge(results: list[tuple[str, ReviewResult]], plan: Plan) -> ReviewResult:
    multi = len(results) > 1
    event = _worst_event([r.event for _, r in results])
    verdict = _EVENT_TO_VERDICT.get(event, "comment")

    seen: set[tuple] = set()
    comments: list[dict] = []
    for name, r in results:
        for c in r.comments:
            body = f"[{name}] {c['body']}" if multi else c["body"]
            key = (c["path"], c["line"], body)
            if key in seen:
                continue
            seen.add(key)
            comments.append({**c, "body": body})

    body = _render_body(results, plan, len(comments))
    summary = "; ".join(r.summary for _, r in results if r.summary)
    return ReviewResult(
        event=event, body=body, verdict=verdict, comments=comments, summary=summary
    )


async def orchestrate_review(
    diff: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    provider: str = "openai",
    json_mode: bool = True,
) -> ReviewResult:
    """Detect direction(s), run the matching profile agent(s), merge into one review."""
    if not diff.strip():
        return ReviewResult("COMMENT", "Пустой diff — нечего ревьюить.", "comment")

    plan = plan_review(diff, available_profiles())

    raw = await asyncio.gather(*[
        review_diff(
            diff, api_key=api_key, base_url=base_url, model=model,
            profile=name, provider=provider, json_mode=json_mode,
        )
        for name in plan.chosen
    ])
    results = list(zip(plan.chosen, raw))

    # A single profile with nothing missing is just a normal review — keep it clean.
    if len(results) == 1 and not plan.missing:
        return results[0][1]
    return _merge(results, plan)
