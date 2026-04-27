   # Content Retrieval API - Complete Explanation

This document explains the Content Retrieval API from scratch, covering all components and their interactions.

## Overview

The Content Retrieval API is a backend system built with FastAPI that enables:
- Uploading and processing PDF documents
- Extracting text content with structural awareness (TOC, sections, paragraphs)
- Generating semantic embeddings for content understanding
- Storing documents, chunks, and embeddings in PostgreSQL with pgvector extension
- Performing hybrid search (semantic + lexical + keyword-based)
- User authentication and role-based access control
- Document versioning and deduplication

## System Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌────────────────────┐
│   Client/API    │───▶│   FastAPI App    │───▶│   PostgreSQL DB    │
│  (Web/Mobile)   │    │                  │    │  (with pgvector)   │
└─────────────────┘    │  - Auth Middleware│    │  - Documents       │
                       │  - Route Handlers │    │  - Sections        │
                       │  - Search Engine  │    │  - Paragraphs      │
                       │  - Ingestion Pipe │    │  - Embeddings      │
                       └──────────────────┘    │  - Users/Roles     │
                                               └────────────────────┘
                                                   ▲
                                                   │
                               ┌───────────────────┴───────────────────┐
                               │                                       │
                ┌─────────────────────┐                   ┌─────────────────────┐
                │ Content Extraction  │                   │ Background Workers  │
                │ (PyMuPDF + OCR)     │                   │ (Cron Jobs)         │
                │ - Text extraction   │                   │ - Cleanup tasks     │
                │ - Structure parsing │                   │ - Maintenance       │
                │ - Chunking          │                   │                     │
                └─────────────────────┘                   └─────────────────────┘
```

## Core Components

### 1. API Layer (`api.py`)

The main entry point built with FastAPI that handles:
- **Authentication Middleware**: JWT-based auth with role checking
- **Route Registration**: Organized into routers (auth, admin, user_profile, assessments)
- **Endpoints**:
  - `/ingest`: Upload and process PDF documents
  - `/books`: List and manage documents
  - `/search`: Hybrid search functionality
  - `/documents/{id}/download`: Download stored documents
  - Health check and utility endpoints

### 2. Ingestion Pipeline (`ingest/ingest.py`)

Handles the complete document processing workflow:
1. **File Validation & Storage**: Saves uploaded PDF to disk
2. **Text Extraction**: Uses PyMuPDF to extract text with OCR fallback
3. **Structure Detection**: Identifies TOC, sections, and hierarchical structure
4. **Text Chunking**: Splits content into semantically meaningful chunks (500-800 tokens)
   5. **Embedding Generation**: Creates vector embeddings using NVIDIA's `nv-embedqa-e5-v5` (1024-dim)
6. **Database Storage**: Persists documents, sections, chunks, and embeddings to PostgreSQL
7. **File Storage**: Compresses and stores original PDF in `document_files` table
8. **Cleanup**: Removes temporary files after processing

Key features:
- Duplicate detection using content hashing
- Version control for document updates
- Visibility controls (public/private)
- Batch processing for efficiency
- Error handling with rollback capabilities

### 3. Search Engine (`search_engine/search.py`)

Implements advanced hybrid search combining multiple signals:
- **Semantic Search**: Vector similarity using embeddings
- **Lexical Search**: Full-text search with BM25 ranking (PostgreSQL tsvector)
- **Keyword Matching**: Exact keyword matching from document metadata
- **Contextual Boosting**: Boosts results from same section/document
- **Quality Signals**: Favors well-developed documents with metadata
- **Deduplication**: Avoids duplicate content in results
- **Query Expansion**: Enhances short queries with related terms

Search types:
- `hybrid`: Combines all signals intelligently (default)
- `semantic`: Prioritizes semantic similarity
- `lexical`: Focuses on exact term matching
- `book`: Search within specific book
- `section`: Search within specific section
- `keywords`: Match by document keywords only

### 4. Database Layer (`db/db.py`)

Manages PostgreSQL interactions:
- **Connection Pooling**: Efficient database connections
- **Migration System**: Schema versioning and updates
- **Query Helpers**: Simplified query execution
- **Initial Schema**: Creates all necessary tables with pgvector extension

Database schema highlights:
- `documents`: Stores document metadata and file references
- `sections`: Hierarchical document structure (TOC-like)
- `paragraphs`: Atomic content chunks with text and metadata
- `embeddings`: Vector representations of paragraphs (1024-dim, NVIDIA nv-embedqa-e5-v5)
- `users/roles`: Authentication and authorization system
- `document_files`: Binary storage for original PDFs (compressed)

### 5. Content Extraction (`content_extraction/`)

Handles converting files to structured text:
- **PDF Processing**: Uses PyMuPDF for text extraction
- **OCR Integration**: Falls back to OCR for scanned documents
- **TOC Parsing**: Extracts table of contents for structure
- **Legacy Support**: Handles non-PDF files (txt, docx)
- **Chunking Strategy**: Smart paragraph-based chunking with overlap

### 6. Authentication System (`auth/`)

Manages user identity and access:
- **JWT Tokens**: Stateless authentication with expiration
- **Role-Based Access**: Admin/User/Guest roles
- **Password Handling**: Secure password storage (hashed)
- **Session Management**: Stateless JWT-based sessions

## Data Flow

### Document Ingestion Flow
1. User uploads PDF via `/ingest` endpoint
2. API saves file to `uploaded_pdfs/` directory
3. Ingestion pipeline processes the file:
   - Extracts text using PyMuPDF
   - Applies OCR if needed (scanned pages)
   - Parses document structure (TOC, sections)
   - Creates hierarchical outline
   - Splits content into chunks (500-800 tokens with overlap)
   - Generates embeddings for each chunk
4. Data stored in PostgreSQL:
   - `documents` table: Metadata and file reference
   - `sections` table: Hierarchical structure
   - `paragraphs` table: Text chunks with indexing
   - `embeddings` table: Vector representations
   - `document_files` table: Compressed original PDF
5. Temporary files cleaned up
6. Returns document ID to user

### Search Flow
1. User submits search query via `/search` endpoint
2. Search engine analyzes query:
   - Classifies query type (short/long, technical, question)
   - Selects appropriate weight profile
   - Generates query embedding (if semantic search)
3. Database query executed:
   - Retrieves candidate paragraphs using vector similarity
   - Applies filters (book, section, date, etc.)
   - Scores candidates using hybrid approach:
     - Semantic score: Cosine similarity of embeddings
     - Lexical score: BM25 full-text match
     - Keyword score: Metadata keyword overlap
     - Context score: Section coherence boost
     - Quality score: Document quality signals
4. Results ranked and returned with detailed scoring breakdown

### Authentication Flow
1. Request arrives at API
2. Middleware checks if path requires auth
3. For protected routes:
   - Extracts JWT from Authorization header
   - Validates token signature and expiration
   - Looks up user in database
   - Attaches user info to request state
4. Route handler accesses user info via `request.state`

## Key Technologies

- **Framework**: FastAPI (async Python web framework)
- **Database**: PostgreSQL with pgvector extension
- **Search**: Hybrid approach combining:
  - Vector similarity (cosine distance)
  - Full-text search (tsvector + BM25)
  - Exact matching (keyword arrays)
- **Embeddings**: NVIDIA `nv-embedqa-e5-v5` (1024 dimensions) via `embedding_client.py`
- **Text Processing**: PyMuPDF (PDF), python-docx (Word), regex/nltk (text)
- **OCR**: Tesseract-based OCR for scanned documents
- **Authentication**: JWT (JSON Web Tokens) with HS256 signing
- **Deployment**: Docker containerization supported

## Configuration

Key configuration points:
- **Database**: Set via environment variables (PGHOST, PGPORT, etc.)
- **NVIDIA API Key**: Required for embeddings (set `NVIDIA_API_KEY`)
- **JWT Secret**: Set SECRET_KEY in api.py (should be env var in prod)
- **Upload Directory**: Configured via UPLOAD_DIR constant
- **CORS**: Currently allows all origins (restrict in production)
- **Weight Profiles**: Tunable in search_engine/search.py

## Extending the System

### Adding New File Types
1. Modify `content_extraction/data_extractor.py`
2. Add handler in `extract_text_from_file()` function
3. Update `legacy_books_extraction()` if needed for metadata

### Customizing Search
1. Adjust weight profiles in `WEIGHT_PROFILES` dict
2. Modify scoring factors in hybrid_search() function
3. Add new filter types to WHERE clause construction
4. Implement new search type functions

### Changing Chunking Strategy
1. Modify `chunk_paragraphs()` in ingest/ingest.py
2. Adjust BASE_TOKENS, MIN_TOKENS, MAX_TOKENS, OVERLAP constants
3. Update overlap handling logic if needed

### Adding New Auth Features
1. Extend auth/auth.py routers
2. Add new role/permission checks in middleware
3. Update database schema via migrations
4. Modify frontend/API contracts as needed

## Deployment Considerations

### Environment Variables
```bash
# Database
PGHOST=your-postgres-host
PGPORT=5432
PGDATABASE=your_db
PGUSER=your_user
PGPASSWORD=your_password

# Security
SECRET_KEY=your_jwt_secret_key

# External Services
NVIDIA_API_KEY=your_nvidia_api_key
NVIDIA_EMBEDDING_MODEL=nvidia/nv-embedqa-e5-v5

# Application
UPLOAD_DIR=uploaded_pdfs
```

### Scaling
- **Horizontal**: Run multiple API instances behind load balancer
- **Database**: Use read replicas for search queries
- **Cache**: Add Redis for frequent query results
- **Storage**: Use object storage (S3) for PDF files instead of DB
- **Workers**: Offload ingestion to background workers (Celery/RQ)

### Monitoring
- Health check endpoint (`/health`)
- Logging configured throughout (INFO level)
- Consider adding:
  - Prometheus metrics
  - Request tracing
  - Error tracking (Sentry)
  - Performance monitoring

## Security Features

1. **Authentication**: JWT-based with expiration
2. **Authorization**: Role-based access control
3. **Input Validation**: FastAPI automatic validation
4. **SQL Injection**: Parameterized queries throughout
5. **File Safety**: 
   - File type validation
   - Size limits (configure at web server level)
   - Virus scanning recommended for production
6. **Data Protection**:
   - Passwords hashed (not shown in snippet but implied)
   - Sensitive data encryption consideration
   - GDPR-compliant data deletion

## Limitations and Future Improvements

### Current Limitations
1. **NVIDIA API Dependency**: Requires NVIDIA API key for embeddings, reranking, and HyDE LLM
2. **Single Language**: Primarily optimized for English
3. **Resource Intensive**: Embedding generation can be slow/costly
4. **Storage**: PDFs stored in DB may bloat over time
5. **Real-time Updates**: No WebSocket for live updates

### Planned Enhancements
1. **Additional Embedding Options**: Support for open-source or local embedding models if NVIDIA is unavailable
2. **Multi-language Support**: Language detection and appropriate models
3. **Improved Caching**: Multi-level caching strategy
4. **Object Storage**: Move PDFs to S3/minio for better scalability
5. **WebSocket Support**: Real-time collaboration features
6. **Analytics**: Usage tracking and search analytics
7. **Advanced Features**:
   - Query understanding and expansion
   - Faceted search and filtering
   - Recommendations based on user behavior
   - Export capabilities (PDF, DOCX, etc.)

## Getting Started

1. **Prerequisites**:
   - Python 3.8+
   - PostgreSQL 12+ with pgvector extension
   - NVIDIA API key (get from build.nvidia.com)
   - Tesseract OCR (for scanned documents)

2. **Installation**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Database Setup**:
   ```bash
   # Create database and user
   # Run migrations
   python db.py migrate
   ```

4. **Configuration**:
   - Set environment variables
   - Adjust secrets in code (move to env vars for production)

5. **Run**:
   ```bash
   uvicorn api:app --host 0.0.0.0 --port 8000
   ```

6. **Test**:
   - Visit http://localhost:8000/docs for interactive API documentation
   - Try uploading a PDF via /ingest endpoint
   - Search content via /search endpoint

## Conclusion

This Content Retrieval API provides a robust foundation for document management and semantic search capabilities. Its modular design allows for easy extension and customization while maintaining high performance through intelligent caching, efficient database design, and hybrid search algorithms. The system balances functionality with maintainability, making it suitable for both small teams and enterprise deployment with appropriate scaling considerations.