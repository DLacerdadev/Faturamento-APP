# Telos Consultoria – Sistema de Gestão

## Tecnologias Utilizadas

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.11 + FastAPI |
| Templates | Jinja2 (HTML server-side) |
| Banco de dados | SQLite (dev) / PostgreSQL (produção) |
| ORM | SQLAlchemy |
| Processamento de dados | Pandas + OpenPyXL |
| Integração ERP | Senior – API REST + Serviços SOAP |
| Integração banco legado | SQL Server via pyodbc |
| Autenticação | Sessão por token (itsdangerous) |
| Hash de senhas | passlib (pbkdf2_sha256) |
| Servidor web | Uvicorn |

---

## Funcionalidades

### Gerar Faturamento FEMSA

Busca os dados de folha de pagamento diretamente no Senior ERP via SOAP, agrupa os eventos por funcionário e gera um arquivo Excel no formato padronizado FEMSA. O arquivo inclui proventos, descontos, horas extras, adicional noturno, exames médicos e EPIs do período. Funcionários ativos e demitidos são incluídos. O resultado pode ser baixado como `.xlsx` ou em `.zip` quando há documentos de EPI anexados.

---

### Cadastro e Listagem de Exames

Permite registrar exames médicos (admissionais, periódicos, demissionais) vinculados a funcionários e centros de custo. Os valores de cada exame são armazenados e utilizados automaticamente no faturamento FEMSA do mês correspondente, preenchendo a coluna *(FAT) EXAMES MÉDICOS* de cada colaborador.

---

### Cadastro e Listagem de EPIs

Permite registrar pacotes de compra de Equipamentos de Proteção Individual (EPIs) por período, com upload dos documentos comprobatórios (notas fiscais, recibos). Os documentos ficam armazenados e são incluídos automaticamente no arquivo `.zip` gerado junto com o faturamento FEMSA do mês.
