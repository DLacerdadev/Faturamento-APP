from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.types import JSON
from datetime import datetime
from app.db import Base


class ImportTemplate(Base):
    """Modelo de importação: define como ler e mapear uma planilha de uma fonte
    específica (clínica, fornecedor, etc.) para o fluxo único de faturamento.

    O mapeamento liga campos canônicos (cpf, nome, valor, data_exame, ...) às
    colunas reais do arquivo de cada fonte, permitindo padronizar a extração
    mesmo quando os layouts são diferentes.
    """
    __tablename__ = "import_templates"

    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), nullable=False)
    categoria = Column(String(50), nullable=False, default="exames")  # extensível
    descricao = Column(String(500))
    ativo = Column(Boolean, default=True)

    # --- Como ler o arquivo ---
    sheet_mode = Column(String(10), default="index")   # 'index' | 'name'
    sheet_index = Column(Integer, default=0)
    sheet_name = Column(String(255))
    header_row = Column(Integer, default=0)            # linha do cabeçalho (0-based)
    layout = Column(String(10), default="long")        # 'long' | 'wide'

    # --- Vínculo e parsing ---
    match_key = Column(String(20), default="cpf")      # 'cpf' | 'matricula' | 'nome'
    decimal_separator = Column(String(1), default=",")
    date_formats = Column(JSON, default=list)          # ex.: ["%d/%m/%Y", "%Y-%m-%d"]

    # --- Mapeamento de colunas ---
    # layout 'long' (1 linha = 1 exame): campo canônico -> nome/índice da coluna.
    #   chaves usadas: cpf, nome, matricula, cnpj_unidade, tipo, exame,
    #                  data_pedido, data_exame, data_inativacao, valor
    # layout 'wide' (1 linha = 1 funcionário): mapping liga cpf/nome/matricula e
    #   value_columns lista as colunas de exame a somar (total por funcionário).
    mapping = Column(JSON, default=dict)
    value_columns = Column(JSON, default=list)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "categoria": self.categoria,
            "descricao": self.descricao,
            "ativo": bool(self.ativo),
            "sheet_mode": self.sheet_mode,
            "sheet_index": self.sheet_index,
            "sheet_name": self.sheet_name,
            "header_row": self.header_row,
            "layout": self.layout,
            "match_key": self.match_key,
            "decimal_separator": self.decimal_separator,
            "date_formats": self.date_formats or [],
            "mapping": self.mapping or {},
            "value_columns": self.value_columns or [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
