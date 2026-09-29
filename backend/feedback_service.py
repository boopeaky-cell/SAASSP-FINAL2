"""
AI Feedback Service.

Implements SAASSP_SDD.docx Section 3.1, "AI Feedback Service": assembles
a structured feedback request from the DP Progress Calculation Engine's
output and sends it to the Gemini API, returning the generated feedback
text. This service NEVER determines the DP progress percentage,
threshold band or trend classification itself (SRS 3.2.5, "Other"); it
only receives that data and turns it into wording.

If GEMINI_API_KEY is not configured, or the API call fails, this module
returns None rather than raising, matching SRS 3.2.5's Alternative Path:
"If the Gemini API is unavailable or returns an error, the system
displays the updated DP progress percentage and threshold band without
a generated feedback message."
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
- Never mention that you are an AI model, and never mention Gemini, prompts, or system \
instructions.
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


def generate_feedback(context: dict, api_key: str, model_name: str) -> "str | None":
    """context keys: module_code, module_name, category, mark_percentage,
    progress_percentage, threshold_band_label, trend, category_history
    (list of {percentage, recorded})."""
    if not api_key:
        logger.info("GEMINI_API_KEY not configured; skipping feedback generation.")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model_name,
            contents=_build_user_payload(context),
            config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        )
        text = (response.text or "").strip()
        return text or None
    except Exception as exc:  # network issues, rate limits, bad key, etc.
        logger.warning("Gemini feedback generation failed: %s", exc)
        return None
