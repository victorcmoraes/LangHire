# Operação do fork local

Este fork mantém o LangHire como base. A primeira alteração trata de tentativas duráveis, confirmação independente e intervenção humana. Ela não declara o piloto real concluído.

## Estado desta entrega

- Um worker por perfil, com bloqueio entre processos.
- Intenção persistida antes de encaminhar um POST de candidatura reconhecido. Limite inicial de dez tentativas/dia, contabilizado no fuso America/Fortaleza. O teto máximo do código continua em 200.
- Evidências e eventos separados por tentativa, em dados locais.
- Confirmação exige resposta HTTP de sucesso do envio observado e mensagem explícita do portal. Um erro visível, JSON de rejeição ou apenas a declaração do modelo não confirma envio.
- Um segundo POST de envio na mesma tentativa é bloqueado antes da rede. POSTs não reconhecidos ficam bloqueados para revisão do endpoint.
- CAPTCHA permanece aguardando confirmação humana, sem timeout da espera e sem nova inferência durante a pausa. Confirmar libera a conferência da mesma tentativa; não clica novamente em enviar.
- Após queda, tentativas interrompidas ficam incertas. Uma confirmação já persistida é reaplicada à projeção jobs.json, sem novo envio. Uma sessão de CAPTCHA antiga não é presumida válida.
- A recuperação não interfere num worker que ainda detenha o perfil. A limpeza de navegadores considera somente o perfil deste fork; o fallback que encerrava todo Chrome foi removido.

O monitor reconhece endpoints de envio de Lever, Greenhouse e padrões de LinkedIn. Isso não equivale a cobertura validada de todos os formulários desses fornecedores. Endpoints auxiliares desconhecidos podem bloquear uma candidatura antes de qualquer envio; a correção deve adicionar um caso de teste para o endpoint, sem liberar POSTs genericamente.

## Windows: comandos

Requisitos: Python 3.13 gerenciado por uv, Node para a interface e Chrome para os testes locais. As versões Python estão fixadas em uv.lock; as dependências da interface estão em package-lock.json.

Na raiz do repositório:

```powershell
uv sync --frozen --group dev
npm.cmd ci --ignore-scripts
.\langhire.cmd doctor
.\langhire.cmd status
.\langhire.cmd holds
.\langhire.cmd confirm ID_DA_TENTATIVA
```

O launcher usa a pasta .local deste checkout e desativa a telemetria do browser-use. Nenhum dado de candidato, currículo, perfil de navegador ou comprovante deve ser versionado.

Se o backend desktop estiver em uso, a lista de vagas também apresenta as intervenções e o botão “Resolvi o desafio”. O endpoint autenticado POST /apply/interventions/{id}/confirm apenas registra essa confirmação humana.

Para um terminal iniciado sem o launcher, indique a mesma pasta antes de rodar o comando de intervenção:

```powershell
$env:LANGHIRE_DATA_DIR = "$PWD\.local"
uv run --frozen python cli/interventions.py list
uv run --frozen python cli/interventions.py confirm ID_DA_TENTATIVA
```

## Importação da POC

Execute uma vez, para um destino sem dados operacionais existentes:

```powershell
uv run --frozen python cli/import_poc.py --source CAMINHO_DA_POC\.local --destination "$PWD\.local"
```

A importação usa backup SQLite para incluir o WAL comprometido, copia cadastro/tracker e preserva a fonte. Os documentos originais continuam referenciados no snapshot privado. Currículos são copiados para .local/resumes. O histórico de tentativas continua disponível no snapshot, e os contadores antigos são transportados sem duplicar URLs alternativas da mesma vaga.

Binance fica como histórico anterior ao novo piloto. Vagas enviadas/incertas e suas URLs alternativas são protegidas. As demais entram bloqueadas para nova conferência de elegibilidade, evitando que importar uma fila dispare candidaturas. Fatos com país/empresa e contradições documentais mantêm o contexto no cadastro de origem; anos de experiência não são inferidos.

A cópia automática do perfil de navegador da POC não foi implementada: o novo diretório fica isolado. Login humano será necessário quando o piloto começar.

## Modelo local e liberação

O CLI usa agora a fábrica de modelos configurada, em vez de sempre criar um cliente AWS. Ollama usa saída estruturada nativa, contexto configurável e think=false. Para modelos locais sem visão, o agente usa o DOM. A execução exclui ferramentas de avaliação de JavaScript e leitura/escrita arbitrária de arquivos.

Diagnóstico reproduzível, sem candidaturas reais:

```powershell
uv run --frozen python cli/benchmark_navigation.py --model qwen3:4b
```

O diagnóstico usa candidato fictício e intercepta toda navegação. Confere os valores efetivamente recebidos pelo formulário e exige um único envio. Passar esse caso **não aprova** o modelo para responder formulários reais.

Na primeira aferição, o contexto de 32 mil tokens e o parâmetro antigo de timeout produziram três timeouts. Com 16 mil tokens, think=false e o parâmetro atual, o Qwen3 4b completou o formulário básico em 156,67 segundos. A utilização da RX 7600 foi observada, mas o desempenho ainda precisa ser medido em formulários representativos.

Nesta instalação, submission_enabled permanece false em .local/settings.json. O comando apply e o backend rejeitam envios enquanto essa configuração estiver desligada. A próxima etapa é integrar/validar o resolvedor de fatos e respostas, repetir a avaliação rotulada e executar o piloto real. Não habilitar 200/dia apenas porque o diagnóstico de navegação passou.

Permanecem pendentes: validação de cem perguntas, qualificação das vagas importadas, CAPTCHA de produção, anexos/controles condicionais dos portais reais e as três confirmações em duas empresas. O objetivo seguinte continua sendo dez confirmações novas, abrangendo os três canais.

## Verificação

```powershell
uv run --frozen python -m pytest tests/backend
npm.cmd run build
npm.cmd test -- src/components/HumanInterventions.test.tsx src/pages/jobs/PendingTab.test.tsx src/lib/api.test.ts
```

Os testes novos usam Chromium real com formulários interceptados localmente: confirmação, sucesso visual com HTTP 500, rejeição JSON com HTTP 200, erro de verificação, CAPTCHA seguido de confirmação/erro, tentativa repetida, limite entre reinícios e na virada do dia, importação e recuperação de confirmação persistida.

A suíte completa da interface tem oito falhas preexistentes na checagem de traduções: os arquivos jobs.json de italiano e chinês não existem no commit de origem. A interface possui fallback para inglês. Esses arquivos não foram inventados nem os testes desativados nesta alteração.


## Piloto supervisionado e perfil persistente

O diagn?stico b?sico do Qwen3 8b passou em 119,56 segundos e tr?s passos, contra 156,67 segundos do 4b. O 8b ? a configura??o local selecionada; a avalia??o geral de respostas continua sem aprova??o. Os resultados s?o de um formul?rio fict?cio de dois campos, sem candidaturas reais.

O browser-use copia perfis Chrome por padr?o. O worker agora inicia um contexto persistente Playwright pr?prio e conecta o browser-use por CDP local, preservando cookies no perfil configurado. Um teste abre duas sess?es e verifica a persist?ncia de cookie. O bloqueio exclusivo continua obrigat?rio.

Para uma candidatura individual revisada, sem habilitar a fila geral:

```powershell
.venv\Scripts\python.exe cli/pilot.py URL_DO_FORMULARIO
```

O operador local prepara campos sustentados por fatos em `control.json`, confere os valores aceitos em `review-N.json` e libera o digest daquela revis?o. O comando n?o gera respostas com o modelo. A inten??o ? persistida antes de um ?nico clique; CAPTCHA mant?m a mesma sess?o aberta e usa o comando `confirm` j? descrito. N?o iniciar outro worker enquanto houver interven??o aberta.

A prepara??o distingue upload e inicializa??o de widgets dos endpoints de envio. As verifica??es nativas do portal seguem normalmente; telemetria LinkedIn ? bloqueada sem transformar a candidatura em falha. CAPTCHA oculto n?o gera interven??o. O autocomplete confere cidade, estado e pa?s aceitos, tolerando o enriquecimento posterior de metadados do Lever. Nenhuma dessas mudan?as resolve CAPTCHA automaticamente.

QuintoAndar permanece pendente por decis?o do usu?rio: CPF e nacionalidade ausentes. A primeira tentativa liberada no novo piloto ? CI&T 31846 e est? aguardando interven??o humana; n?o h? confirma??o de envio registrada at? esta atualiza??o. Prepara??es encerradas antes do clique foram preservadas com zero inten??es de envio. O teto de 200 permanece bloqueado at? aprova??o do piloto.
