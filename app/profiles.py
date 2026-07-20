"""Review profiles: pluggable domain focus for the reviewer.

A profile is just a name + a system prompt describing what to look for.
Add a new one by registering it in ``PROFILES``; select it at runtime with
the ``REVIEW_PROFILE`` env var. The rest of the pipeline is profile-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass

# The strict-JSON contract every profile must ask the model to follow.
# Kept separate so profiles only describe *what to review*, not *the format*.
_JSON_CONTRACT = """\
Верни СТРОГО валидный JSON без markdown-обёртки:
{
  "verdict": "approve" | "request_changes" | "comment",
  "summary": "1-3 предложения общего вывода на русском",
  "issues": [
    {"file": "путь", "severity": "high|medium|low", "note": "что не так и как починить"}
  ]
}
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


PROFILES: dict[str, Profile] = {
    "android": Profile("android", ANDROID_FOCUS),
    "kmp": Profile("kmp", KMP_FOCUS),
}

DEFAULT_PROFILE = "android"


def get_profile(name: str | None) -> Profile:
    """Return the requested profile, falling back to the default."""
    return PROFILES.get((name or DEFAULT_PROFILE).lower(), PROFILES[DEFAULT_PROFILE])
