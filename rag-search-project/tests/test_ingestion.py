import os
import pytest
from ingest.ingest import clean_text, chunk_paragraphs

def test_clean_text_basic():
    assert clean_text("Hello\x00World") == "HelloWorld"
    assert clean_text("Double  space") == "Double space"

def test_clean_text_ligatures():
    # NFKC normalisation test
    assert clean_text("ﬁnd the ﬂow") == "find the flow"

def test_clean_text_hyphenation():
    # Fix broken words across lines
    text = "tele-\nvision"
    assert clean_text(text) == "television"

def test_chunking_sliding_window():
    # Mock paragraphs
    paras = ["This is a very short paragraph."] * 10
    chunks = chunk_paragraphs(paras)
    assert len(chunks) > 0
    # Overlap logic works
    assert chunks[0].startswith("This is")
