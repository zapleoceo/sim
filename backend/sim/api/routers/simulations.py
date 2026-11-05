"""Admin simulations (the login-bot test chats): read-only list and transcript, owner and up."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from sim.adapters.db.repositories.simulations import SimBranchRepo, SimMessageRepo
from sim.api.deps import Db, owner_only
from sim.api.schemas import sim_branch_out, sim_branch_row, sim_message_out

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
async def get_simulation(branch_id: int, s: Db):
    branch = await SimBranchRepo(s).get(branch_id)
    if branch is None:
        raise HTTPException(404, "Simulation not found")
    messages = await SimMessageRepo(s).all_of(branch_id)
    return {**sim_branch_out(branch, len(messages)), "messages": [sim_message_out(m) for m in messages]}
