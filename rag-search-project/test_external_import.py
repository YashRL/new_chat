import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

print("Testing RAG external import...")

# Test 1: Import
try:
    from search_engine.search import hybrid_search, semantic_search
    print("✅ Test 1 PASSED: Imports successful")
except Exception as e:
    print(f"❌ Test 1 FAILED: {e}")
    sys.exit(1)

# Test 2: DB Connection
try:
    from db.db import get_pool
    pool = get_pool()
    print("✅ Test 2 PASSED: Database connection successful")
except Exception as e:
    print(f"❌ Test 2 FAILED: Database connection error: {e}")
    sys.exit(1)

# Test 3: Embedding
try:
    from embedding_client import create_single_embedding
    vec = create_single_embedding("test query")
    assert len(vec) > 0
    print(f"✅ Test 3 PASSED: Embedding generated, dimension={len(vec)}")
except Exception as e:
    print(f"❌ Test 3 FAILED: Embedding error: {e}")
    sys.exit(1)

# Test 4: Actual Search
try:
    results = hybrid_search(query="test", top_k=2, candidate_pool=20)
    print(f"✅ Test 4 PASSED: Search returned {len(results)} results")
    if results:
        print(f"   Sample result keys: {list(results[0].keys())}")
except Exception as e:
    print(f"❌ Test 4 FAILED: Search error: {e}")
    sys.exit(1)

print("\n✅ ALL TESTS PASSED — RAG system ready for external integration")
