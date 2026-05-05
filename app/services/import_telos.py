from app.db import SessionLocal
from app.models.billing import (
    Company, Unit, BillingEmployee as Employee, EmploymentContract,
    BillingPeriod, PayrollItemType, PayrollItem, BillingExamRecord as ExamRecord
)
from app.services.senior_connector import fetch_employees_telos, fetch_payroll_items_telos


def import_telos_employees():
    """
    Importa funcionários da empresa TELOS do banco Senior para o banco interno.
    
    1. Busca todos os funcionários da empresa TELOS na base Senior.
    2. Cria/busca a Company TELOS.
    3. Para cada funcionário:
       - Cria/busca Unit (unidade) pelo cnpj_unidade.
       - Cria/busca Employee pelo CPF.
       - Cria EmploymentContract ligando employee + company + unit.
    """
    db = SessionLocal()
    
    try:
        company = db.query(Company).filter(Company.name == "TELOS").first()
        if not company:
            company = Company(name="TELOS", cnpj_femsa="00000000000000")
            db.add(company)
            db.commit()
            db.refresh(company)
        
        employees_data = fetch_employees_telos()
        
        imported_count = 0
        errors = []
        
        for row in employees_data:
            try:
                cpf = row.get("cpf")
                if not cpf:
                    errors.append("Registro sem CPF encontrado")
                    continue
                
                cpf = cpf.strip().replace(".", "").replace("-", "")
                
                unit = None
                cnpj_unidade = row.get("cnpj_unidade")
                if cnpj_unidade:
                    cnpj_unidade = cnpj_unidade.strip()
                    unit = db.query(Unit).filter(
                        Unit.cnpj_unidade == cnpj_unidade,
                        Unit.company_id == company.id
                    ).first()
                    
                    if not unit:
                        unit = Unit(
                            company_id=company.id,
                            cnpj_unidade=cnpj_unidade,
                            nome_unidade=row.get("nome_unidade", "").strip() or cnpj_unidade,
                            centro_custo_femsa=row.get("centro_custo")
                        )
                        db.add(unit)
                        db.commit()
                        db.refresh(unit)
                
                employee = db.query(Employee).filter(Employee.cpf == cpf).first()
                if not employee:
                    employee = Employee(
                        cpf=cpf,
                        nome=row.get("nome", "").strip() or "Sem Nome"
                    )
                    db.add(employee)
                    db.commit()
                    db.refresh(employee)
                
                existing_contract = db.query(EmploymentContract).filter(
                    EmploymentContract.employee_id == employee.id,
                    EmploymentContract.company_id == company.id,
                    EmploymentContract.unit_id == (unit.id if unit else None)
                ).first()
                
                if existing_contract:
                    existing_contract.cargo = row.get("cargo") or existing_contract.cargo
                    existing_contract.funcao = row.get("funcao") or existing_contract.funcao
                    existing_contract.data_admissao = row.get("data_admissao") or existing_contract.data_admissao
                    existing_contract.data_demissao = row.get("data_demissao") or existing_contract.data_demissao
                    db.commit()
                else:
                    contract = EmploymentContract(
                        employee_id=employee.id,
                        company_id=company.id,
                        unit_id=unit.id if unit else None,
                        cargo=row.get("cargo"),
                        funcao=row.get("funcao"),
                        data_admissao=row.get("data_admissao"),
                        data_demissao=row.get("data_demissao")
                    )
                    db.add(contract)
                    db.commit()
                
                imported_count += 1
                
            except Exception as e:
                errors.append(f"Erro ao importar CPF {row.get('cpf', 'N/A')}: {str(e)}")
                db.rollback()
        
        return {
            "status": "ok",
            "imported": imported_count,
            "errors": errors
        }
        
    except Exception as e:
        db.rollback()
        return {
            "status": "error",
            "message": str(e)
        }
    finally:
        db.close()


def import_telos_payroll(competencia: str):
    """
    Importa os lançamentos de folha da TELOS para um determinado mês de referência.
    
    1. Busca Company TELOS.
    2. Cria/busca BillingPeriod para a competência.
    3. Para cada item retornado de fetch_payroll_items_telos:
       - Busca Employee pelo CPF.
       - Busca Unit pelo cnpj_unidade (se existir).
       - Busca/cria PayrollItemType pelo código.
       - Cria PayrollItem com os dados.
    """
    db = SessionLocal()
    
    try:
        company = db.query(Company).filter(Company.name == "TELOS").first()
        if not company:
            return {
                "status": "error",
                "message": "Empresa TELOS não encontrada. Execute import-employees primeiro."
            }
        
        billing_period = db.query(BillingPeriod).filter(
            BillingPeriod.company_id == company.id,
            BillingPeriod.mes_referencia == competencia
        ).first()
        
        if not billing_period:
            billing_period = BillingPeriod(
                company_id=company.id,
                mes_referencia=competencia,
                status="aberto"
            )
            db.add(billing_period)
            db.commit()
            db.refresh(billing_period)
        
        payroll_data = fetch_payroll_items_telos(competencia)
        
        imported_count = 0
        skipped_count = 0
        errors = []
        
        for row in payroll_data:
            try:
                cpf = row.get("cpf")
                if not cpf:
                    errors.append("Registro sem CPF encontrado")
                    continue
                
                cpf = cpf.strip().replace(".", "").replace("-", "")
                
                employee = db.query(Employee).filter(Employee.cpf == cpf).first()
                if not employee:
                    skipped_count += 1
                    continue
                
                unit = None
                cnpj_unidade = row.get("cnpj_unidade")
                if cnpj_unidade:
                    cnpj_unidade = cnpj_unidade.strip()
                    unit = db.query(Unit).filter(
                        Unit.cnpj_unidade == cnpj_unidade,
                        Unit.company_id == company.id
                    ).first()
                
                tipo_lancamento = row.get("tipo_lancamento", "OUTROS")
                
                payroll_item_type = db.query(PayrollItemType).filter(
                    PayrollItemType.code == tipo_lancamento
                ).first()
                
                if not payroll_item_type:
                    payroll_item_type = PayrollItemType(
                        code=tipo_lancamento,
                        description=tipo_lancamento
                    )
                    db.add(payroll_item_type)
                    db.commit()
                    db.refresh(payroll_item_type)
                
                payroll_item = PayrollItem(
                    billing_period_id=billing_period.id,
                    employee_id=employee.id,
                    unit_id=unit.id if unit else None,
                    payroll_item_type_id=payroll_item_type.id,
                    quantity=row.get("quantidade"),
                    amount=row.get("valor", 0),
                    source_column=tipo_lancamento
                )
                db.add(payroll_item)
                db.commit()
                
                imported_count += 1
                
            except Exception as e:
                errors.append(f"Erro ao importar lançamento CPF {row.get('cpf', 'N/A')}: {str(e)}")
                db.rollback()
        
        return {
            "status": "ok",
            "competencia": competencia,
            "imported": imported_count,
            "skipped": skipped_count,
            "errors": errors
        }
        
    except Exception as e:
        db.rollback()
        return {
            "status": "error",
            "message": str(e)
        }
    finally:
        db.close()
