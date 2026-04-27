# jobs/processor.py

import os
import time
import uuid
import json
import logging
import asyncio
import threading
from typing import Dict, Any, Optional, Callable
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

job_status: Dict[str, Dict[str, Any]] = {}
_job_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=4)

def create_job_id() -> str:
    return str(uuid.uuid4())

def register_job(job_id: str, job_type: str, metadata: Optional[Dict] = None):
    with _job_lock:
        job_status[job_id] = {
            "job_id": job_id,
            "job_type": job_type,
            "status": "queued",
            "progress": 0,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "metadata": metadata or {},
            "result": None,
            "error": None,
        }

def update_job_status(job_id: str, status: str, progress: Optional[int] = None, result: Optional[Any] = None, error: Optional[str] = None):
    with _job_lock:
        if job_id not in job_status:
            raise ValueError(f"Job {job_id} not found")
        
        job_status[job_id]["status"] = status
        job_status[job_id]["updated_at"] = datetime.utcnow().isoformat()
        
        if progress is not None:
            job_status[job_id]["progress"] = progress
        if result is not None:
            job_status[job_id]["result"] = result
        if error is not None:
            job_status[job_id]["error"] = error

def get_job_status(job_id: str) -> Optional[Dict[str, Any]]:
    with _job_lock:
        return job_status.get(job_id)

def get_active_jobs() -> list:
    with _job_lock:
        return [
            {"job_id": k, "status": v["status"], "progress": v["progress"], "created_at": v["created_at"]}
            for k, v in job_status.items()
            if v["status"] in ["queued", "processing"]
        ]

def cleanup_job(job_id: str):
    with _job_lock:
        if job_id in job_status:
            del job_status[job_id]

def process_document_sync(job_id: str, file_path: str, title: str, doc_type: str, 
                          keywords: list, created_by: str, visibility: dict,
                          on_progress: Optional[Callable] = None):
    from ingest.ingest import ingest
    
    try:
        update_job_status(job_id, "processing", progress=10)
        
        if on_progress:
            on_progress(10, "Extracting text from PDF...")
        
        document_id = ingest(
            file_path=file_path,
            keywords=keywords,
            doc_type=doc_type,
            override_title=title,
            created_by=created_by,
            updated_by=created_by,
            visibility=visibility,
        )
        
        update_job_status(job_id, "completed", progress=100, result={
            "document_id": str(document_id),
            "title": title,
        })
        
        logger.info(f"Job {job_id} completed successfully")
        return document_id
        
    except Exception as e:
        logger.error(f"Job {job_id} failed: {str(e)}")
        update_job_status(job_id, "failed", error=str(e))
        raise

async def process_document_async(job_id: str, file_path: str, title: str, doc_type: str,
                                  keywords: list, created_by: str, visibility: dict,
                                  on_progress: Optional[Callable] = None):
    loop = asyncio.get_event_loop()
    
    def on_progress_sync(progress: int, message: str):
        update_job_status(job_id, "processing", progress=progress)
        if on_progress:
            on_progress(progress, message)
    
    future = loop.run_in_executor(
        _executor,
        process_document_sync,
        job_id, file_path, title, doc_type, keywords, created_by, visibility, on_progress_sync
    )
    
    try:
        result = await future
        return result
    except Exception as e:
        logger.error(f"Job {job_id} failed: {str(e)}")
        raise
