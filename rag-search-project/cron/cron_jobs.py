# cron_jobs.py

import logging
import asyncio
from db.db import get_db_connection

CHECK_INTERVAL_HOURS = 24

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CRON] %(levelname)s: %(message)s"
)

def clean_expired_documents():
    logging.info("Starting daily document expiry scan...")

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT document_id 
                    FROM document_files
                    WHERE expiry_date IS NOT NULL
                    AND expiry_date < NOW();
                """)
                expired = cur.fetchall()

                if expired:
                    ids = [row[0] for row in expired]
                    logging.info(f"Found expired: {ids}")

                    cur.execute("""
                        DELETE FROM document_files
                        WHERE expiry_date IS NOT NULL
                        AND expiry_date < NOW();
                    """)

                    logging.info(f"Deleted {cur.rowcount} expired documents.")

                conn.commit()
    except Exception as e:
        logging.error(f"Error during cleanup: {e}")


async def run_cron():
    logging.info("Cron loop started...")

    while True:
        # Run blocking cleanup in a thread
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, clean_expired_documents)

        # Sleep asynchronously (does NOT block FastAPI)
        await asyncio.sleep(CHECK_INTERVAL_HOURS * 3600)
