"""Admin simulations (the login-bot test chats): read-only list and transcript, owner and up."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from sim.adapters.db.locks import NS_SIM_INPUT, lock
from sim.adapters.db.repositories.catalog import PersonaRepo
from sim.adapters.db.repositories.jobs import JobRepo
from sim.adapters.db.repositories.simulations import SimBranchRepo, SimMessageRepo
from sim.api.deps import App, Db, Owner, owner_only
from sim.api.schemas import SimContinueIn, SimStartIn, sim_branch_out, sim_branch_row, sim_message_out
from sim.domain.enums import ClientStatus
from sim.services import jobkinds

router = APIRouter(prefix="/api/simulations", tags=["simulations"], dependencies=[Depends(owner_only)])


@router.get("")
async def list_simulations(
    s: Db,
    persona_id: int | None = None,
    admin_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    branches = SimBranchRepo(s)
    rows, total = await branches.page(branches.listing(persona_id, admin_id), limit, offset)
    counts = await SimMessageRepo(s).counts([b.id for b in rows])
    return {"items": [sim_branch_row(b, counts.get(b.id, 0)) for b in rows], "total": total}


@router.get("/{branch_id}")
async def get_simulation(branch_id: int, s: Db, p: Owner):
    branch = await SimBranchRepo(s).get(branch_id)
    if branch is None:
        raise HTTPException(404, "Simulation not found")
    messages = await SimMessageRepo(s).all_of(branch_id)
    pending = await JobRepo(s).has_open(jobkinds.SIM_REPLY, jobkinds.sim_reply_prefix(branch_id))
    return {**sim_branch_out(branch, len(messages)), "messages": [sim_message_out(m) for m in messages],
            "pending": pending,
            "can_continue": branch.admin_user_id == p.user.id and branch.closed_at is None
            and branch.status not in (ClientStatus.REFUSED, ClientStatus.PAUSED)}


@router.post("", status_code=201)
async def start_simulation(body: SimStartIn, c: App, s: Db, p: Owner):
    persona = await PersonaRepo(s).get(body.persona_id)
    if persona is None or not persona.is_active:
        raise HTTPException(422, "Persona is unavailable")
    branch = await c.simulation(s).start(p.user, persona)
    return {**sim_branch_out(branch, 0), "messages": [], "pending": False}


@router.post("/{branch_id}/messages", status_code=202)
async def continue_simulation(branch_id: int, body: SimContinueIn, c: App, s: Db, p: Owner):
    """Queue a browser simulation without using the admin bot transport or a client channel."""
    await lock(s, NS_SIM_INPUT, branch_id)
    branch = await SimBranchRepo(s).get(branch_id)
    if branch is None or branch.admin_user_id != p.user.id:
        raise HTTPException(404, "Simulation not found")
    messages = SimMessageRepo(s)
    previous = await messages.by_request(branch_id, body.request_id)
    jobs = JobRepo(s)
    if previous is not None:
        if previous.role != "client" or previous.text != body.text:
            raise HTTPException(409, "Request id belongs to another message")
        return {"message": sim_message_out(previous), "pending": await jobs.has_open(
            jobkinds.SIM_REPLY, jobkinds.sim_reply_prefix(branch_id))}
    if branch.closed_at is not None or branch.status in (ClientStatus.REFUSED, ClientStatus.PAUSED):
        raise HTTPException(409, "This simulation cannot continue; start a new one")
    if not branch.persona.is_active:
        raise HTTPException(409, "Persona is unavailable")
    if await jobs.has_open(jobkinds.SIM_REPLY, jobkinds.sim_reply_prefix(branch_id)):
        raise HTTPException(409, "Previous simulation reply is still pending")
    message = await c.simulation(s).add_client_message(
        branch, body.text, request_id=body.request_id
    )
    await jobs.enqueue(
        jobkinds.SIM_REPLY,
        {"branch_id": branch_id, "message_id": message.id, "web": True,
         "client_name": f"Тест {p.user.display_name or 'админ'}"},
        c.clock(), dedup_key=jobkinds.sim_reply_key(branch_id, message.id), max_attempts=2,
    )
    return {"message": sim_message_out(message), "pending": True}
