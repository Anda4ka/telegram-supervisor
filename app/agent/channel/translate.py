"""Multi-language translation with Brand Voice preservation.

Translates posts to target languages while maintaining the channel's
unique voice profile — tone, addressing style, emoji patterns, etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent.channel.llm_client import openrouter_chat_completion
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.agent.channel.brand_voice import VoiceProfile

logger = get_logger("channel.translate")

_TRANSLATE_PROMPT = """\
You are a translator that PRESERVES the author's unique voice and personality.

RULES:
1. Translate the post to {target_language}.
2. PRESERVE the exact tone, slang level, and personality — do NOT make it more formal or polished.
3. If the original uses slang/profanity, find equivalent slang/profanity in the target language.
4. Keep the same emoji patterns and density.
5. Keep Markdown formatting (**bold**, [links](url)) intact.
6. Keep the footer exactly as-is (do NOT translate channel names, usernames, or footer structure).
7. Adapt cultural references if needed, but keep the same energy.

{voice_context}

Return ONLY the translated post, nothing else."""


SUPPORTED_LANGUAGES = {
    "ru": "Russian",
    "en": "English",
    "es": "Spanish",
    "vi": "Vietnamese",
    "uk": "Ukrainian",
    "zh": "Chinese (Simplified)",
    "pt": "Portuguese",
    "de": "German",
    "fr": "French",
    "tr": "Turkish",
    "ar": "Arabic",
    "ko": "Korean",
    "ja": "Japanese",
}


async def translate_post(
    text: str,
    target_language: str,
    api_key: str,
    model: str,
    *,
    voice_profile: VoiceProfile | None = None,
    temperature: float = 0.4,
    http_timeout: int = 30,
) -> str | None:
    """Translate a post to the target language, preserving brand voice.

    Args:
        text: Post text in original language.
        target_language: Target language code (e.g., "en") or full name.
        api_key: OpenRouter API key.
        model: Model to use for translation.
        voice_profile: Optional voice profile for tone preservation.

    Returns:
        Translated post text, or None on failure.
    """
    lang_name = SUPPORTED_LANGUAGES.get(target_language, target_language)

    voice_context = ""
    if voice_profile:
        voice_context = (
            f"VOICE PROFILE TO PRESERVE:\n"
            f"- Tone: {voice_profile.tone}\n"
            f"- Addressing style: adapt '{voice_profile.addressing}' to {lang_name} equivalent\n"
            f"- Emoji density: {voice_profile.emoji_density}\n"
            f"- Sentence style: {voice_profile.sentence_style}"
        )
        if voice_profile.forbidden_patterns:
            voice_context += f"\n- AVOID in translation: {', '.join(voice_profile.forbidden_patterns[:3])}"

    system_prompt = _TRANSLATE_PROMPT.format(
        target_language=lang_name,
        voice_context=voice_context,
    )

    try:
        result = await openrouter_chat_completion(
            api_key=api_key,
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            operation="translation",
            temperature=temperature,
            timeout=http_timeout,
        )

        if result:
            translated = str(result).strip()
            logger.info(
                "post_translated",
                target=lang_name,
                original_len=len(text),
                translated_len=len(translated),
            )
            return translated
        return None

    except Exception:
        logger.exception("translation_failed", target=lang_name)
        return None


async def translate_to_multiple(
    text: str,
    target_languages: list[str],
    api_key: str,
    model: str,
    *,
    voice_profile: VoiceProfile | None = None,
) -> dict[str, str]:
    """Translate a post to multiple languages.

    Returns a dict mapping language code → translated text.
    Failed translations are silently skipped.
    """
    results: dict[str, str] = {}
    for lang in target_languages:
        translated = await translate_post(text, lang, api_key, model, voice_profile=voice_profile)
        if translated:
            results[lang] = translated
    return results
