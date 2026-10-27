# Easy Apply pelo desktop

## Executar

`desktop.cmd` configura a pasta privada `.local/`, gera o sidecar e abre o Tauri em desenvolvimento. `desktop.cmd build` gera o instalador Windows. O aplicativo instalado usa a pasta padrão do sistema quando `LANGHIRE_DATA_DIR` não estiver definido. Rust/MSVC e WebView2 foram instalados/verificados nesta máquina.

O token do Rust e os dados do backend respeitam o mesmo override. Não é necessária a ponte privada usada na inspeção anterior pelo Chrome. Login, coleta e candidatura compartilham o Chrome persistente e o bloqueio de perfil.

## Fluxo atual

1. Em Jobs, abrir o login do LinkedIn. Depois de autenticar, fechar somente a janela de login e verificar a sessão.
2. Coletar até dez vagas por execução. A busca restringe Easy Apply; o coletor confere o botão real e deduplica o ID. Vagas novas aguardam conferência de requisitos e localização; o catálogo antigo não é liberado em lote.
3. Selecionar a vaga, registrar trechos de elegibilidade e fatos usados. Moeda e período salarial só são preenchidos quando confirmados; a ausência de salário publicado não descarta a vaga.
4. Usar “Preencher e revisar sem enviar”. Perguntas desconhecidas ficam no Q&A. Propostas do 8b dependem de confirmação humana.
5. Quando o LinkedIn acrescentar histórico profissional ou acadêmico do próprio perfil, confirmar o conteúdo mostrado no Q&A. Essa confirmação considera o conteúdo: alterações no histórico exigem outra confirmação. Não aceitar dados incorretos; corrigir o perfil antes de preparar novamente.
6. Após resolver pendências, preparar novamente. A aprovação reabre somente preparações compatíveis, sem tocar em envios confirmados ou incertos.
7. Preparar três vagas distintas antes do primeiro envio. Cada liberação individual exige a mesma revisão, respostas, fatos e PDF; mudanças impedem o clique.

A fila geral externa continua desabilitada. O fluxo Easy Apply não chama o agente genérico nem segue redirecionamentos externos. O modelo não navega nem clica.

No cartão de uma pergunta salarial, confirme a moeda e o período junto do valor.
Unidades já explícitas na pergunta ficam bloqueadas para edição. Essa confirmação
vale para a pergunta e as vagas já associadas; não define unidades para outras
perguntas ou novas vagas. Se faltarem fatos para o 8b propor uma resposta, o app
informa isso e mantém a pergunta pendente para preenchimento manual. Uma proposta
com referências ou formato inválidos também permanece sem uso.

## Catálogo de projetos do GitHub

Com GitHub CLI autenticado e Ollama local, execute
`.venv\Scripts\python.exe -m cli.github_inventory`. O comando consulta os
repositórios pertencentes à conta, lê documentação, manifests e amostras de
código e pede ao Qwen3 8b uma descrição com referências aos arquivos e ao commit.
Use `--limit N` para um teste pequeno. Resultados e contagens de tokens ficam em
`.local/github/`; o cache considera commit, modelo e versão do analisador.
Repositórios privados continuam locais. Arquivos de credenciais e diretórios
gerados são excluídos da seleção. A leitura é limitada a quatro arquivos e
8 mil caracteres por repositório, portanto não é uma auditoria completa.
O catálogo contém propostas: código existente não comprova autoria individual,
uso profissional, métricas ou decisões de POC. Essas afirmações não são
importadas automaticamente como respostas confirmadas para candidaturas.

O botão “Propor com 8b” usa o cadastro e amostras dos arquivos desse catálogo
para elaborar respostas abertas, sem exigir uma resposta idêntica pré-cadastrada.
As referências são limitadas aos IDs fornecidos ao modelo. Propostas apoiadas
em projetos passam por outra chamada local de revisão de evidências antes de
aparecer no app. As duas chamadas entram na contagem de uso do modelo.
Mudanças nos arquivos usados invalidam respostas derivadas. A confirmação humana
continua necessária; a descrição gerada no inventário não é tratada como fato.

No teste real da pergunta sobre pesquisa/POCs, o 8b gerou texto e referências
válidas, mas também acrescentou comparações e resultados que os arquivos não
comprovavam, inclusive após a revisão pelo próprio modelo. O rascunho dessa
pergunta foi corrigido editorialmente e permanece sem aprovação do candidato.
Portanto, a revisão local não comprova a veracidade de toda a narrativa e não
libera aprovação autônoma de respostas abertas.

## Implementação e evidências

- Estados do executor: abrir vaga/modal, identificar etapa, resolver/preencher, conferir, avançar, revisar e enviar uma vez.
- Formatos de contato, currículo, componentes RSC e operações auxiliares foram inspecionados na interface autenticada atual. A permissão distingue leitura, telemetria nativa, upload/vinculação de currículo e envio final. Operações desconhecidas continuam bloqueadas; identificadores observados possuem testes locais.
- O upload pelo seletor nativo exige resposta aceita do armazenamento e da vinculação do currículo. O hash do PDF é registrado; o nome de um documento antigo não comprova seu conteúdo.
- Alertas de sucesso e carregamento não são erros de formulário. Campos obrigatórios sem resposta, validações, controles não suportados e alterações de etapa inválidas viram pendências.
- Respostas aprovadas consideram pergunta, opções, país, idioma, empresa quando pertinente, moeda/período e fatos utilizados. Alterações explícitas no cadastro atualizam fatos sem promover valores padrão não alterados.
- Evidências ficam em `.local/evidence/ID/`. Relatórios JSON por execução ficam em `.local/reports/`, com duração, resultados, intenções de envio e contadores de regras/cache/modelo.
- A intenção é persistida antes do clique; somente resposta aceita do envio e confirmação do portal permitem “confirmada”. Após intenção sem confirmação, a vaga fica incerta e não pode ser repetida. O limite permanece dez intenções por dia no fuso America/Fortaleza.

## Validação e limite desta entrega

A interface desktop e o sidecar empacotado foram abertos e autenticados usando os dados reais. Os testes locais demonstram três revisões com o mesmo executor, envio único, respostas contextualizadas, retomada, deduplicação, bloqueio de concorrência e limite diário. Há casos de português/inglês, layout sem h1, nomes acessíveis extensos, carregamento, alertas, documentos e confirmação de histórico.

No portal real, a rodada supervisionada completou **três revisões e três envios
confirmados em três empresas**, com nova conferência dos status em uma sessão de
leitura. O formato final observado do LinkedIn possui fixture e validação da
identidade da vaga antes do envio. Operações desconhecidas continuam bloqueadas.
O piloto maior de dez confirmações entre plataformas permanece aberto, e o teto
de 200 ainda não foi liberado. Veja `EASY_APPLY_PILOT_STATUS.md` para o marco atual.

A suíte completa da interface mantém as oito falhas de traduções já existentes no upstream (jobs.json de italiano e chinês ausentes). Elas não foram ocultadas ou substituídas por traduções fictícias.
