from search_engine import search as search_module


def test_hybrid_search_accepts_multiple_book_ids(monkeypatch):
    captured = {}

    def fake_run_query(query, params):
        captured["query"] = query
        captured["params"] = params
        return [
            {
                "paragraph_id": "p1",
                "text": "Alpha content",
                "token_count": 50,
                "chunk_hash": "hash-1",
                "dup_cluster_id": None,
                "paragraph_index": 0,
                "chunk_type": "standalone",
                "parent_chunk_id": None,
                "parent_text": None,
                "parent_token_count": None,
                "document_id": "doc-1",
                "book_title": "Doc One",
                "document_keywords": ["alpha"],
                "document_created_at": None,
                "document_total_tokens": 200,
                "uploader_id": None,
                "uploader_name": None,
                "uploader_email": None,
                "section_id": "sec-1",
                "section_title": "Section A",
                "section_level": 1,
                "section_parent_id": None,
                "embedding": [1.0, 0.0],
            },
            {
                "paragraph_id": "p2",
                "text": "Beta content",
                "token_count": 40,
                "chunk_hash": "hash-2",
                "dup_cluster_id": None,
                "paragraph_index": 1,
                "chunk_type": "standalone",
                "parent_chunk_id": None,
                "parent_text": None,
                "parent_token_count": None,
                "document_id": "doc-2",
                "book_title": "Doc Two",
                "document_keywords": ["beta"],
                "document_created_at": None,
                "document_total_tokens": 150,
                "uploader_id": None,
                "uploader_name": None,
                "uploader_email": None,
                "section_id": "sec-2",
                "section_title": "Section B",
                "section_level": 1,
                "section_parent_id": None,
                "embedding": [0.0, 1.0],
            },
        ]

    monkeypatch.setattr(search_module, "run_query", fake_run_query)
    monkeypatch.setattr(search_module, "embed_query", lambda query: (1.0, 0.0))

    results = search_module.hybrid_search(
        query="alpha",
        top_k=5,
        filters={"book_ids": ["doc-1", "doc-2"]},
    )

    assert "d.id IN (%s, %s)" in captured["query"]
    assert captured["params"] == ("doc-1", "doc-2")
    assert {item["document_id"] for item in results} == {"doc-1", "doc-2"}
