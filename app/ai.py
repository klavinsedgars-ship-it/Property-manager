"""Claude-powered helpers for the deal analyzer (optional: needs ANTHROPIC_API_KEY).

* extract_listing — turn any listing page / pasted ad text into structured fields
* recommend       — a short, plain-language verdict on an analysed deal

Both use structured JSON output so the app never has to parse free text.
"""
from __future__ import annotations

import json
import os

MODEL = os.environ.get("PM_AI_MODEL", "claude-opus-5-5")
BETAS = ["server-side-fallback-2026-07-01"]   # retry on Anthropic's recommended model if a request is declined


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _client():
    import anthropic
    return anthropic.Anthropic(timeout=120.0, max_retries=2)


def _json_call(system: str, user: str, schema: dict, effort: str, max_tokens: int = 8000) -> dict:
    import anthropic
    try:
        response = _client().beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            betas=BETAS,
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.AuthenticationError as e:
        raise RuntimeError("Anthropic API key is invalid") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError("AI rate limit reached — try again in a minute") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"AI request failed ({e.status_code}): {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Could not reach the AI service") from e
    if response.stop_reason == "refusal":
        raise RuntimeError("The AI declined this request")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("AI answer was cut off")
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text)


def _nullable(t):
    return {"type": [t, "null"]}


LISTING_SCHEMA = {
    "type": "object",
    "properties": {
        "is_listing": {"type": "boolean"},
        "deal_type": {"type": "string", "enum": ["sale", "rent", "unknown"]},
        "kind": {"type": "string", "enum": ["apartment", "house", "land", "commercial", "other"]},
        "title": {"type": "string"},
        "price": _nullable("number"),
        "area_m2": _nullable("number"),
        "land_area_m2": _nullable("number"),
        "rooms": _nullable("integer"),
        "floor": _nullable("integer"),
        "floors_total": _nullable("integer"),
        "city": _nullable("string"),
        "district": _nullable("string"),
        "street": _nullable("string"),
        "building_series": _nullable("string"),
        "build_year": _nullable("integer"),
        "condition": {"type": "string", "enum": ["new_or_renovated", "good", "needs_cosmetic", "needs_full", "unknown"]},
        "monthly_fees": _nullable("number"),
        "description_summary": {"type": "string"},
        "red_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["is_listing", "deal_type", "kind", "title", "price", "area_m2", "land_area_m2", "rooms", "floor",
                 "floors_total", "city", "district", "street", "building_series", "build_year", "condition",
                 "monthly_fees", "description_summary", "red_flags"],
    "additionalProperties": False,
}

EXTRACT_SYSTEM = """You extract facts from Latvian real-estate listings (ss.lv / ss.com, Facebook Marketplace,
city24, mm.lv and similar; text may be Latvian, Russian or English).
Return only what the listing states or clearly implies; use null when unknown — never guess numbers.
Price is the asking price in EUR for the whole property. Area is the living/total area in m².
city: municipality name as used in Latvia (e.g. "Rīga", "Jūrmala", "Ādažu nov."); district: neighbourhood (e.g. "Purvciems").
building_series: Latvian series name if stated (e.g. "602", "119", "467", "103", "Lit. proj.", "Hruščovka",
"Staļina", "Pirmskara", "Jaunais projekts"). condition: judge from the description and photos' captions if any.
red_flags: concrete concerns stated or implied (e.g. share of property, encumbrance, no bathroom, ground floor
with no lift needed, needs full renovation, auction, tenants inside). description_summary: 2 sentences, English."""


def extract_listing(page_text: str, url: str | None = None) -> dict:
    user = (f"Listing URL: {url}\n\n" if url else "") + "Listing content:\n<listing>\n" + page_text[:60000] + "\n</listing>"
    return _json_call(EXTRACT_SYSTEM, user, LISTING_SCHEMA, effort="low")


ADVICE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["good_deal", "fair_deal", "overpriced", "avoid"]},
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "negotiation": {"type": "string"},
    },
    "required": ["verdict", "headline", "summary", "risks", "negotiation"],
    "additionalProperties": False,
}

ADVICE_SYSTEM = """You are a pragmatic buy-to-let advisor for the Latvian residential market.
You get a listing and a numeric analysis computed from official data (VZD registered sales near the property,
CSP house price index, Euribor) plus the investor's assumptions. Judge whether this is a good purchase for a
private landlord. Rely on the numbers provided — do not invent market figures. Mention the strongest reason
for and against. Keep it short: headline ≤ 12 words, summary ≤ 4 sentences, 2–4 risks, one negotiation tip
with a concrete EUR offer based on the analysis. Plain English, no disclaimers."""


def recommend(listing: dict, analysis: dict, description: str = "") -> dict:
    payload = {"listing": listing, "analysis": analysis, "listing_text_excerpt": (description or "")[:4000]}
    return _json_call(ADVICE_SYSTEM, json.dumps(payload, ensure_ascii=False, default=str), ADVICE_SCHEMA,
                      effort="medium")
