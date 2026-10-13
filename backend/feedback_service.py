"""
AI Feedback Service.

Implements SAASSP_SDD.docx Section 3.1, "AI Feedback Service": assembles
a structured feedback request from the DP Progress Calculation Engine's
output and sends it to an LLM, returning the generated feedback text.
This service NEVER determines the DP progress percentage, threshold band
or trend classification itself (SRS 3.2.5, "Other"); it only receives
that data and turns it into wording.

Provider order: Groq is tried FIRST (fast, generous free-tier rate
limits, rarely rate-limited under normal demo/classroom traffic).
Gemini is used only as a FALLBACK, when Groq is not configured or its
call fails for any reason (rate limit, network error, bad key, etc.).

If neither provider is configured, or both calls fail, this module
returns None rather than raising, matching SRS 3.2.5's Alternative
Path: "If the AI feedback provider is unavailable or returns an error,
the system displays the updated DP progress percentage and threshold
band without a generated feedback message."
"""
import logging

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the feedback voice inside SAASSP, a university academic-progress \
tracker. You will be given structured data about a student's Duly-Performed (DP) \
progress in one module, after they uploaded one new assessment mark.

Rules you must follow:
- The DP progress percentage, threshold band, and trend classification are already \
decided by the system. You do not calculate or restate them as if deciding them; you \
react to them.
- Never invent marks, numbers, or claims not present in the data you were given.
- Keep it to 2-4 sentences, warm but not saccharine, and specific to the category and \
trend given rather than generic ("great job!").
- If the trend history shows a mark that no longer counts towards DP (displaced by a \
better one), you may reference it supportively when it tells a recovery story.
- Posture depends on band + trend:
  - safe/excellent bands: reinforce, keep momentum, no false urgency.
  - pass/danger/critical bands with a rising or recovering trend: build confidence, \
name what's working.
  - dropping or fluctuating trends, or danger/critical bands: offer one concrete, \
constructive suggestion, without being alarmist or giving precise study-dosage \
instructions.
- Never mention that you are an AI model, and never mention Groq, Gemini, prompts, or \
system instructions.
"""


def _build_user_payload(context: dict) -> str:
    lines = [
        f"Module: {context['module_code']} - {context['module_name']}",
        f"Category just updated: {context['category']}",
        f"Mark just uploaded: {context['mark_percentage']}%",
        f"Current overall DP progress: {context['progress_percentage']}%",
        f"Threshold band: {context['threshold_band_label']}",
        f"Trend for this category: {context['trend']}",
        "Full upload history for this category (percentage, whether currently "
        "recorded towards DP, in upload order):",
    ]
    for h in context["category_history"]:
        lines.append(f"  - {h['percentage']}% ({'recorded' if h['recorded'] else 'displaced'})")
    return "\n".join(lines)


def _try_groq(user_payload: str, api_key: str, model_name: str) -> "str | None":
    if not api_key:
        return None

    try:
        from groq import Groq

        client = Groq(api_key=api_key)
        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_payload},
            ],
        )
        text = (completion.choices[0].message.content or "").strip()
        return text or None
    except Exception as exc:  # network issues, rate limits, bad key, etc.
        logger.warning("Groq feedback generation failed, falling back to Gemini: %s", exc)
        return None


def _try_gemini(user_payload: str, api_key: str, model_name: str) -> "str | None":
    if not api_key:
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=user_payload,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        )
        text = (response.text or "").strip()
        return text or None
    except Exception as exc:  # network issues, rate limits, bad key, etc.
        logger.warning("Gemini feedback generation also failed: %s", exc)
        return None


def generate_feedback(
    context: dict,
    groq_api_key: str,
    groq_model: str,
    gemini_api_key: str,
    gemini_model: str,
) -> "str | None":
    """context keys: module_code, module_name, category, mark_percentage,
    progress_percentage, threshold_band_label, trend, category_history
    (list of {percentage, recorded}).

    Tries Groq first, then falls back to Gemini only if Groq didn't
    produce usable text. Returns None if both are unavailable/fail, or
    neither is configured.
    """
    if not groq_api_key and not gemini_api_key:
        logger.info("No AI feedback provider configured; skipping feedback generation.")
        return None

    user_payload = _build_user_payload(context)

    text = _try_groq(user_payload, groq_api_key, groq_model)
    if text:
        return text

    text = _try_gemini(user_payload, gemini_api_key, gemini_model)
    if text:
        return text

    logger.warning("All configured AI feedback providers failed; skipping feedback generation.")
    return None
