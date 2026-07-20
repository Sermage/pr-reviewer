"""Review profiles: pluggable domain focus for the reviewer.

A profile is just a name + a system prompt describing what to look for.
Built-ins live in ``PROFILES``; custom ones are plain files in ``profiles.d/``
(``<name>.md`` where the file body is the review focus) and are picked up
automatically — no code change needed. Select at runtime with ``REVIEW_PROFILE``
(or ``pr-reviewer profile <name>``). The rest of the pipeline is profile-agnostic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Where user-defined profiles live. In the repo root so they're committed and
# available to GitHub Actions too. Override with $PROFILES_DIR.
PROFILES_DIR = Path(
    os.getenv("PROFILES_DIR", str(Path(__file__).resolve().parent.parent / "profiles.d"))
)

# The strict-JSON contract every profile must ask the model to follow.
# Kept separate so profiles only describe *what to review*, not *the format*.
_JSON_CONTRACT = """\
Верни СТРОГО валидный JSON без markdown-обёртки:
{
  "verdict": "approve" | "request_changes" | "comment",
  "summary": "1-3 предложения общего вывода на русском",
  "issues": [
    {
      "file": "путь как в diff",
      "line": <номер строки в НОВОЙ версии файла, к которой относится замечание>,
      "severity": "high|medium|low",
      "note": "что не так и как починить"
    }
  ]
}
Поле "line" — номер строки из правой (новой) стороны diff (считай по заголовкам @@ и добавленным/контекстным строкам). Если строку указать нельзя, ставь null.
Если серьёзных проблем нет — verdict "approve" и пустой issues.
Не придирайся к стилю без причины. Будь конкретным и кратким.
"""

ANDROID_FOCUS = """\
Ты — старший Android-инженер, делаешь код-ревью pull request'а (Kotlin/Java, Jetpack Compose, корутины).
Смотри именно на Android-специфику и корректность:
- утечки Context/Activity, работа с UI не в главном потоке;
- неотменённые корутины/Flow, злоупотребление GlobalScope;
- лишние recomposition в Compose, тяжёлые операции в composable;
- проблемы с жизненным циклом, утечки ресурсов;
- очевидные баги, небезопасные касты, NPE, забытые null-проверки.
"""

COMPOSE_FOCUS = """\
Ты — инженер по Jetpack Compose, делаешь код-ревью pull request'а.
Смотри на специфику Compose и корректность:
- лишние recomposition: нестабильные параметры, лямбды и объекты, создаваемые в теле composable;
- тяжёлые вычисления в composable без remember/derivedStateOf;
- side effects не в правильных API (LaunchedEffect/DisposableEffect/SideEffect) с корректными ключами;
- состояние: hoisting, mutableStateOf без remember, утечка state между рекомпозициями;
- работа со списками без ключей в LazyColumn/LazyRow, modifier order, лишние аллокации в отрисовке;
- очевидные баги, небезопасные касты, NPE.
"""

KMP_FOCUS = """\
Ты — инженер Kotlin Multiplatform (KMP), делаешь код-ревью pull request'а.
Смотри на специфику KMP и корректность:
- правильное разнесение кода по source sets (commonMain / androidMain / iosMain);
- платформо-зависимый код вне expect/actual, утечки платформенных API в commonMain;
- корректность expect/actual объявлений, отсутствие actual-реализаций;
- потокобезопасность и особенности корутин на разных платформах (Dispatchers.Main на iOS);
- работа с ресурсами и сериализацией, общими для платформ;
- очевидные баги, небезопасные касты, NPE.
"""


@dataclass(frozen=True)
class Profile:
    name: str
    focus: str  # domain-specific reviewing instructions

    @property
    def system_prompt(self) -> str:
        return f"{self.focus}\n{_JSON_CONTRACT}"


# Built-in profiles, always available.
PROFILES: dict[str, Profile] = {
    "android": Profile("android", ANDROID_FOCUS),
    "compose": Profile("compose", COMPOSE_FOCUS),
    "kmp": Profile("kmp", KMP_FOCUS),
}

DEFAULT_PROFILE = "android"

# Meta-profile: let the orchestrator detect the PR's direction(s) and run the
# matching profile(s) automatically (see app/orchestrator.py). Not a real focus.
AUTO_PROFILE = "auto"


def load_profiles() -> dict[str, Profile]:
    """Built-in profiles plus any custom ones from ``profiles.d/*.md``.

    A custom profile whose name matches a built-in overrides it.
    """
    profiles = dict(PROFILES)
    if PROFILES_DIR.is_dir():
        for path in sorted(PROFILES_DIR.glob("*.md")):
            focus = path.read_text().strip()
            if focus:
                key = path.stem.lower()
                profiles[key] = Profile(key, focus)
    return profiles


def available_profiles() -> list[str]:
    """Names of all profiles (built-in + custom), in listing order."""
    return list(load_profiles())


def is_builtin(name: str) -> bool:
    return name.lower() in PROFILES


def custom_path(name: str) -> Path:
    """Filesystem path of the custom-profile file for `name`."""
    return PROFILES_DIR / f"{name.lower()}.md"


def get_profile(name: str | None) -> Profile:
    """Return the requested profile, falling back to the default."""
    profiles = load_profiles()
    return profiles.get((name or DEFAULT_PROFILE).lower(), profiles[DEFAULT_PROFILE])
