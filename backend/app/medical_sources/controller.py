# Admin API of the trusted medical source library: /ai/admin/medical-sources.
# Every endpoint needs an Admin access token from the NestJS backend (401 without a valid token, 403 otherwise).
# Thin, like a Django view: it reads the request and calls service.py, which holds the rules.
#
#   GET    /ai/admin/medical-sources                    list, newest first (filters: country, status, motor, topic;
#                                                        search; sort/order; optional page/limit)
#   GET    /ai/admin/medical-sources/topics             the allowed topic keys
#   GET    /ai/admin/medical-sources/{id}               one source
#   POST   /ai/admin/medical-sources                    add a source (saved as "draft": the AI cannot cite it yet)
#   PATCH  /ai/admin/medical-sources/{id}               change fields (an active source needs "approval")
#   POST   /ai/admin/medical-sources/{id}/approve       client's written approval: the AI may cite it
#   POST   /ai/admin/medical-sources/{id}/deactivate    the AI stops citing it (sources are never deleted)
#
# The motors read the table again within MEDICAL_SOURCES_CACHE_SECONDS (5 minutes by default).

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.medical_sources import service
from app.medical_sources.schemas import (
    ApproveRequest, Country, DeactivateRequest, Motor, SortField, SortOrder, SourceCreate, SourceList, SourceOut,
    SourceUpdate, Status, TopicsOut,
)
from python_auth.auth import AuthUser, require_roles
from python_auth.roles import UserType

router = APIRouter(prefix="/ai/admin/medical-sources", tags=["medical sources (admin)"])

admin_only = require_roles(UserType.Admin)


def http_error(error: service.SourceError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.message)


@router.get("", response_model=SourceList)
async def list_sources(
    country: Optional[Country] = None,
    status: Optional[Status] = None,
    motor: Optional[Motor] = None,
    topic: Optional[str] = Query(None, max_length=100),
    search: Optional[str] = Query(
        None, max_length=200,
        description="Text in id, title, organisation, authors, publication, DOI, patient label, link, notes or a topic",
    ),
    sort: SortField = Query("created_at", description="Newest first by default"),
    order: SortOrder = "desc",
    page: Optional[int] = Query(None, ge=1),
    limit: Optional[int] = Query(None, ge=1, le=200, description="Page size; leave out to get every source"),
    user: AuthUser = Depends(admin_only),
) -> dict:
    return await service.list_sources(
        country=country, status=status, motor=motor, topic=topic, search=search,
        sort=sort, order=order, page=page, limit=limit,
    )


# Declared before "/{source_id}", or "topics" would be read as a source id.
@router.get("/topics", response_model=TopicsOut)
async def list_topics(user: AuthUser = Depends(admin_only)) -> dict:
    return {"topics": service.list_topics()}


@router.get("/{source_id}", response_model=SourceOut)
async def get_source(source_id: str, user: AuthUser = Depends(admin_only)) -> dict:
    try:
        return await service.get_source(source_id)
    except service.SourceError as error:
        raise http_error(error) from None


@router.post("", response_model=SourceOut, status_code=201)
async def create_source(request: SourceCreate, user: AuthUser = Depends(admin_only)) -> dict:
    try:
        return await service.create_source(request, user)
    except service.SourceError as error:
        raise http_error(error) from None


@router.patch("/{source_id}", response_model=SourceOut)
async def update_source(source_id: str, request: SourceUpdate, user: AuthUser = Depends(admin_only)) -> dict:
    try:
        return await service.update_source(source_id, request, user)
    except service.SourceError as error:
        raise http_error(error) from None


@router.post("/{source_id}/approve", response_model=SourceOut)
async def approve_source(source_id: str, request: ApproveRequest, user: AuthUser = Depends(admin_only)) -> dict:
    try:
        return await service.approve_source(source_id, request, user)
    except service.SourceError as error:
        raise http_error(error) from None


@router.post("/{source_id}/deactivate", response_model=SourceOut)
async def deactivate_source(
    source_id: str, request: DeactivateRequest, user: AuthUser = Depends(admin_only),
) -> dict:
    try:
        return await service.deactivate_source(source_id, request, user)
    except service.SourceError as error:
        raise http_error(error) from None
