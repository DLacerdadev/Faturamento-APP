from fastapi import APIRouter, Depends, UploadFile, File, Form
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, joinedload
from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel
import os
import uuid
import shutil

from app.db import get_db
from app.models.epi_purchase import EpiPurchasePackage, EpiPurchaseItem, EpiPurchaseDocument

router = APIRouter(prefix="/api/epi-purchases", tags=["epi_purchases"])

EPI_UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "epi_documents")
os.makedirs(EPI_UPLOAD_DIR, exist_ok=True)


class EpiItemData(BaseModel):
    descricao: str
    quantidade: int = 1
    valor_unitario: float = 0.0
    valor_total: float = 0.0


class EpiPackageCreate(BaseModel):
    empresa: str = "FEMSA"
    mes_ano: str
    observacao: Optional[str] = None
    items: List[EpiItemData] = []


class EpiPackageUpdate(BaseModel):
    empresa: Optional[str] = None
    mes_ano: Optional[str] = None
    observacao: Optional[str] = None
    items: Optional[List[EpiItemData]] = None


def package_to_dict(pkg: EpiPurchasePackage) -> dict:
    return {
        "id": pkg.id,
        "empresa": pkg.empresa,
        "mes_ano": pkg.mes_ano.isoformat() if pkg.mes_ano else None,
        "observacao": pkg.observacao,
        "items": [
            {
                "id": item.id,
                "descricao": item.descricao,
                "quantidade": item.quantidade,
                "valor_unitario": item.valor_unitario,
                "valor_total": item.valor_total,
            }
            for item in (pkg.items or [])
        ],
        "documents": [
            {
                "id": doc.id,
                "original_filename": doc.original_filename,
                "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            }
            for doc in (pkg.documents or [])
        ],
        "total_geral": sum(item.valor_total or 0 for item in (pkg.items or [])),
        "created_at": pkg.created_at.isoformat() if pkg.created_at else None,
        "updated_at": pkg.updated_at.isoformat() if pkg.updated_at else None,
    }


@router.post("")
async def create_package(data: EpiPackageCreate, db: Session = Depends(get_db)):
    try:
        mes_ano_date = datetime.strptime(data.mes_ano[:7], "%Y-%m").date()
    except ValueError:
        return {"status": "error", "message": "Formato de mes_ano invalido. Use YYYY-MM"}

    pkg = EpiPurchasePackage(
        empresa=data.empresa,
        mes_ano=mes_ano_date,
        observacao=data.observacao,
    )

    for item_data in data.items:
        item = EpiPurchaseItem(
            descricao=item_data.descricao,
            quantidade=item_data.quantidade,
            valor_unitario=item_data.valor_unitario,
            valor_total=item_data.valor_total,
        )
        pkg.items.append(item)

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
async def update_package(package_id: int, data: EpiPackageUpdate, db: Session = Depends(get_db)):
    pkg = (
        db.query(EpiPurchasePackage)
        .options(joinedload(EpiPurchasePackage.items), joinedload(EpiPurchasePackage.documents))
        .filter(EpiPurchasePackage.id == package_id)
        .first()
    )
    if not pkg:
        return {"status": "error", "message": "Pacote nao encontrado"}

    if data.empresa is not None:
        pkg.empresa = data.empresa
    if data.mes_ano is not None:
        try:
            pkg.mes_ano = datetime.strptime(data.mes_ano[:7], "%Y-%m").date()
        except ValueError:
            return {"status": "error", "message": "Formato de mes_ano invalido"}
    if data.observacao is not None:
        pkg.observacao = data.observacao

    if data.items is not None:
        for old_item in pkg.items:
            db.delete(old_item)
        db.flush()

        for item_data in data.items:
            item = EpiPurchaseItem(
                package_id=pkg.id,
                descricao=item_data.descricao,
                quantidade=item_data.quantidade,
                valor_unitario=item_data.valor_unitario,
                valor_total=item_data.valor_total,
            )
            db.add(item)

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
