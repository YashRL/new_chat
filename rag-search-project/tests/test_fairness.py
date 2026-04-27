"""
Fairness tests for the two-stage ranking engine.

These tests work entirely in-process with no database or embedding API calls.
They exercise the pure scoring/grouping logic directly so they are fast,
deterministic, and never flaky.

Run with:
    python -m pytest tests/test_fairness.py -v
"""

import math
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from search_engine.search import (
    _title_match_score,
    _keyword_match_score,
    _size_fairness_factor,
    _score_chunk,
    _score_document,
    FAIRNESS_LARGE_DOC_THRESHOLD,
    FAIRNESS_SMALL_DOC_THRESHOLD,
    FAIRNESS_LARGE_PENALTY,
    FAIRNESS_SMALL_BONUS,
    DOC_BEST_CHUNK_W,
    DOC_SECOND_CHUNK_W,
    DOC_TITLE_BOOST_W,
    DOC_KW_BOOST_W,
    MAX_CHUNKS_PER_DOC,
    MAX_CHUNKS_PER_SECTION,
)

BALANCED = {"semantic": 0.50, "lexical": 0.30, "keywords": 0.10, "context": 0.10}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_chunk(sem, lex, kw, section_id="s1", weights=None):
    w = weights or BALANCED
    hits = {}
    score, ctx = _score_chunk(sem, lex, kw, section_id, hits, w, enable_context_boost=True)
    return score


def doc_score(chunk_scores, chunk_count, title_boost=0.0, kw_boost=0.0):
    return _score_document(chunk_scores, chunk_count, title_boost, kw_boost)


# ---------------------------------------------------------------------------
# Unit: _title_match_score
# ---------------------------------------------------------------------------

def test_title_match_exact():
    assert _title_match_score("machine learning", "machine learning") == 1.0

def test_title_match_partial():
    s = _title_match_score("Introduction to Machine Learning", "machine learning")
    assert 0.0 < s <= 1.0

def test_title_match_no_overlap():
    assert _title_match_score("Cooking Recipes", "machine learning") == 0.0

def test_title_match_empty():
    assert _title_match_score(None, "query") == 0.0
    assert _title_match_score("title", None) == 0.0


# ---------------------------------------------------------------------------
# Unit: _keyword_match_score
# ---------------------------------------------------------------------------

def test_kw_match_full():
    assert _keyword_match_score(["ai", "ml"], ["ai", "ml"]) == 1.0

def test_kw_match_partial():
    s = _keyword_match_score(["ai", "ml", "nlp"], ["ai", "deep learning"])
    assert 0.0 < s < 1.0

def test_kw_match_none():
    assert _keyword_match_score(["cooking"], ["ai", "ml"]) == 0.0

def test_kw_match_empty_query():
    assert _keyword_match_score(["ai", "ml"], []) == 0.0


# ---------------------------------------------------------------------------
# Unit: _size_fairness_factor
# ---------------------------------------------------------------------------

def test_small_doc_gets_bonus():
    f = _size_fairness_factor(5)
    assert f > 0, "small doc should get a positive bonus"
    assert f <= FAIRNESS_SMALL_BONUS

def test_large_doc_gets_penalty():
    f = _size_fairness_factor(500)
    assert f < 0, "large doc should get a negative penalty"
    assert f >= -FAIRNESS_LARGE_PENALTY

def test_medium_doc_neutral():
    f = _size_fairness_factor(100)
    assert f == 0.0

def test_fairness_is_mild():
    """The fairness factor must never exceed ±0.05 so relevance always dominates."""
    for n in [1, 5, 10, 50, 100, 200, 500, 1000, 5000]:
        f = _size_fairness_factor(n)
        assert abs(f) <= 0.05, f"fairness too large for chunk_count={n}: {f}"

def test_fairness_monotone():
    """Larger docs should not get more bonus than smaller ones."""
    prev = _size_fairness_factor(1)
    for n in [5, 10, 20, 50, 100, 200, 500, 1000]:
        curr = _size_fairness_factor(n)
        assert curr <= prev + 1e-9, f"fairness not monotone at n={n}: {curr} > {prev}"
        prev = curr


# ---------------------------------------------------------------------------
# Unit: _score_chunk — no size bonus, only relevance signals
# ---------------------------------------------------------------------------

def test_chunk_score_high_sem():
    score = make_chunk(sem=0.95, lex=0.0, kw=0.0)
    assert score > 0.4, "high semantic should yield strong chunk score"

def test_chunk_score_high_lex():
    score = make_chunk(sem=0.0, lex=0.95, kw=0.0)
    assert score > 0.2

def test_chunk_score_zero():
    score = make_chunk(sem=0.0, lex=0.0, kw=0.0)
    assert score == 0.0

def test_context_boost_capped():
    """Context boost must never grow above 0.15 no matter how many section hits."""
    hits = {}
    for i in range(20):
        hits["sec_x"] = i
        _, ctx = _score_chunk(0.5, 0.3, 0.1, "sec_x", hits, BALANCED, True)
    assert ctx <= 0.15, f"context boost exceeded cap: {ctx}"

def test_context_boost_per_section():
    """Context boost in section A must not affect section B."""
    hits = {"sec_a": 10}
    _, ctx_b = _score_chunk(0.5, 0.3, 0.1, "sec_b", hits, BALANCED, True)
    assert ctx_b == 0.0, "context from sec_a should not bleed into sec_b"


# ---------------------------------------------------------------------------
# Unit: _score_document
# ---------------------------------------------------------------------------

def test_doc_score_single_chunk():
    s = doc_score([0.8], chunk_count=5)
    expected = DOC_BEST_CHUNK_W * 0.8 + _size_fairness_factor(5)
    assert abs(s - expected) < 1e-5

def test_doc_score_two_chunks():
    s = doc_score([0.8, 0.6], chunk_count=5)
    expected = DOC_BEST_CHUNK_W * 0.8 + DOC_SECOND_CHUNK_W * 0.6 + _size_fairness_factor(5)
    assert abs(s - expected) < 1e-5

def test_doc_score_title_boost():
    no_title = doc_score([0.5], chunk_count=50, title_boost=0.0)
    with_title = doc_score([0.5], chunk_count=50, title_boost=1.0)
    assert with_title > no_title

def test_doc_score_kw_boost():
    no_kw = doc_score([0.5], chunk_count=50, kw_boost=0.0)
    with_kw = doc_score([0.5], chunk_count=50, kw_boost=1.0)
    assert with_kw > no_kw


# ---------------------------------------------------------------------------
# ACCEPTANCE TEST 1
# Small highly-relevant file must beat a large weakly-relevant file
# ---------------------------------------------------------------------------

def test_small_beats_large_when_more_relevant():
    """
    Small doc: 8 chunks, best chunk score 0.92 (very relevant).
    Large doc: 400 chunks, best chunk score 0.55 (weakly relevant).
    Small doc must win.
    """
    small_score = doc_score([0.92, 0.85], chunk_count=8, title_boost=0.5, kw_boost=0.5)
    large_score = doc_score([0.55, 0.50], chunk_count=400, title_boost=0.0, kw_boost=0.0)

    assert small_score > large_score, (
        f"Small doc ({small_score:.4f}) should beat large doc ({large_score:.4f})"
    )


# ---------------------------------------------------------------------------
# ACCEPTANCE TEST 2
# Large doc with much stronger relevance still wins
# ---------------------------------------------------------------------------

def test_large_wins_when_clearly_more_relevant():
    """
    Large doc: 400 chunks, best chunk score 0.95 (very relevant).
    Small doc: 5 chunks,   best chunk score 0.40 (barely relevant).
    Large doc must win — relevance beats size advantage.
    """
    large_score = doc_score([0.95, 0.90], chunk_count=400, title_boost=0.0, kw_boost=0.0)
    small_score = doc_score([0.40, 0.35], chunk_count=5,   title_boost=0.0, kw_boost=0.0)

    assert large_score > small_score, (
        f"Large doc ({large_score:.4f}) should beat small doc ({small_score:.4f}) "
        f"when its chunks are far more relevant"
    )


# ---------------------------------------------------------------------------
# ACCEPTANCE TEST 3
# Title + keyword match helps small doc overcome a moderately-relevant large doc
# ---------------------------------------------------------------------------

def test_title_kw_boost_helps_small_doc():
    """
    Small doc: 6 chunks, chunk score 0.70, perfect title + keyword match.
    Large doc: 350 chunks, chunk score 0.75, no title/keyword match.
    The small doc's boosts should close or exceed the gap.
    """
    small_score = doc_score([0.70], chunk_count=6,   title_boost=1.0, kw_boost=1.0)
    large_score = doc_score([0.75], chunk_count=350, title_boost=0.0, kw_boost=0.0)

    assert small_score >= large_score * 0.95, (
        f"Small doc with title+kw boosts ({small_score:.4f}) should be "
        f"competitive with large doc ({large_score:.4f})"
    )


# ---------------------------------------------------------------------------
# ACCEPTANCE TEST 4
# Diversity: simulate final selection and assert per-doc cap
# ---------------------------------------------------------------------------

def _simulate_selection(doc_entries, top_k):
    """
    Simplified version of the final selection loop in hybrid_search.
    doc_entries: list of {"doc_id", "doc_score", "chunks": [{"chunk_score", "section_id"}]}
    """
    doc_entries = sorted(doc_entries, key=lambda x: x["doc_score"], reverse=True)
    final = []
    doc_counts = {}
    sec_counts = {}

    for entry in doc_entries:
        if len(final) >= top_k:
            break
        doc_id = entry["doc_id"]
        for ch in sorted(entry["chunks"], key=lambda x: x["chunk_score"], reverse=True):
            if len(final) >= top_k:
                break
            if doc_counts.get(doc_id, 0) >= MAX_CHUNKS_PER_DOC:
                break
            sid = ch.get("section_id")
            if sid and sec_counts.get(sid, 0) >= MAX_CHUNKS_PER_SECTION:
                continue
            doc_counts[doc_id] = doc_counts.get(doc_id, 0) + 1
            if sid:
                sec_counts[sid] = sec_counts.get(sid, 0) + 1
            final.append({"doc_id": doc_id, "chunk_score": ch["chunk_score"]})
    return final


def test_per_doc_cap_enforced():
    """One document with 20 chunks must not take more than MAX_CHUNKS_PER_DOC slots."""
    big_doc_chunks = [{"chunk_score": 0.9 - i * 0.01, "section_id": f"s{i}"} for i in range(20)]
    entries = [
        {"doc_id": "big",   "doc_score": 0.8, "chunks": big_doc_chunks},
        {"doc_id": "small", "doc_score": 0.3, "chunks": [{"chunk_score": 0.4, "section_id": "sx"}]},
    ]
    selected = _simulate_selection(entries, top_k=10)
    big_used = sum(1 for r in selected if r["doc_id"] == "big")
    assert big_used <= MAX_CHUNKS_PER_DOC, (
        f"big doc used {big_used} slots, limit is {MAX_CHUNKS_PER_DOC}"
    )


def test_multiple_docs_represented():
    """With 5 equal-score documents, all 5 must appear in top-10 results."""
    entries = []
    for i in range(5):
        entries.append({
            "doc_id": f"doc_{i}",
            "doc_score": 0.7,
            "chunks": [
                {"chunk_score": 0.7, "section_id": f"sec_{i}_a"},
                {"chunk_score": 0.6, "section_id": f"sec_{i}_b"},
                {"chunk_score": 0.5, "section_id": f"sec_{i}_c"},
            ],
        })
    selected = _simulate_selection(entries, top_k=10)
    represented = {r["doc_id"] for r in selected}
    assert len(represented) == 5, (
        f"Expected all 5 docs represented, got {len(represented)}: {represented}"
    )


def test_per_section_cap_enforced():
    """No more than MAX_CHUNKS_PER_SECTION chunks from the same section."""
    same_section_chunks = [{"chunk_score": 0.9 - i * 0.01, "section_id": "same_sec"} for i in range(10)]
    entries = [{"doc_id": "doc_a", "doc_score": 0.9, "chunks": same_section_chunks}]
    selected = _simulate_selection(entries, top_k=10)
    sec_used = sum(1 for r in selected if r["doc_id"] == "doc_a")
    assert sec_used <= MAX_CHUNKS_PER_SECTION, (
        f"section used {sec_used} slots, limit is {MAX_CHUNKS_PER_SECTION}"
    )


# ---------------------------------------------------------------------------
# EXAMPLE CALCULATIONS (printed, not asserted — for documentation)
# ---------------------------------------------------------------------------

def test_example_calculations(capsys):
    """Print worked examples matching the spec requirement."""
    print("\n=== EXAMPLE CALCULATIONS ===\n")

    small_chunk = make_chunk(sem=0.92, lex=0.60, kw=0.80)
    large_chunk = make_chunk(sem=0.55, lex=0.30, kw=0.10)

    small_doc = doc_score([small_chunk], chunk_count=8, title_boost=0.7, kw_boost=0.8)
    large_doc = doc_score([large_chunk, large_chunk * 0.95], chunk_count=400)

    print(f"Small highly-relevant file:")
    print(f"  chunk_score  = {small_chunk:.4f}  (sem=0.92 lex=0.60 kw=0.80)")
    print(f"  chunk_count  = 8")
    print(f"  title_boost  = 0.70,  kw_boost = 0.80")
    print(f"  fairness     = {_size_fairness_factor(8):+.5f}")
    print(f"  doc_score    = {small_doc:.4f}")
    print()
    print(f"Large weakly-relevant file:")
    print(f"  chunk_score  = {large_chunk:.4f}  (sem=0.55 lex=0.30 kw=0.10)")
    print(f"  chunk_count  = 400")
    print(f"  title_boost  = 0.00,  kw_boost = 0.00")
    print(f"  fairness     = {_size_fairness_factor(400):+.5f}")
    print(f"  doc_score    = {large_doc:.4f}")
    print()
    print(f"Winner: {'SMALL (correct)' if small_doc > large_doc else 'LARGE (unexpected)'}")

    assert small_doc > large_doc


# ---------------------------------------------------------------------------
# VERIFICATION CHECKLIST
# ---------------------------------------------------------------------------

def test_verification_checklist():
    """
    Final checklist — all must pass.
    """
    results = {}

    # 1. Small file with better match wins
    s_small = doc_score([0.88], chunk_count=10, title_boost=0.8, kw_boost=0.8)
    s_large = doc_score([0.60, 0.55], chunk_count=300, title_boost=0.0, kw_boost=0.0)
    results["small_beats_large_when_relevant"] = s_small > s_large

    # 2. Large clearly-better doc wins
    s_big   = doc_score([0.95, 0.93], chunk_count=300, title_boost=0.0, kw_boost=0.0)
    s_tiny  = doc_score([0.35], chunk_count=3, title_boost=0.0, kw_boost=0.0)
    results["large_wins_when_clearly_better"] = s_big > s_tiny

    # 3. Fairness factor is mild (never > 0.05)
    results["fairness_is_mild"] = all(
        abs(_size_fairness_factor(n)) <= 0.05
        for n in [1, 5, 20, 100, 200, 500, 2000]
    )

    # 4. No size bonus — large doc with zero relevance scores near zero
    s_irrelevant_large = doc_score([0.0, 0.0], chunk_count=1000)
    results["no_size_bonus_for_irrelevant_large"] = s_irrelevant_large < 0.01

    # 5. Title + keyword boosts are meaningful
    no_boost  = doc_score([0.5], chunk_count=50)
    with_boost = doc_score([0.5], chunk_count=50, title_boost=1.0, kw_boost=1.0)
    results["title_kw_boosts_meaningful"] = with_boost > no_boost + 0.05

    # 6. Context boost capped per section
    hits = {"s": 100}
    _, ctx = _score_chunk(0.5, 0.3, 0.1, "s", hits, BALANCED, True)
    results["context_boost_capped"] = ctx <= 0.15

    # 7. Diversity: per-doc cap
    big_chunks = [{"chunk_score": 0.9, "section_id": f"s{i}"} for i in range(20)]
    sel = _simulate_selection(
        [{"doc_id": "x", "doc_score": 0.9, "chunks": big_chunks}], top_k=10
    )
    results["per_doc_cap"] = len(sel) <= MAX_CHUNKS_PER_DOC

    failed = [k for k, v in results.items() if not v]
    assert not failed, f"Checklist failures: {failed}\nAll results: {results}"
    print(f"\nVerification checklist: all {len(results)} checks passed.")


