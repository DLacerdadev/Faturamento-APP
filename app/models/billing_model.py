from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, LargeBinary
from sqlalchemy.types import JSON
from datetime import datetime
from app.db import Base


class BillingModel(Base):
    """Modelo de faturamento configurável: nome + lista ORDENADA das colunas
    que a planilha exportada deve conter.

    'is_base' marca o modelo GERAL (superconjunto de colunas conhecidas) usado
    como base para montar/editar os demais modelos na tela. A lista de colunas
    fica em 'colunas' (JSON com os nomes na ordem de exibição).

    Modelos criados por UPLOAD de Excel guardam também 'estrutura' (contrato C1
    do plano: aba, header_rows, data_row, colunas com tipo campo/formula/vazio,
    constantes) — usada pelo renderizador custom da exportação. Modelos sem
    'estrutura' continuam colunas-driven (compat total).

    'encargos_pct'/'taxa_adm_pct'/'imposto_pct' são os percentuais PADRÃO do
    modelo: quando o modelo é escolhido na exportação, esses valores preenchem
    os campos (fallback: contrato). NULL = sem padrão definido.

    Um contrato (Company) aponta para um BillingModel via billing_model_id; a
    exportação usa a lista de colunas desse modelo (ver excel_export).
    """
    __tablename__ = "billing_models"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(120), unique=True, nullable=False, index=True)
    descricao = Column(String(500))
    is_base = Column(Boolean, default=False)   # True = modelo GERAL (superconjunto)
    ativo = Column(Boolean, default=True)
    colunas = Column(JSON, default=list)       # lista ordenada de nomes de coluna
    estrutura = Column(JSON, nullable=True)    # contrato C1 (modelos por upload); NULL = colunas-driven
    arquivo_origem = Column(String(500))       # nome do Excel que originou o modelo
    # Planilha-modelo SEM PII (funcionários removidos), usada como TEMPLATE na
    # exportação: preserva logo, bordas, larguras, mesclagens e formatação do
    # cliente. NULL = usa o renderizador reconstruído (fallback). Ver excel_export.
    arquivo_template = Column(LargeBinary, nullable=True)
    encargos_pct = Column(Float)               # % padrão de encargos sociais do modelo
    taxa_adm_pct = Column(Float)               # % padrão de taxa administrativa do modelo
    imposto_pct = Column(Float)                # alíquota padrão de imposto (%) do modelo
    # Fórmula OPCIONAL do campo "Salário" (metodologia própria do cliente, ex.:
    # "salario / 29 * 30"). NULL = salário-base cadastral (padrão). Variáveis e
    # validação: app/services/formula_salario.py.
    salario_formula = Column(String(200))
    # Grade "Fórmulas" do modelo (aba na tela de modelos): configuração POR CAMPO
    # da exportação. Lista de {campo, codigo (evento Senior, aceita "257,259"),
    # codigo_nome (informativo), formula (aritmética c/ variáveis; só número =
    # valor fixo)}. NULL/ausente = mapeamento padrão do sistema para o campo.
    campos_config = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self, with_colunas=False):
        d = {
            "id": self.id,
            "nome": self.nome,
            "descricao": self.descricao,
            "is_base": bool(self.is_base),
            "ativo": bool(self.ativo),
            "num_colunas": len(self.colunas or []),
            "encargos_pct": self.encargos_pct,
            "taxa_adm_pct": self.taxa_adm_pct,
            "imposto_pct": self.imposto_pct,
            "salario_formula": self.salario_formula,
            "tem_estrutura": bool(self.estrutura),
            "tem_template": bool(self.arquivo_template),
            "arquivo_origem": self.arquivo_origem,
        }
        if with_colunas:
            d["colunas"] = self.colunas or []
            d["estrutura"] = self.estrutura
            d["campos_config"] = self.campos_config or []
        return d
