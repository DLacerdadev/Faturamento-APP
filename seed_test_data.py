"""
Popula o banco LOCAL com dados SINTÉTICOS (fictícios) para testar as
funcionalidades em DEV_MODE — sem qualquer dado real de pessoas.

- Nada aqui é dado real: nomes "Funcionário Teste NN", CPFs 000.000.0NN-00.
- Idempotente: se a empresa de teste já existir, não duplica nada.
- Cria 1 empresa, 3 centros de custo (CCs), 15 funcionários com contrato e
  lançamentos de folha no mês corrente e no anterior — o suficiente para que
  os CCs apareçam e os funcionários sejam listados como ATIVOS ao selecionar.

Uso:
    python seed_test_data.py            # popula
    python seed_test_data.py --wipe     # remove SÓ os dados de teste e recria
"""
import sys
from datetime import date

from app.db import SessionLocal, init_db
from app.models.billing import (
    Company, Unit, BillingEmployee, EmploymentContract,
    BillingPeriod, PayrollItem, PayrollItemType, BillingStatus,
)

COMPANY_NAME = "EMPRESA TESTE (DEV)"
COMPANY_CNPJ = "00.000.000/0001-00"

# (codccu, nome_unidade, cnpj_unidade)
UNITS = [
    ("10101", "CD Sao Paulo - Teste", "01.000.001/0001-00"),
    ("10202", "CD Rio de Janeiro - Teste", "01.000.002/0001-00"),
    ("10303", "Filial Belo Horizonte - Teste", "01.000.003/0001-00"),
]

CARGOS = ["Operador de Logistica", "Auxiliar Administrativo", "Motorista", "Conferente", "Supervisor"]
EMP_POR_CC = 5


def _month_keys(today: date):
    cur = f"{today.year}-{today.month:02d}"
    if today.month == 1:
        py, pm = today.year - 1, 12
    else:
        py, pm = today.year, today.month - 1
    prev = f"{py}-{pm:02d}"
    return cur, prev


def wipe(db):
    """Remove APENAS os dados da empresa de teste (não toca em nada real)."""
    company = db.query(Company).filter(Company.cnpj_femsa == COMPANY_CNPJ).first()
    if not company:
        print("Nada para limpar (empresa de teste não existe).")
        return
    unit_ids = [u.id for u in db.query(Unit).filter(Unit.company_id == company.id).all()]
    period_ids = [p.id for p in db.query(BillingPeriod).filter(BillingPeriod.company_id == company.id).all()]
    contract_emp_ids = [
        c.employee_id for c in db.query(EmploymentContract).filter(EmploymentContract.company_id == company.id).all()
    ]
    if period_ids:
        db.query(PayrollItem).filter(PayrollItem.billing_period_id.in_(period_ids)).delete(synchronize_session=False)
    db.query(EmploymentContract).filter(EmploymentContract.company_id == company.id).delete(synchronize_session=False)
    if period_ids:
        db.query(BillingPeriod).filter(BillingPeriod.id.in_(period_ids)).delete(synchronize_session=False)
    if unit_ids:
        db.query(Unit).filter(Unit.id.in_(unit_ids)).delete(synchronize_session=False)
    # funcionários de teste (só os que estavam ligados aos contratos da empresa de teste)
    if contract_emp_ids:
        db.query(BillingEmployee).filter(BillingEmployee.id.in_(set(contract_emp_ids))).delete(synchronize_session=False)
    db.query(Company).filter(Company.id == company.id).delete(synchronize_session=False)
    db.commit()
    print("Dados de teste removidos.")


def run(do_wipe: bool = False):
    init_db()  # garante tabelas + tipos de evento (SALARIO_DIA etc.)
    db = SessionLocal()
    try:
        if do_wipe:
            wipe(db)

        existing = db.query(Company).filter(Company.cnpj_femsa == COMPANY_CNPJ).first()
        if existing:
            print(f"Empresa de teste já existe (id={existing.id}). Use --wipe para recriar. Nada a fazer.")
            return

        salario_type = db.query(PayrollItemType).filter(PayrollItemType.code == "SALARIO_DIA").first()
        if salario_type is None:
            salario_type = db.query(PayrollItemType).first()

        company = Company(
            name=COMPANY_NAME, cnpj_femsa=COMPANY_CNPJ,
            encargos_pct=57.91, taxa_adm_pct=10.0, imposto_pct=8.65,
        )
        db.add(company); db.flush()

        units = []
        for cod, nome, cnpj in UNITS:
            u = Unit(company_id=company.id, cnpj_unidade=cnpj, nome_unidade=nome, centro_custo_femsa=cod)
            db.add(u); units.append(u)
        db.flush()

        today = date.today()
        cur, prev = _month_keys(today)
        periods = []
        for mk in (cur, prev):
            p = BillingPeriod(company_id=company.id, mes_referencia=mk, status=BillingStatus.DRAFT)
            db.add(p); periods.append(p)
        db.flush()

        emp_seq = 0
        for u in units:
            for _ in range(EMP_POR_CC):
                emp_seq += 1
                e = BillingEmployee(cpf=f"000.000.{emp_seq:03d}-00", nome=f"Funcionario Teste {emp_seq:02d}")
                db.add(e); db.flush()
                cargo = CARGOS[(emp_seq - 1) % len(CARGOS)]
                salario = 1800.0 + (emp_seq % 5) * 350.0
                c = EmploymentContract(
                    employee_id=e.id, company_id=company.id, unit_id=u.id,
                    cargo=cargo, funcao=cargo, salario_base=salario,
                    data_admissao=date(2024, 1, 15),
                )
                db.add(c); db.flush()
                for p in periods:
                    db.add(PayrollItem(
                        billing_period_id=p.id, employee_id=e.id, contract_id=c.id,
                        unit_id=u.id, payroll_item_type_id=salario_type.id,
                        quantity=30.0, amount=salario, source_column="SALARIO",
                    ))
        db.commit()
        print(
            f"OK: empresa id={company.id} | {len(units)} centros de custo "
            f"({', '.join(c for c, _, _ in UNITS)}) | {emp_seq} funcionarios | "
            f"periodos={cur}, {prev}"
        )
        print("Ligue o DEV_MODE (FORCE_DEV_MODE=1 no .env) e reinicie o servidor para ver os CCs.")
    finally:
        db.close()


if __name__ == "__main__":
    run(do_wipe="--wipe" in sys.argv)
