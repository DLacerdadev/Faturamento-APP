# Feature Specification: Harness de Testes (Fundação)

**Feature ID**: 006-harness-testes
**Created**: 2026-07-25
**Status**: Draft
**Spec File**: spec.md
**Onda de execução**: 0 (fundação — roda primeiro, sozinha, e é mergeada antes de tudo)
**Agente**: 1 agente dedicado

## Overview

Hoje o projeto **não tem framework de testes instalado** (`pytest` não está em
`requirements.txt`/`pyproject.toml`; não há `tests/`, `conftest.py` nem banco de teste). A regra
de execução desta iniciativa exige que **cada agente rode testes para confirmar que sua spec
está 100% concluída** — o que é impossível sem um harness comum. Esta spec cria essa fundação:
`pytest` + `conftest.py` + estrutura `tests/` + fixtures de banco isolado + comando único de
execução, servindo de base para as specs 007–010 e implementando o esqueleto da spec 005
(suíte de cálculos).

## User Scenarios & Testing

### Primary Flow

1. Desenvolvedor roda `pytest` (ou `python -m pytest`) na raiz do projeto.
2. O harness cria um banco SQLite temporário isolado (não toca `app.db`), sobe `Base.metadata`,
   injeta um `TestClient` do FastAPI e roda toda a suíte offline (sem Senior, sem SMTP, sem LLM).
3. A saída mostra os testes passando em menos de 2 minutos.

### Acceptance Scenarios

- **Scenario 1**: Given o repositório recém-clonado, When rodo o comando de teste documentado,
  Then `pytest` executa e um teste-smoke (app sobe, rota `/health` responde 200) passa.
- **Scenario 2**: Given uma fixture `db_session`, When um teste cria registros, Then eles vivem
  num banco temporário descartado ao fim, sem efeito colateral em `app.db`.
- **Scenario 3**: Given uma fixture `client` (TestClient autenticado por papel), When um teste
  chama endpoint protegido como `gestor`, Then a permissão é resolvida sem servidor externo.

### Edge Cases

- Rodar sem `.env`/sem Senior: DEV_MODE ativo, nenhuma chamada externa.
- Rodar em Windows (dev) e Linux (prod/docker): caminhos e encoding consistentes.
- Paralelismo de testes não corrompe o banco (cada worker/arquivo usa DB próprio ou transação).

## Functional Requirements

- **FR-1**: Adicionar `pytest` (e `httpx` para `TestClient`, se necessário) às dependências.
- **FR-2**: Criar `tests/` com `conftest.py` expondo fixtures: `test_engine`, `db_session`,
  `client` (TestClient), e `client_as(role)` para autenticar como operador/gestor/admin.
- **FR-3**: Fixture de banco isolado (SQLite temporário/in-memory) que cria e derruba schema por
  sessão de teste, sem tocar `app.db`.
- **FR-4**: Configuração de teste em arquivo próprio (`pytest.ini`) para NÃO editar `pyproject.toml`
  fora de dependências (minimiza conflito de merge).
- **FR-5**: Um teste-smoke (`tests/test_smoke.py`) que garante boot do app e `/health` 200.
- **FR-6**: Helpers de fixtures anônimas (folha sintética mínima, sem PII) reutilizáveis por 007–010.
- **FR-7**: Documentar o comando único de teste no `RUNBOOK.md` (seção "Testes").
- **FR-8**: Todos os testes rodam offline (sem Senior/SMTP/LLM), determinísticos, < 2 min.

## Success Criteria

- **SC-1**: `pytest` roda do zero num clone limpo e o smoke passa.
- **SC-2**: Nenhum teste toca `app.db` real (verificável por checksum antes/depois).
- **SC-3**: As specs 007–010 conseguem importar `conftest` e escrever testes sem re-configurar harness.

## Key Entities

- **Fixtures de teste** (`conftest.py`): `test_engine`, `db_session`, `client`, `client_as`.
- **Dados sintéticos**: folha/funcionários fake anonimizados para alimentar cálculos.

## Assumptions

- SQLite em teste é suficiente (paridade com dev). Cálculos não dependem de features PG-only.

## Out of Scope

- Cobertura completa dos cálculos (isso é a spec 005 madura); aqui entra só o esqueleto + smoke.
- CI/pipeline remoto (pode vir depois; foco é o comando local que os agentes usam).

## Dependencies

- Nenhuma spec anterior. É a base de todas as outras.

## File Ownership / Boundaries

**Cria (exclusivo)**: `tests/` (todos os arquivos), `tests/conftest.py`, `tests/test_smoke.py`,
`pytest.ini`.
**Edita (permitido nesta onda sequencial)**: `requirements.txt` (add pytest/httpx), `RUNBOOK.md`
(seção Testes). *Como é Onda 0 e roda sozinha, editar arquivos de integração é seguro.*
**Não toca**: `app/` (código de produção) — exceto leitura.

## Test Plan (definição de "100% concluída")

- `pytest` verde com o smoke e as fixtures exercitadas por pelo menos 1 teste cada.
- Checksum de `app.db` inalterado após a suíte.
