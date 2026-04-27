from rag.generator import assemble_context, build_rag_prompt, extract_citations


def test_extract_citations_uses_book_title_when_document_name_missing():
    chunks = [
        {"book_title": "Demo PDF", "section_title": "Intro", "text": "First chunk"}
    ]

    citations = extract_citations(chunks)

    assert citations[0]["document_name"] == "Demo PDF"


def test_assemble_context_uses_book_title_when_document_name_missing():
    chunks = [
        {"book_title": "Demo PDF", "section_title": "Intro", "text": "First chunk", "token_count": 20}
    ]

    context = assemble_context(chunks, max_tokens=100)

    assert "Demo PDF" in context


def test_build_rag_prompt_includes_recent_history():
    messages = build_rag_prompt(
        "What does it say?",
        "Retrieved text",
        history=[
            {"role": "user", "content": "Tell me about the policy."},
            {"role": "assistant", "content": "It covers onboarding."},
        ],
    )

    assert messages[1]["role"] == "user"
    assert "Tell me about the policy." in messages[1]["content"]
    assert messages[2]["role"] == "assistant"
    assert "Retrieved context" in messages[-1]["content"]
