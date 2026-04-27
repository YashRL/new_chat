import pytest
from rag.generator import assemble_context, extract_citations

def test_extract_citations():
    chunks = [
        {"document_name": "Doc A", "section_title": "Sec 1", "text": "Content A"},
        {"document_name": "Doc B", "section_title": "Sec 2", "text": "Content B"}
    ]
    citations = extract_citations(chunks)
    assert len(citations) == 2
    assert citations[0]["citation_id"] == 1
    assert citations[0]["document_name"] == "Doc A"

def test_assemble_context():
    chunks = [
        {"document_name": "Doc A", "section_title": "Sec 1", "text": "Content 1", "token_count": 100},
        {"document_name": "Doc B", "section_title": "Sec 2", "text": "Content 2", "token_count": 2000}
    ]
    
    # Should fit both with default 3000 max tokens
    ctx_full = assemble_context(chunks, max_tokens=3000)
    assert "Doc A" in ctx_full
    assert "Doc B" in ctx_full
    assert "[Citation 1]" in ctx_full
    assert "[Citation 2]" in ctx_full
    
    # Should clip the second one to fit under budget
    ctx_clipped = assemble_context(chunks, max_tokens=150)
    assert "Doc A" in ctx_clipped
    assert "Doc B" not in ctx_clipped
    assert "[Citation 2]" not in ctx_clipped
