# Plano de escala: novos ATS, Gupy e Lever

Estado em 2026-10-28. Na triagem, 98 vagas estavam elegíveis, de um catálogo coletado em 52 boards (45 Greenhouse, 7 Lever). Greenhouse envia e confirma de ponta a ponta; o Lever está travado por um erro de verificação depois do CAPTCHA. Este plano cobre os itens 2 a 4 da lista de escala; os itens 1 e 5 estão no prompt de execução ([CODEX_PROMPT.md](CODEX_PROMPT.md)).

Regras que valem para todos os itens:

- Nenhum envio real sem aprovação explícita do usuário na mesma sessão. Desenvolvimento e piloto usam `--execution review`, que preenche e revisa sem clicar em enviar.
- CAPTCHA, verificação humana, limite de taxa e proteção antirrobô não são contornados. Quando aparecem, a tentativa fica em espera humana (o modelo atual de `awaiting_human`).
- Uma plataforma só ganha envio automático depois de ter: coleta pela fonte oficial, preenchimento determinístico, reconhecimento do endpoint de envio em `submission_monitor.py` e confirmação independente (resposta HTTP + mensagem do portal, ou e-mail de recebimento).
- Respostas seguem o resolvedor atual (`easy_answers.py`, `automatic_answers.py`). Nada de afrouxar as regras de fato verificável e de experiência para fazer um formulário passar.

## Item 2: Ashby, Workable e SmartRecruiters

Objetivo: trazer vagas de startups e empresas médias que não usam Greenhouse/Lever, reaproveitando triagem, fila, respostas e monitor.

Ordem sugerida: Ashby, depois Workable, depois SmartRecruiters. O Ashby é o mais comum entre startups com vagas remotas para LATAM.

### Fase 0: verificação (pequena)

- Confirmar, contra a documentação oficial e chamadas reais, os endpoints públicos de listagem de vagas de cada plataforma. Endpoints que se espera encontrar, a confirmar:
  - Ashby: posting API por job board (`api.ashbyhq.com/posting-api/job-board/{board}`).
  - Workable: API pública de contas (`apply.workable.com/api/.../accounts/{conta}/jobs`).
  - SmartRecruiters: `api.smartrecruiters.com/v1/companies/{empresa}/postings`.
- Ler os termos de uso de cada uma. Se a listagem pública não puder ser consumida automaticamente, a plataforma sai do plano.
- Abrir manualmente 2 formulários de cada uma e registrar: campos, upload de currículo, perguntas customizadas, CAPTCHA, verificação por e-mail e como aparece a confirmação.
- Entregável: uma seção por plataforma em `docs/EXTERNAL_PILOT_STATUS.md` com endpoint confirmado, formato do formulário e riscos observados.

### Fase 1: coleta (pequena)

- Estender `external_catalog.refresh_board` e `external_discovery.board_spec` para `ashby:<board>`, `workable:<conta>` e `smartrecruiters:<empresa>`, normalizando para o mesmo formato de vaga (`platform`, `board`, `externalId`, `url`, `title`, `company`, `location`, `description`, `workplaceType`).
- `job_key` em `application_safety.py` precisa reconhecer as novas URLs para a proteção contra duplicata.
- Reaproveitar `qualify` sem mudanças de regra.
- Aceite: `cli.external discover --board ashby:<x>` traz vagas, a triagem roda, e nenhum envio é possível ainda (`platform_hold` ativo por padrão para plataformas novas).

### Fase 2: preenchimento em modo revisão (média, por plataforma)

- Um adaptador por plataforma no padrão de `GreenhouseAdapter` (`inspect`, `fill`, `set`, `upload`, `review`, `run`), selecionado em `execute_external`.
- Se a plataforma expõe a definição do formulário por API (como o schema do Greenhouse), usar isso para a pré-checagem sem navegador (equivalente a `preflight_greenhouse`).
- Upload do currículo com verificação de que o arquivo foi aceito.
- Aceite: 3 vagas reais por plataforma chegam a `reviewed` com todas as respostas vindas do resolvedor e evidência salva.

### Fase 3: envio e confirmação (média)

- Reconhecer o POST de envio da plataforma em `submission_monitor.is_submission`, com teste usando servidor HTTP local, como já existe para Greenhouse e Lever.
- Confirmação: resposta HTTP de sucesso e mensagem explícita do portal amarradas à mesma vaga (`confirmation_matches_job`).
- Incluir o remetente de e-mail da plataforma na medição de resultados (`application_outcomes.py`) se os e-mails de confirmação vierem dela.
- Aceite: testes de envio único, segundo envio bloqueado e confirmação de outra vaga rejeitada.

### Fase 4: piloto supervisionado

- Com aprovação do usuário: 3 envios reais por plataforma, acompanhados. Só depois liberar a plataforma nos lotes (`batch --platform`).

## Item 3: Gupy

Objetivo: cobrir o mercado brasileiro, onde boa parte das vagas só existe na Gupy. Maior potencial e maior risco.

### Fase 0: viabilidade (decisão de seguir ou não)

Investigar e documentar antes de escrever código de envio:

- Busca pública de vagas: existe uma busca no portal de vagas da Gupy e páginas de carreira por empresa (`<empresa>.gupy.io`). Confirmar se há API pública de busca e o que os termos permitem.
- Conta do candidato: a candidatura exige login na Gupy. Testar se o login funciona no perfil de navegador automatizado. O login com Google foi bloqueado no navegador automatizado na POC original; login com e-mail e senha da própria Gupy pode ser necessário.
- Etapas: currículo e perfil ficam na Gupy, as perguntas variam por vaga, e muitas vagas têm **testes** (lógica, perfil comportamental, técnicos, vídeo). Testes são avaliação do candidato: não são respondidos pelo sistema e viram espera humana.
- Proteção antirrobô e limite de taxa observados.
- Critério de decisão: seguir só se a candidatura (sem os testes) puder ser feita com o navegador do usuário, logado por ele, sem contornar proteção. Caso contrário, a Gupy vira **só coleta** (fase 1) com fila para candidatura manual.

### Fase 1: coleta (pequena a média)

- Coletar vagas pela busca pública ou pelas páginas de carreira, normalizar para o formato de vaga e passar na triagem atual.
- Mesmo sem envio automático, isso já entrega valor: uma fila priorizada de vagas Gupy elegíveis para o usuário se candidatar.

### Fase 2: perfil (manual, uma vez)

- O usuário preenche e revisa o perfil na Gupy uma vez. O sistema não edita o perfil da plataforma.

### Fase 3: candidatura assistida (grande)

- Adaptador que abre a vaga logada, clica em candidatar, responde as perguntas com o resolvedor e para antes de qualquer teste.
- Testes e etapas de vídeo ficam como espera humana, com a vaga marcada para o usuário concluir.
- Confirmação pelo status da candidatura no painel da Gupy e pelo e-mail de confirmação.

### Fase 4: piloto

- 3 candidaturas assistidas com aprovação do usuário antes de qualquer lote.

Riscos: restrição de conta por automação, termos de uso, variação grande entre empresas e testes obrigatórios que limitam a automação. Por isso o plano trata a coleta como entrega independente.

## Item 4: destravar o Lever

Situação: `.local/external/lever-verification-hold.json` está ativo. Um envio real chegou ao portal depois do CAPTCHA humano e voltou HTTP 400 com erro de verificação de candidatura. Outro caso anterior teve bloqueio no cliente. Detalhes em `docs/EXTERNAL_PILOT_STATUS.md`. A trava existe para não queimar a conta nem repetir envios.

### Fase 1: diagnóstico sem envio

- Reunir a evidência das tentativas `uncertain` do Lever: metadados de transporte (URLs limpas, status, hashes de corpo), screenshots e mensagens do portal. Nunca o corpo da requisição ou token de CAPTCHA.
- Comparar com o fluxo do Greenhouse, que funciona: diferenças de headers, ordem de navegação, tempo entre carregar a página e enviar, campos ocultos do formulário.
- Separar por origem: vagas do Jobgether (agregador) contra vagas diretas (CI&T, Swile). Agregador pode ter regra própria de aceitação.
- Hipóteses a testar, nesta ordem: campo obrigatório oculto ou alterado pelo preenchimento; token de CAPTCHA expirado pelo tempo entre resolver e enviar; verificação ligada a sinais de automação do navegador.

### Fase 2: experimento controlado (requer aprovação do usuário)

- Controle manual: o usuário se candidata a uma vaga Lever no mesmo perfil de navegador, sem automação. Se esse envio também falhar, o problema é da conta ou do perfil, não do preenchimento.
- Se o controle passar: um único envio com o comando existente `cli.external diagnose-lever`, com a hipótese principal corrigida.

### Fase 3: decisão

- Se a causa for de preenchimento ou tempo: corrigir, cobrir com teste e desligar a trava com piloto de 3 envios.
- Se a causa for proteção antirrobô: não contornar. O Lever vira **modo assistido**: o sistema preenche e revisa, e o usuário resolve o CAPTCHA e clica em enviar. O monitor continua confirmando pelo portal.
- Aceite: causa documentada em `docs/EXTERNAL_PILOT_STATUS.md` e uma das duas saídas implementada.

## Esforço relativo

| Item | Esforço | Retorno esperado |
|---|---|---|
| 2: Ashby | Médio | Alto entre startups remotas |
| 2: Workable, SmartRecruiters | Médio cada | Médio |
| 3: Gupy, só coleta | Pequeno a médio | Alto (visibilidade do mercado BR) |
| 3: Gupy, candidatura assistida | Grande | Alto, mas limitado pelos testes |
| 4: Lever | Pequeno a médio | Alto (catálogo já existe) |
