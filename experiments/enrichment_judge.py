from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class EnrichmentJudge:
    """
    Quality gate for enrichment. Validates extraction results.
    Decides: accept or reject based on accuracy and completeness.
    """

    def __init__(
        self,
        llm_client,
        model: str = "ollama/gemma2:2b",
        fallback_model: str | None = None,
        max_tokens: int = 600,
    ):
        self.llm_client = llm_client
        self.model = model
        self.fallback_model = fallback_model
        self.max_tokens = max_tokens

    def _build_judge_prompt(self, method_entry, extraction: dict) -> str:
        """Build the prompt for judgment."""
        source = method_entry.source

        return f"""You are a code quality reviewer. Validate this method extraction.

**Method Signature:**
{method_entry.signature}

**Source Code:**
```java
{source}
```

**Extraction Result:**
```json
{{
  "summary": "{extraction.get("summary", "")}",
  "external_calls": {json.dumps(extraction.get("external_calls", []))},
  "implicit_behavior": {json.dumps(extraction.get("implicit_behavior", []))},
  "confidence": {extraction.get("confidence", 0.0)},
  "reasoning": "{extraction.get("reasoning", "")}"
}}
```

**Evaluation Criteria:**
1. **Accuracy**: Does the summary accurately describe what the method does?
   - Is it business-level, not implementation details?
   - Does it mention key inputs/outputs/side effects?

2. **Completeness**: Are external method calls identified?
   - No major missing calls to external classes?
   - No false positives (internal helper calls listed)?

3. **Implicit Behavior**: Important annotations captured?
   - @Transactional, @Async, @Cached, etc.?

4. **Confidence**: Is the confidence score justified?
   - Complex/ambiguous methods should have lower scores
   - Clear methods should be 0.85+

**Judge Decision:**
Respond in JSON format:

{{
  "acceptable": true/false,
  "issues": ["issue1", "issue2"],  # List of problems found
  "confidence": 0.0-1.0,  # Your confidence in this judgment
  "feedback": "Brief explanation of decision (1-2 sentences)",
  "suggestion": "If rejected, how to improve? Otherwise empty string."
}}

Focus on: accuracy, completeness, and usefulness. Reject if:
- Summary is vague or misleading
- Major external calls are missing
- Confidence score is significantly off"""

    async def judge(self, method_entry, extraction: dict) -> dict[str, Any]:
        """Judge extraction quality with fallback to secondary model."""
        prompt = self._build_judge_prompt(method_entry, extraction)
        models_to_try = [self.model]
        if self.fallback_model and self.fallback_model != self.model:
            models_to_try.append(self.fallback_model)

        for model in models_to_try:
            try:
                response_text = self.llm_client.generate_messages(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=self.max_tokens,
                )
                result = self._parse_judge_response(response_text)
                return {
                    "success": True,
                    "judgment": result,
                    "raw_response": response_text,
                    "model_used": model,
                }
            except Exception as e:
                logger.warning(f"Judgment failed with model {model}: {e}")
                if model == models_to_try[-1]:
                    logger.error("All judgment models failed for extraction")
                    return {
                        "success": False,
                        "error": str(e),
                    }
                continue

    @staticmethod
    def _parse_judge_response(response_text: str) -> dict[str, Any]:
        """Parse JSON from judge response with robust fallback handling."""
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
                raise ValueError(f"No JSON object found in response: {text[:300]}")
            result = json.loads(match.group(0))

        # Validate structure
        required_keys = {"acceptable", "confidence", "feedback"}
        if not required_keys.issubset(result.keys()):
            raise ValueError(
                f"Missing required keys {required_keys - result.keys()}. Got: {result.keys()}"
            )

        return result
