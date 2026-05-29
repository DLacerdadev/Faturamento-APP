from fastapi import APIRouter, Depends, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel, Field
import os
import uuid
import shutil

from app.db import get_db
from app.models.epi_purchase import EpiPurchasePackage, EpiPurchaseItem, EpiPurchaseDocument
from app.services.senior_connector import fetch_active_employees

router = APIRouter(prefix="/api/epi-purchases", tags=["epi_purchases"])

EPI_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "epi_documents")
os.makedirs(EPI_UPLOAD_DIR, exist_ok=True)


class EmployeeSelection(BaseModel):
    numcad: int
    nome: str


class EpiItemInput(BaseModel):
    descricao: str = Field(..., min_length=1)
    quantidade: int = Field(..., ge=1)
    valor_unitario: float = Field(..., gt=0)


class EpiPackageCreateV2(BaseModel):
    empresa: str = "FEMSA"
    mes_ano: str
    codccu: str = Field(..., min_length=1)
    observacao: Optional[str] = None
    employees: List[EmployeeSelection] = Field(..., min_length=1)
    items: List[EpiItemInput] = Field(..., min_length=1)


def _expand_cartesian(pkg: EpiPurchasePackage, payload: EpiPackageCreateV2) -> None:
    """Adiciona linhas |employees| × |items| ao pacote (sem commit)."""
    for emp in payload.employees:
        for item in payload.items:
            pkg.items.append(EpiPurchaseItem(
                descricao=item.descricao,
                quantidade=item.quantidade,
                valor_unitario=item.valor_unitario,
                valor_total=item.quantidade * item.valor_unitario,
                employee_numcad=emp.numcad,
                employee_nome=emp.nome,
            ))


def _revalidate_active(payload: EpiPackageCreateV2) -> List[dict]:
    """
    Spec 001-epi-purchase-flow, FR-13: ao salvar, checa se cada `numcad` recebido
    ainda está ativo no CCU. Retorna lista de inativos (vazia = tudo OK).
    """
    try:
        active = fetch_active_employees(payload.codccu)
    except Exception:
        # Se a consulta Senior falhar, não bloqueia o save — apenas pula a revalidação.
        # A alternativa (bloquear sempre) seria pior em incidente do Senior.
        return []
    active_numcads = {e.get("numcad") for e in active}
    inactive = []
    for emp in payload.employees:
        if emp.numcad not in active_numcads:
            inactive.append({
                "numcad": emp.numcad,
                "nome": emp.nome,
                "motivo": "Não está mais ativo no CCU informado",
            })
    return inactive


def _dict_distinct(seq, key):
    seen = set()
    out = []
    for x in seq:
        k = key(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def package_to_dict(pkg: EpiPurchasePackage) -> dict:
    items_list = list(pkg.items or [])

    linhas_flat = [
        {
            "id": item.id,
            "descricao": item.descricao,
            "quantidade": item.quantidade,
            "valor_unitario": item.valor_unitario,
            "valor_total": item.valor_total,
            "employee_numcad": item.employee_numcad,
            "employee_nome": item.employee_nome,
        }
        for item in items_list
    ]

    funcionarios = _dict_distinct(
        [
            {"numcad": item.employee_numcad, "nome": item.employee_nome}
            for item in items_list
            if item.employee_numcad is not None
        ],
        key=lambda f: f["numcad"],
    )

    itens_distintos = _dict_distinct(
        [
            {
                "descricao": item.descricao,
                "quantidade": item.quantidade,
                "valor_unitario": item.valor_unitario,
            }
            for item in items_list
        ],
        key=lambda i: (i["descricao"], i["quantidade"], i["valor_unitario"]),
    )

    return {
        "id": pkg.id,
        "empresa": pkg.empresa,
        "mes_ano": pkg.mes_ano.isoformat() if pkg.mes_ano else None,
        "codccu": pkg.codccu,
        "observacao": pkg.observacao,
        "linhas_flat": linhas_flat,
        "agrupado": {
            "funcionarios": funcionarios,
            "itens": itens_distintos,
        },
        "totais": {
            "funcionarios_distintos": len(funcionarios),
            "itens_distintos": len(itens_distintos),
            "total_linhas": len(items_list),
            "valor_total_compra": sum(item.valor_total or 0 for item in items_list),
        },
        # legacy keys (retro-compat)
        "items": linhas_flat,
        "total_geral": sum(item.valor_total or 0 for item in items_list),
        "documents": [
            {
                "id": doc.id,
                "original_filename": doc.original_filename,
                "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            }
            for doc in (pkg.documents or [])
        ],
        "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
        "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else None,
    }


@router.post("")
async def create_package(data: EpiPackageCreateV2, db: Session = Depends(get_db)):
    try:
        mes_ano_date = datetime.strptime(data.mes_ano[:7], "%Y-%m").date()
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Formato de mes_ano invalido. Use YYYY-MM"},
        )

    inactive = _revalidate_active(data)
    if inactive:
        return JSONResponse(
            status_code=409,
            content={
                "status": "stale",
                "message": "Alguns funcionários selecionados já não estão ativos.",
                "inactive": inactive,
            },
        )

    pkg = EpiPurchasePackage(
        empresa=data.empresa,
        mes_ano=mes_ano_date,
        codccu=data.codccu,
        observacao=data.observacao,
    )
    _expand_cartesian(pkg, data)

    db.add(pkg)
    db.commit()
    db.refresh(pkg)
    return {"status": "success", "data": package_to_dict(pkg)}


@router.get("")
async def list_packages(
    empresa: Optional[str] = None,
    mes_ano: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    db: Session = Depends(get_db),
):
    query = db.query(EpiPurchasePackage).options(
        joinedload(EpiPurchasePackage.items),
        joinedload(EpiPurchasePackage.documents),
    )

    if empresa:
        query = query.filter(EpiPurchasePackage.empresa == empresa)
    if mes_ano:
        try:
            filter_date = datetime.strptime(mes_ano[:7], "%Y-%m").date()
            query = query.filter(EpiPurchasePackage.mes_ano == filter_date)
        except ValueError:
            pass

    total = query.count()
    packages = (
        query.order_by(EpiPurchasePackage.mes_ano.desc(), EpiPurchasePackage.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    seen = set()
    unique_packages = []
    for p in packages:
        if p.id not in seen:
            seen.add(p.id)
            unique_packages.append(p)

    return {
        "status": "ok",
        "data": [package_to_dict(p) for p in unique_packages],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


@router.get("/{package_id}")
async def get_package(package_id: int, db: Session = Depends(get_db)):
    pkg = (
        db.query(EpiPurchasePackage)
        .options(
            joinedload(EpiPurchasePackage.items),
            joinedload(EpiPurchasePackage.documents),
        )
        .filter(EpiPurchasePackage.id == package_id)
        .first()
    )
    if not pkg:
        return {"status": "error", "message": "Pacote nao encontrado"}
    return {"status": "ok", "data": package_to_dict(pkg)}


@router.put("/{package_id}")
async def update_package(package_id: int, data: EpiPackageCreateV2, db: Session = Depends(get_db)):
    pkg = (
        db.query(EpiPurchasePackage)
        .options(joinedload(EpiPurchasePackage.items), joinedload(EpiPurchasePackage.documents))
        .filter(EpiPurchasePackage.id == package_id)
        .first()
    )
    if not pkg:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "message": "Pacote nao encontrado"},
        )

    try:
        mes_ano_date = datetime.strptime(data.mes_ano[:7], "%Y-%m").date()
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Formato de mes_ano invalido. Use YYYY-MM"},
        )

    inactive = _revalidate_active(data)
    if inactive:
        return JSONResponse(
            status_code=409,
            content={
                "status": "stale",
                "message": "Alguns funcionários selecionados já não estão ativos.",
                "inactive": inactive,
            },
        )

    pkg.empresa = data.empresa
    pkg.mes_ano = mes_ano_date
    pkg.codccu = data.codccu
    pkg.observacao = data.observacao

    for old_item in list(pkg.items):
        db.delete(old_item)
    db.flush()

    _expand_cartesian(pkg, data)

    db.commit()
    db.refresh(pkg)
    return {"status": "success", "data": package_to_dict(pkg)}


@router.delete("/{package_id}")
async def delete_package(package_id: int, db: Session = Depends(get_db)):
    pkg = (
        db.query(EpiPurchasePackage)
        .options(joinedload(EpiPurchasePackage.documents))
        .filter(EpiPurchasePackage.id == package_id)
        .first()
    )
    if not pkg:
        return {"status": "error", "message": "Pacote nao encontrado"}

    for doc in pkg.documents:
        filepath = os.path.join(EPI_UPLOAD_DIR, doc.stored_filename)
        if os.path.exists(filepath):
            os.remove(filepath)

    db.delete(pkg)
    db.commit()
    return {"status": "success", "message": "Pacote removido"}


@router.post("/{package_id}/documents")
async def upload_document(
    package_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    pkg = db.query(EpiPurchasePackage).filter(EpiPurchasePackage.id == package_id).first()
    if not pkg:
        return {"status": "error", "message": "Pacote nao encontrado"}

    ext = os.path.splitext(file.filename)[1] if file.filename else ".pdf"
    stored_name = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(EPI_UPLOAD_DIR, stored_name)

    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)

    doc = EpiPurchaseDocument(
        package_id=package_id,
        original_filename=file.filename or "documento",
        stored_filename=stored_name,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return {
        "status": "success",
        "data": {
            "id": doc.id,
            "original_filename": doc.original_filename,
            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        },
    }


@router.get("/{package_id}/documents/{doc_id}/download")
async def download_document(package_id: int, doc_id: int, db: Session = Depends(get_db)):
    doc = (
        db.query(EpiPurchaseDocument)
        .filter(EpiPurchaseDocument.id == doc_id, EpiPurchaseDocument.package_id == package_id)
        .first()
    )
    if not doc:
        return {"status": "error", "message": "Documento nao encontrado"}

    filepath = os.path.join(EPI_UPLOAD_DIR, doc.stored_filename)
    if not os.path.exists(filepath):
        return {"status": "error", "message": "Arquivo nao encontrado no servidor"}

    return FileResponse(
        filepath,
        filename=doc.original_filename,
        media_type="application/octet-stream",
    )


@router.delete("/{package_id}/documents/{doc_id}")
async def delete_document(package_id: int, doc_id: int, db: Session = Depends(get_db)):
    doc = (
        db.query(EpiPurchaseDocument)
        .filter(EpiPurchaseDocument.id == doc_id, EpiPurchaseDocument.package_id == package_id)
        .first()
    )
    if not doc:
        return {"status": "error", "message": "Documento nao encontrado"}

    filepath = os.path.join(EPI_UPLOAD_DIR, doc.stored_filename)
    if os.path.exists(filepath):
        os.remove(filepath)

    db.delete(doc)
    db.commit()
    return {"status": "success", "message": "Documento removido"}
