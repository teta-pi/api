import json

import anthropic

from app.core.config import settings


async def categorize_business(
    name: str,
    description: str | None,
    block_texts: list[str],
) -> dict:
    """
    Use Claude to auto-detect industry, sub-category, keywords, claims, and region.
    Returns ai_categories JSONB.
    """
    if not settings.anthropic_api_key:
        return {}

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    content_parts = [f"Business name: {name}"]
    if description:
        content_parts.append(f"Description: {description}")
    for i, block in enumerate(block_texts, 1):
        content_parts.append(f"Block {i}: {block}")

    prompt = "\n".join(content_parts)

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Analyze this business and return ONLY a JSON object with these fields:\n"
                    f"industry, sub_category, cuisine (if food), specialty, claims (array of strings), "
                    f"region, confidence (0-1).\n\nBusiness info:\n{prompt}"
                ),
            }
        ],
    )

    text = message.content[0].text.strip()
    # Extract JSON from the response
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


def block_embedding_text(title: str | None, description: str | None) -> str:
    """Text fed to the embedder for a block (title + description)."""
    return "\n".join(p for p in (title, description) if p)


async def generate_embedding(text: str) -> list[float]:
    """Generate text embedding using OpenAI text-embedding-3-small."""
    if not settings.openai_api_key:
        return []
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    resp = await client.embeddings.create(
        model="text-embedding-3-small",
        input=text[:8000],
    )
    return resp.data[0].embedding


async def verify_claim(
    claim: str,
    business_name: str,
    blocks: list[dict],
) -> dict:
    """Use Claude to verify a specific claim against business blocks."""
    if not settings.anthropic_api_key:
        return {"claim": claim, "result": "unverified", "evidence": [], "confidence": 0.0}

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    blocks_text = "\n".join(
        f"- Block '{b.get('title')}': {b.get('description')} "
        f"(C2PA verified: {b.get('c2pa_verified', False)}, Bitcoin TS: {b.get('bitcoin_confirmed', False)})"
        for b in blocks
    )

    message = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Business: {business_name}\n"
                    f"Claim to verify: {claim}\n"
                    f"Blocks:\n{blocks_text}\n\n"
                    f"Return JSON: {{result: 'verified'|'unverified'|'partial', evidence: [{{block_title, confidence}}], confidence: 0-1}}"
                ),
            }
        ],
    )

    text = message.content[0].text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        result = json.loads(text)
        result["claim"] = claim
        return result
    except json.JSONDecodeError:
        return {"claim": claim, "result": "unverified", "evidence": [], "confidence": 0.0}
