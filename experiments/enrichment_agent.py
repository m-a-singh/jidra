from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class MethodEnrichmentAgent:
    """
    Extracts semantic information from a method.
    """

    def __init__(
        self,
        llm_client,
        model: str = "ollama/qwen2.5-coder:7b",
        max_tokens: int = 800,
    ):
        self.llm_client = llm_client
        self.model = model
        self.max_tokens = max_tokens

    def _build_extraction_prompt(self, method_entry, context: dict | None = None) -> str:
        """Build the prompt for method extraction."""
        source = method_entry.source
        class_context = method_entry.class_context or {}
        class_annotations = class_context.get("annotations", [])
        class_full_name = method_entry.class_full_name

        context_block = json.dumps(context or {}, ensure_ascii=True)[:12000]
        return f"""You are a code analyst. Extract semantic information from this Java method.

**Method Signature:**
{method_entry.signature}

**File:** {method_entry.file_path}:{method_entry.start_line}-{method_entry.end_line}

**Class Context:**
- Full name: {class_full_name}
- Annotations: {", ".join(class_annotations) if class_annotations else "none"}

**Method Annotations:**
{", ".join(method_entry.annotations) if method_entry.annotations else "none"}

**Method Source Code:**
```java
{source}
```

**Deterministic Context (parser/static):**
```json
{context_block}
```

**Task:**
Extract semantic fields in JSON format:

1. **summary**: A single-line business-level description of what this method does (max 100 chars).
   - Focus on: inputs, outputs, side effects, business purpose.
   - Example: "Fetches user from cache, falls back to DB if miss"

2. **business_intent**: Business intent in one sentence.

3. **risk_notes**: potential risks/uncertainties in one short sentence.

4. **confidence**: Your confidence in this extraction (0.0-1.0).
   - 0.9+ = clear, confident extraction
   - 0.7-0.89 = mostly clear, minor ambiguity
   - <0.7 = complex/ambiguous method

5. **reasoning**: Briefly explain your analysis (1-2 sentences).

**Output (JSON only, no markdown):**
{{
  "summary": "...",
  "business_intent": "...",
  "risk_notes": "...",
  "confidence": 0.0,
  "reasoning": "..."
}}
"""

    async def extract(self, method_entry, context: dict | None = None) -> dict[str, Any]:
        """Extract enrichment data from a method using LLM."""
        prompt = self._build_extraction_prompt(method_entry, context=context)

        try:
            response_text = self.llm_client.generate_messages(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=self.max_tokens,
            )
            # Parse JSON from response
            result = self._parse_extraction_response(response_text)

            return {
                "success": True,
                "extraction": result,
                "raw_response": response_text,
            }
        except Exception as e:
            logger.error(f"Extraction failed for method {method_entry.id}: {e}")
            return {
                "success": False,
                "error": str(e),
            }

    @staticmethod
    def _parse_extraction_response(response_text: str) -> dict[str, Any]:
        """Parse JSON from LLM response with robust fallback handling."""
        text = response_text.strip()

        # Normalize markdown fences: strip both ` ``` ` and ` ```json `, case-insensitive
        text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()

        # Try direct parse first
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: extract first {...} block to handle trailing prose
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                raise ValueError("json_parse_error")
            try:
                result = json.loads(match.group(0))
            except json.JSONDecodeError:
                raise ValueError("json_parse_error")

        # Validate structure
        required_keys = {"summary", "business_intent", "risk_notes", "confidence"}
        if not required_keys.issubset(result.keys()):
            raise ValueError(
                f"Missing required keys {required_keys - result.keys()}. Got: {result.keys()}"
            )

        return result
