# Revisão do fluxo Easy Apply

## O que manter

- Interface React: cadastro, configuração Ollama, Jobs, Q&A, memória e logs.
- Backend FastAPI, catálogo de vagas e controle de fila existentes.
- Perfil Chrome persistente, exclusão entre workers e histórico SQLite de tentativas.
- Monitor de envio e intervenção humana; reconhecimento de endpoints não comprova cobertura de um formulário real.

## Caminho atual

`PendingTab.tsx` → `POST /apply/start` → `apply_jobs.worker` → `apply_to_job` → `_run_observed_agent` → `browser_use.Agent`.

A tela envia `mode: "all"`, inclusive ao selecionar uma vaga. O CLI possui `--mode easy`, mas o caminho normal continua entregando ao agente a instrução de clicar em Easy Apply e preencher o formulário. Não existe executor específico das etapas do modal LinkedIn.

`backend/sources/plugins/linkedin.yaml` possui busca com localização, data, senioridade e modalidade; não possui filtro Easy Apply. Seu prompt de coleta pede vagas internas e externas e ainda inclui uma visita ao Gmail. Esse passo não é necessário para nosso fluxo e deve ser removido antes de usar essa coleta.

`build_memory_context` injeta cadastro e todas as respostas disponíveis no prompt. A tabela `qa_repository` oferece edição, normalização e agrupamento por semelhança, mas não resolve campos diretamente nem isola respostas por país, moeda, período ou versão do cadastro. As propostas do agente não são promovidas automaticamente a fatos pelo nosso worker.

## Estado observado na instalação

- Backend autenticado e frontend local em execução. Desktop Tauri indisponível sem Rust e sidecar compilado; a inspeção usa Chrome e uma ponte local privada apenas para obter o token de autenticação real.
- Cadastro, currículo, Ollama e Chromium configurados.
- Login LinkedIn não detectado no perfil do fork; onboarding incompleto.
- Banco ativo de Q&A: zero perguntas. Memória ativa: zero registros. A presença de fatos importados não significa que o resolvedor esteja integrado.
- Catálogo contém 1.482 registros de URLs importadas, incluindo aliases; esse total não representa vagas únicas elegíveis. Nenhuma está pendente na fila atual.
- Um envio histórico e zero confirmações novas do piloto. Os registros incertos continuam protegidos.
- Corrigida a observação de `.local/` pelo Vite, que derrubava o servidor ao encontrar arquivos de cache Chrome ocupados no Windows.

## Continuação proposta

1. Expor um modo Easy Apply na interface e na coleta. Usar o filtro correspondente na busca; conferir o botão real antes de entrar na fila. Redirecionamento externo vira pendência, sem exploração de ATS.
2. Implementar um executor LinkedIn reaproveitando o worker, com estados: abrir vaga, abrir modal, ler etapa, resolver campos, preencher, conferir, avançar, revisar, enviar uma vez e confirmar. Seletores DOM por rótulos e estados, com português e inglês.
3. Integrar os fatos confirmados ao Q&A contextualizado. Campos conhecidos recebem respostas diretas; o 8b pode propor interpretação de perguntas desconhecidas, sem decidir navegação ou inventar fatos. Informação insuficiente vira pendência reutilizável.
4. Testar o mesmo executor em fixtures de várias etapas, perguntas adicionais, currículo, revisão, CAPTCHA e retomada; depois executar uma candidatura real e repetir o fluxo em outras vagas sem código específico por empresa.

O primeiro incremento deve demonstrar preenchimento e revisão sem envio em um Easy Apply real. A liberação de volume continua dependendo de confirmações verificadas.
