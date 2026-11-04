# Prompt para o Codex: escalar a coleta e a candidatura

Cole o bloco abaixo no Codex, aberto na raiz do repositório `D:\Code\LangHire`.

---

Você vai trabalhar no LangHire, um fork local de automação de candidaturas. Objetivo: aumentar o número de vagas elegíveis e de candidaturas confirmadas, sem enfraquecer as garantias de segurança que já existem.

## Leia antes de começar

- `docs/FORK_LOCAL.md`: operação, comandos e o modo de respostas autônomas.
- `docs/EXTERNAL_PILOT_STATUS.md`: estado do Greenhouse e do Lever, incluindo a trava do Lever.
- `docs/scaling/PLANO_ESCALA.md`: plano detalhado dos itens 2, 3 e 4 abaixo. Siga as fases e os critérios de aceite de lá.
- Código central: `backend/core/external_catalog.py`, `external_discovery.py`, `external_qualification.py`, `external_batch.py`, `external_apply.py`, `greenhouse_apply.py`, `submission_monitor.py`, `easy_answers.py`, `automatic_answers.py`, `application_safety.py`. CLI em `cli/external.py`.

## Estado atual

- 1.724 vagas no catálogo (`.local/jobs.json`), de 52 boards em `.local/external/board-registry.json` (45 Greenhouse, 7 Lever). Cinco boards concentram 88% do volume.
- Triagem (`.local/external/triage.json`): 98 elegíveis, 906 descartadas, das quais 756 por `outside_tracks`.
- Greenhouse envia e confirma de ponta a ponta. O Lever está travado por `lever-verification-hold.json`.
- 23 candidaturas confirmadas no total.

## Regras inegociáveis

1. **Não envie candidaturas.** Nunca rode `--execution submit`, `batch --execution submit` nem `diagnose-lever` sem o usuário aprovar explicitamente naquela conversa. Para testar o fluxo, use `--execution review`, que preenche e revisa sem clicar em enviar.
2. **Não contorne proteção.** CAPTCHA, verificação humana, limite de taxa e antirrobô viram espera humana, nunca bypass. Respeite os termos de uso de cada plataforma; se uma fonte proibir coleta automática, pare e reporte.
3. **Não afrouxe as respostas.** As regras de `easy_answers.py` e `automatic_answers.py` (fatos verificáveis só do cadastro, experiência sem evidência fica pendente, travas contra texto copiado da vaga, números e histórias inventados) não podem ser relaxadas para destravar formulários.
4. **Dados pessoais.** O repositório `origin` é público. Nada de `.local/`, currículo, e-mail, telefone, CPF ou respostas pessoais em código, testes, docs ou commits. Use dados fictícios nos testes.
5. **Estilo.** Siga o estilo do código ao redor: Python denso, comentários curtos que explicam o porquê, testes em `tests/backend/core/` com o padrão dos existentes.
6. **Qualidade.** Antes de cada commit: `uv run pytest tests/backend`, `npx vitest run`, `npx eslint .` e `npx tsc -b` passando. Um commit por item concluído, com mensagem no estilo do histórico (frase em inglês, sem prefixo).

## Tarefas, nesta ordem

### Item 5: revisar o filtro de trilhas (comece por aqui)

1. Gere um relatório dos descartes `outside_tracks` e `outside_seniority_or_specialty`: títulos agrupados por padrão (full stack, plataforma, SRE, QA, mobile, dados, IA etc.), com contagem e 3 exemplos cada. Salve em `.local/reports/track-review.md`.
2. **Pare e mostre o relatório ao usuário.** Ele decide quais grupos entram.
3. Depois da decisão: mova `TRACK` e `EXCLUDED` de `external_qualification.py` para configuração (por exemplo `job_tracks` em `.local/settings.json`), com os valores atuais como padrão, para o usuário ajustar sem mexer em código. Aplique os grupos aprovados, rode `cli.external triage` e reporte quantas vagas viraram elegíveis.

Aceite: trilhas configuráveis, testes cobrindo padrão e configuração, e contagem antes/depois.

### Item 1: mais empresas no Greenhouse e no Lever

1. Fontes de novos boards, sem coleta agressiva:
   - Links de candidatura externa já coletados (vagas do LinkedIn e do Jobgether que apontem para `boards.greenhouse.io`, `job-boards.greenhouse.io` ou `jobs.lever.co`).
   - Um arquivo de sementes editável pelo usuário, como `.local/external/board-seeds.txt`, com um `plataforma:slug` ou URL oficial por linha.
2. Para cada candidato: validar com `refresh_board` (API pública), registrar só boards que respondem e têm ao menos uma vaga que passe em `TRACK` e `candidate_location`. Manter a concorrência atual (`workers=3`) e cache, sem martelar as APIs.
3. Expor como comando, por exemplo `cli.external boards suggest` (lista candidatos validados) e `cli.external boards add` (grava no registro). O registro continua sendo a fonte de verdade.
4. Rode a sugestão com as fontes existentes e reporte quantos boards e vagas elegíveis novas apareceriam. Não grave no registro sem mostrar a lista ao usuário.

Aceite: comando com testes (API simulada), relatório de candidatos e nenhuma alteração no registro sem aprovação.

### Item 4: diagnóstico do Lever

Siga a seção "Item 4" de `docs/scaling/PLANO_ESCALA.md`. Faça só a Fase 1 (diagnóstico sem envio) e entregue as hipóteses ordenadas com evidência. As Fases 2 e 3 exigem aprovação do usuário.

### Item 2: Ashby, Workable e SmartRecruiters

Siga a seção "Item 2" do plano. Comece pela Fase 0 do Ashby e só avance de fase com o critério de aceite cumprido. Plataforma nova nasce com envio bloqueado (`platform_hold`) até o piloto aprovado.

### Item 3: Gupy

Siga a seção "Item 3" do plano. Faça a Fase 0 (viabilidade) e **pare para o usuário decidir** entre candidatura assistida e só coleta. A Fase 1 (coleta) pode ser entregue mesmo que a candidatura automática seja descartada.

## Ao final de cada item, reporte

- O que mudou e onde (arquivos e comandos novos).
- Números antes e depois (boards, vagas no catálogo, elegíveis).
- Resultado dos testes.
- O que ficou pendente de decisão do usuário.
