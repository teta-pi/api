"""TWIRA + Temporal Moat background jobs (SystemSpec v2.1 §02/§03).

- ots_lifecycle: every 30 min — stamp pending events, upgrade anchored proofs,
  mark confirmed with btc_block.
- recompute_scores: nightly (and after new confirmed events) — refresh
  businesses.t_score / p_score. Idempotent.
- probe_endpoints: every 30 min — hit declared agent endpoints, write results
  to endpoint_probes.
"""

import asyncio
import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="twira_recompute_scores")
def recompute_scores() -> dict:
    return asyncio.get_event_loop().run_until_complete(_recompute_async())


async def _recompute_async() -> dict:
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.business import Business
    from app.models.verification_event import VerificationEvent
    from app.twira.provenance import provenance_score
    from app.twira.trust import trust_score

    updated = 0
    async with AsyncSessionLocal() as db:
        entity_ids = (await db.execute(select(Business.id))).scalars().all()
        for eid in entity_ids:
            events = (
                await db.execute(
                    select(VerificationEvent).where(VerificationEvent.entity_id == eid)
                )
            ).scalars().all()
            t = trust_score(list(events))
            p = await provenance_score(db, eid)
            biz = (await db.execute(select(Business).where(Business.id == eid))).scalar_one()
            biz.t_score = t
            biz.p_score = p
            updated += 1
        await db.commit()
    logger.info("TWIRA recompute: %s entities updated", updated)
    return {"updated": updated}


@celery_app.task(name="ots_lifecycle")
def ots_lifecycle() -> dict:
    return asyncio.get_event_loop().run_until_complete(_ots_lifecycle_async())


async def _ots_lifecycle_async() -> dict:
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.verification_event import VerificationEvent
    from app.services.bitcoin import submit_hash, verify_proof

    stamped = confirmed = 0

    # 1) Stamp new events → anchored
    async with AsyncSessionLocal() as db:
        pending = (
            await db.execute(
                select(VerificationEvent).where(VerificationEvent.ots_status == "pending")
            )
        ).scalars().all()
        for ev in pending:
            proof = await submit_hash(ev.payload_hash)
            if proof:
                ev.ots_proof = proof
                ev.ots_status = "anchored"
                stamped += 1
        await db.commit()

    # 2) Upgrade anchored proofs → confirmed once in a Bitcoin block
    async with AsyncSessionLocal() as db:
        anchored = (
            await db.execute(
                select(VerificationEvent).where(VerificationEvent.ots_status == "anchored")
            )
        ).scalars().all()
        for ev in anchored:
            if not ev.ots_proof:
                continue
            verification = await verify_proof(ev.ots_proof, ev.payload_hash)
            if verification.get("confirmed"):
                ev.ots_status = "confirmed"
                ev.btc_block = verification.get("bitcoin_block")
                confirmed += 1
        await db.commit()

    logger.info("OTS lifecycle: %s stamped, %s confirmed", stamped, confirmed)
    return {"stamped": stamped, "confirmed": confirmed}


@celery_app.task(name="probe_endpoints")
def probe_endpoints() -> dict:
    return asyncio.get_event_loop().run_until_complete(_probe_async())


async def _probe_async() -> dict:
    import httpx
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.business import Business
    from app.models.endpoint_probe import EndpointProbe

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                select(Business.id, Business.agent_endpoint).where(
                    Business.agent_endpoint.is_not(None)
                )
            )
        ).all()

    results = []
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        for eid, endpoint in rows:
            try:
                resp = await client.get(endpoint)
                ok = resp.status_code < 500
            except httpx.HTTPError:
                ok = False
            results.append((eid, ok))

    async with AsyncSessionLocal() as db:
        for eid, ok in results:
            db.add(EndpointProbe(entity_id=eid, ok=ok))
        await db.commit()

    return {"probed": len(results)}
