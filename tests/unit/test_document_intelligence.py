import httpx

from apps.api.routers.document_intelligence import extract_requirement_candidates
from services.intelligence.llm import LlmConfig, generate_text, response_text


def test_extracts_cited_requirement_candidates():
    document_id = "3a34f09d-3191-4f76-9ac1-c7721136fb1d"
    candidates = extract_requirement_candidates(
        [
            {
                "document_id": document_id,
                "page_number": 7,
                "text": "Ο ανάδοχος πρέπει να διαθέτει πιστοποιητικό ISO 27001 σε ισχύ. Άσχετη σύντομη γραμμή.",
            }
        ]
    )
    assert len(candidates) == 1
    assert candidates[0]["requirement_type"] == "CERTIFICATE"
    assert candidates[0]["evidence_document_id"] == document_id
    assert candidates[0]["evidence_page"] == 7


def test_response_text_supports_responses_payload():
    assert response_text(
        {"output": [{"content": [{"type": "output_text", "text": "Cited answer [1]."}]}]}
    ) == "Cited answer [1]."


async def test_generate_text_does_not_store_response_data():
    seen: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"output_text": "Answer [1]."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        answer = await generate_text(
            client,
            instructions="Use evidence.",
            input_text="Evidence [1]",
            config=LlmConfig(api_key="secret", model="test-model", endpoint="https://llm.test/v1/responses"),
        )
    assert answer == "Answer [1]."
    assert seen["store"] is False
