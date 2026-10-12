import json

import httpx
import pytest

from app.ai import LLMClient, SummaryError, extractive_summary, validate_output

GOOD = {"headline_en": "RBI holds rates", "detail_en": "The central bank kept the repo rate unchanged.",
        "headline_hi": "आरबीआई ने दरें स्थिर रखीं", "detail_hi": "केंद्रीय बैंक ने रेपो दर अपरिवर्तित रखी।",
        "category": "business", "claim_type": "reported", "needs_review": False}


def test_validate_accepts_well_formed_output():
    assert validate_output(dict(GOOD))["category"] == "business"


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("headline_hi"),
    lambda d: d.update(category="made_up"),
    lambda d: d.update(claim_type="fact_checked"),
    lambda d: d.update(needs_review="yes"),
    lambda d: d.update(detail_hi=""),
])
def test_validate_rejects_bad_output(mutate):
    d = dict(GOOD)
    mutate(d)
    with pytest.raises(SummaryError):
        validate_output(d)


def test_llm_client_parses_structured_json_and_marks_generator():
    def handler(req):
        body = json.loads(req.content)
        # Article text must be quoted as data inside <article> tags, with the injection guard in the system prompt.
        assert "<article>" in body["messages"][1]["content"]
        assert "UNTRUSTED" in body["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]})

    c = LLMClient("K", "https://llm.example/v1", "m", http=httpx.Client(transport=httpx.MockTransport(handler)))
    out = c.summarize("t", "The Hindu", "https://www.thehindu.com/a", "body")
    assert out["generator"] == "llm" and out["model"] == "m"


def test_llm_malformed_json_raises_so_caller_can_fallback():
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    c = LLMClient("K", "https://llm.example/v1", "m", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(SummaryError):
        c.summarize("t", "p", "https://x.com/a", "body")


def test_extractive_fallback_is_flagged_for_review_and_has_no_hindi():
    out = extractive_summary("Title", "First sentence. Second sentence. Third sentence.")
    assert out["generator"] == "extractive_fallback"
    assert out["needs_review"] is True
    assert out["headline_hi"] is None
    assert "Third" not in out["detail_en"]
