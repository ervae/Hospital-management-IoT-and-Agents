"""Yatış talepleri ve ajan önerileri (insan onayı burada verilir)."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, col, select

from ..db import get_session
from ..deps import current_user, require_operator
from ..models import BedRequest, Recommendation, RecStatus, RequestStatus, User
from ..schemas import ApproveIn, BedRequestIn, BedRequestOut, RecommendationOut, RejectIn
from ..services import flow

router = APIRouter(tags=["Hasta akışı"])


@router.get("/requests", response_model=list[BedRequestOut])
def list_requests(status: RequestStatus | None = RequestStatus.waiting, limit: int = Query(100, le=500),
                  s: Session = Depends(get_session), _: User = Depends(current_user)):
    q = select(BedRequest).order_by(col(BedRequest.created_at).desc()).limit(limit)
    if status:
        q = q.where(BedRequest.status == status)
    return [flow.request_out(r) for r in s.exec(q).all()]


@router.post("/requests", response_model=BedRequestOut, status_code=201)
def create_request(body: BedRequestIn, s: Session = Depends(get_session), user: User = Depends(require_operator)):
    return flow.request_out(flow.create_request(s, body.model_dump(), user.id))


@router.post("/requests/{request_id}/cancel", response_model=BedRequestOut)
def cancel_request(request_id: str, s: Session = Depends(get_session), user: User = Depends(require_operator)):
    req = s.get(BedRequest, request_id)
    if not req:
        raise HTTPException(404, "Talep bulunamadı")
    return flow.request_out(flow.cancel_request(s, req, user.id))


@router.get("/recommendations", response_model=list[RecommendationOut])
def list_recommendations(status: RecStatus | None = None, limit: int = Query(50, le=200),
                         s: Session = Depends(get_session), _: User = Depends(current_user)):
    return flow.recent_recommendations(s, limit, status)


def _rec(s: Session, rec_id: str) -> Recommendation:
    rec = s.get(Recommendation, rec_id)
    if not rec:
        raise HTTPException(404, "Öneri bulunamadı")
    return rec


@router.post("/recommendations/{rec_id}/approve", response_model=RecommendationOut)
def approve(rec_id: str, body: ApproveIn, s: Session = Depends(get_session), user: User = Depends(require_operator)):
    """Yatak yönetim sorumlusu öneriyi onaylar. Kurallar onay anında yeniden denetlenir."""
    return flow.approve(s, _rec(s, rec_id), body.room_id, body.note, user.id)


@router.post("/recommendations/{rec_id}/reject", response_model=RecommendationOut)
def reject(rec_id: str, body: RejectIn, s: Session = Depends(get_session), user: User = Depends(require_operator)):
    return flow.reject(s, _rec(s, rec_id), body.note, user.id)
