import logging

from . import db
from .config import settings

log = logging.getLogger("hoomanise.credits")


class InsufficientCredits(Exception):
    def __init__(self, granted: int, used: int, needed: int):
        self.granted, self.used, self.needed = granted, used, needed
        super().__init__(f"credits exhausted ({used}/{granted}, needed {needed})")


def charge(user_id: str, api_key_id: str | None, job_id: str | None, endpoint: str,
           credits: int, status: str = "reserved", input_words: int | None = None) -> dict:
    """Atomically check the balance and record the charge.

    Locks the user row for the duration so concurrent requests cannot all pass the
    check before any charge exists. Idempotent per job_id (unique partial index).
    """
    with db.get_pool().connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT free_credits FROM users WHERE id = %s FOR UPDATE", (user_id,))
            row = cur.fetchone()
            granted = row[0] if row else settings.free_credits_default
            cur.execute(
                "SELECT COALESCE(SUM(credits_used), 0) FROM usage_records WHERE user_id = %s",
                (user_id,),
            )
            used = cur.fetchone()[0]
            if used + credits > granted:
                conn.rollback()
                raise InsufficientCredits(granted, used, credits)
            cur.execute(
                "INSERT INTO usage_records (user_id, api_key_id, job_id, endpoint, status, input_words, credits_used)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (user_id, api_key_id, job_id, endpoint, status, input_words, credits),
            )
            inserted = cur.rowcount
        conn.commit()
    return {"granted": granted, "used_before": used, "charged": inserted == 1}


def settle(job_id: str, *, status: str, input_words: int | None, output_words: int | None,
           score_before: float | None, score_after: float | None) -> None:
    db.execute(
        "UPDATE usage_records SET status = %s, input_words = %s, output_words = %s,"
        " ai_score_before = %s, ai_score_after = %s WHERE job_id = %s",
        (status, input_words, output_words, score_before, score_after, job_id),
    )


def ensure_completed(user_id: str, api_key_id: str | None, job_id: str, endpoint: str,
                     credits: int, input_words: int | None) -> None:
    """For paths that skip the worker reservation (e.g. cache hits): charge if absent."""
    charge(user_id, api_key_id, job_id, endpoint, credits, status="completed", input_words=input_words)
    settle(job_id, status="completed", input_words=input_words, output_words=None,
           score_before=None, score_after=None)


def refund(job_id: str) -> None:
    db.execute(
        "UPDATE usage_records SET credits_used = 0, status = 'refunded' WHERE job_id = %s",
        (job_id,),
    )
