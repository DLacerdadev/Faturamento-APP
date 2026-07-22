"""Importa o cadastro de produtos do TOTVS (uniformes/EPIs/equipamentos) para o
ProductCatalog. Filtra pelos grupos definidos e faz upsert por código.
Preço NÃO vem deste arquivo — é editado depois na tela."""
import io
from typing import Dict, Any
import openpyxl
from sqlalchemy.orm import Session

from app.models.product_catalog import ProductCatalog

# grupo TOTVS -> (nome do grupo, categoria no faturamento)
GROUP_MAP = {
    "0001": ("Ativo Fixo (Máquinas)", "equipamento"),
    "0002": ("TI", "equipamento"),
    "0003": ("EPI", "epi"),
    "0004": ("Uniformes", "uniforme"),
    "0006": ("Ferramentas", "equipamento"),
    "0007": ("Material de expediente", "equipamento"),
    "0008": ("Material de limpeza", "equipamento"),
}


def _norm_grupo(v) -> str:
    s = str(v).strip() if v is not None else ""
    return s.zfill(4) if s.isdigit() else s


def import_produtos_totvs(db: Session, content: bytes) -> Dict[str, Any]:
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # detecta a linha de cabeçalho (que tem 'Codigo' e 'Grupo')
    hidx = None
    for i, r in enumerate(rows[:15]):
        cells = [str(c).strip().lower() if c is not None else "" for c in r]
        if "codigo" in cells and "grupo" in cells:
            hidx = i
            break
    if hidx is None:
        return {"success": False, "erro": "Cabeçalho não encontrado (esperado colunas Codigo/Grupo)."}

    headers = [str(c).strip() if c is not None else "" for c in rows[hidx]]

    def col(name):
        for j, h in enumerate(headers):
            if h.lower() == name.lower():
                return j
        return None

    ci, cd, cg, cu = col("Codigo"), col("Descricao"), col("Grupo"), col("Unidade")
    if ci is None or cg is None:
        return {"success": False, "erro": "Colunas Codigo/Grupo não encontradas."}

    existentes = {p.codigo: p for p in db.query(ProductCatalog).all()}
    result = {"success": True, "inseridos": 0, "atualizados": 0, "ignorados_outros_grupos": 0,
              "por_categoria": {}, "total_arquivo": 0}

    for r in rows[hidx + 1:]:
        cod = str(r[ci]).strip() if ci < len(r) and r[ci] is not None else ""
        if not cod:
            continue
        result["total_arquivo"] += 1
        grp = _norm_grupo(r[cg]) if cg < len(r) else ""
        if grp not in GROUP_MAP:
            result["ignorados_outros_grupos"] += 1
            continue
        gnome, cat = GROUP_MAP[grp]
        desc = str(r[cd]).strip() if cd is not None and cd < len(r) and r[cd] is not None else ""
        uni = str(r[cu]).strip() if cu is not None and cu < len(r) and r[cu] is not None else ""

        p = existentes.get(cod)
        if p:
            p.descricao, p.grupo, p.grupo_nome, p.categoria, p.unidade = desc, grp, gnome, cat, uni
            result["atualizados"] += 1
        else:
            p = ProductCatalog(codigo=cod, descricao=desc, grupo=grp, grupo_nome=gnome,
                               categoria=cat, unidade=uni, ativo=True)
            db.add(p)
            existentes[cod] = p
            result["inseridos"] += 1
        result["por_categoria"][cat] = result["por_categoria"].get(cat, 0) + 1

    db.commit()
    # Espelha os EPIs no catálogo de EPIs antigo (aparecem nas duas telas)
    sync = sync_epis_to_epi_catalog(db)
    result["epis_sincronizados"] = sync
    return result


def sync_epis_to_epi_catalog(db: Session) -> Dict[str, int]:
    """Espelha os produtos categoria='epi' no catálogo de EPIs (epi_catalog),
    vinculados por produto_codigo. Produto é a fonte; tamanho 'Único' com o preço."""
    from sqlalchemy.orm import joinedload
    from app.models.epi_purchase import EpiCatalog, EpiCatalogSize

    existentes = {
        e.produto_codigo: e
        for e in db.query(EpiCatalog).options(joinedload(EpiCatalog.sizes))
        .filter(EpiCatalog.produto_codigo.isnot(None)).all()
    }
    epis = db.query(ProductCatalog).filter(ProductCatalog.categoria == "epi").all()
    criados = atualizados = 0
    for p in epis:
        valor = float(p.preco) if p.preco else 0.0
        e = existentes.get(p.codigo)
        if e:
            e.nome = p.descricao or p.codigo
            e.ativo = bool(p.ativo)
            if e.sizes:
                e.sizes[0].valor = valor
            else:
                e.sizes.append(EpiCatalogSize(tamanho="Único", valor=valor))
            atualizados += 1
        else:
            ne = EpiCatalog(nome=p.descricao or p.codigo, ativo=bool(p.ativo), produto_codigo=p.codigo)
            ne.sizes.append(EpiCatalogSize(tamanho="Único", valor=valor))
            db.add(ne)
            existentes[p.codigo] = ne
            criados += 1
    db.commit()
    return {"criados": criados, "atualizados": atualizados}


def sync_one_epi(db: Session, product) -> None:
    """Propaga uma alteração de um produto EPI para o epi_catalog vinculado."""
    from app.models.epi_purchase import EpiCatalog, EpiCatalogSize
    if not product or product.categoria != "epi":
        return
    valor = float(product.preco) if product.preco else 0.0
    e = db.query(EpiCatalog).filter(EpiCatalog.produto_codigo == product.codigo).first()
    if e:
        e.nome = product.descricao or product.codigo
        e.ativo = bool(product.ativo)
        if e.sizes:
            e.sizes[0].valor = valor
        else:
            e.sizes.append(EpiCatalogSize(tamanho="Único", valor=valor))
    else:
        e = EpiCatalog(nome=product.descricao or product.codigo, ativo=bool(product.ativo), produto_codigo=product.codigo)
        e.sizes.append(EpiCatalogSize(tamanho="Único", valor=valor))
        db.add(e)
    db.commit()
