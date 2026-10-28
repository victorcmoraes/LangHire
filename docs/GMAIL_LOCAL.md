# Gmail local e propostas automáticas

O executor acessa o Gmail diretamente pela API oficial, com OAuth de aplicativo
desktop e permissão somente de leitura. A conexão do plugin desta conversa não
é uma credencial reutilizável pelo programa local. O Qwen não recebe tokens,
corpos de email ou códigos de verificação.

## Conectar a conta

É necessário criar um cliente Google pertencente ao usuário antes de autorizar
a conta. O fluxo segue a [documentação de OAuth para desktop](https://developers.google.com/identity/protocols/oauth2/native-app)
e o [quickstart da Gmail API](https://developers.google.com/workspace/gmail/api/quickstart/python).

1. No [Google Cloud](https://console.cloud.google.com/), selecione um projeto seu
   ou crie um para o LangHire local.
2. Ative a [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com).
3. Na Google Auth Platform, configure Branding com o nome do aplicativo e seu
   email. Para uma conta pessoal `@gmail.com`, use público **External** e adicione
   seu próprio email em **Test users** enquanto o projeto estiver em testes.
4. Em [Clients](https://console.cloud.google.com/auth/clients), crie um cliente do
   tipo **Desktop app / App para computador** e baixe seu JSON.
5. Salve o arquivo em `.local/mail/google-client.json`, no diretório do fork.
   Não o coloque no Git ou nesta conversa.
6. No PowerShell, em `D:\Code\LangHire`, execute:

```powershell
.venv\Scripts\python.exe -m cli.mail connect
.venv\Scripts\python.exe -m cli.mail check
```

Também é possível indicar o arquivo baixado diretamente:

```powershell
.venv\Scripts\python.exe -m cli.mail connect --client "C:\caminho\do\cliente.json"
```

O comando abre o navegador normal para login e consentimento, com retorno em
`127.0.0.1` e porta aleatória. Ele confere se a conta autorizada é a mesma do
cadastro confirmado. Os tokens são criptografados pelo DPAPI do usuário Windows
e ficam em `.local/mail/gmail-token.dpapi`. O escopo solicitado é apenas
`https://www.googleapis.com/auth/gmail.readonly`; essa permissão pode ler a caixa,
e a aplicação restringe suas consultas às verificações de candidatura. Não são
solicitadas permissões de envio, alteração de mensagens ou exclusão.

Em projetos **External / Testing**, o Google emite refresh tokens com validade
de sete dias para esse escopo; quando expirarem, execute `connect` novamente.
Essa é uma [regra do Google](https://developers.google.com/identity/protocols/oauth2#expiration),
não um timeout do worker. A publicação de um aplicativo para outros usuários tem
requisitos próprios e não faz parte desta configuração local.

## Verificação por email

Na versão atual, a integração cobre o formato de código de oito caracteres
observado na Greenhouse. Ela só funciona dentro de uma sessão viva, após uma
requisição final nativa ter retornado HTTP 428 e a precondição esperada.

O código verifica a conta conectada, o destinatário mostrado no formulário, o
remetente permitido, o resultado de autenticação DKIM, a empresa do assunto e
o horário da mensagem. Consulta no máximo dez mensagens por busca. Mensagens
ambíguas, de outra empresa, de outro destinatário, antigas ou já utilizadas não
são preenchidas automaticamente. Não são lidos anexos.

O código fica apenas na memória e nos campos mascarados da página. O histórico
guarda o ID da mensagem e o horário, sem o código ou seu corpo. A continuação
reconfere o formulário, cria uma tentativa vinculada e consome outra unidade do
limite diário antes de um único clique. A confirmação continua exigindo resposta
aceita e comprovante na rota da própria vaga.

Sem autorização local, sem mensagem válida ou com falha de acesso, a mesma página
fica aguardando intervenção manual. CAPTCHA continua manual. Uma sessão encerrada
ou uma candidatura incerta não é reconstruída para obter outro código.

Comandos de diagnóstico:

```powershell
.venv\Scripts\python.exe -m cli.mail setup
.venv\Scripts\python.exe -m cli.mail status
.venv\Scripts\python.exe -m cli.mail check
.venv\Scripts\python.exe -m cli.local doctor
```

## Perguntas desconhecidas

O worker Easy Apply e os adaptadores externos chamam o Qwen3 8b automaticamente
para gerar uma proposta quando não encontram um fato direto ou uma resposta
aprovada. A primeira aprovação de uma resposta nova continua necessária; propostas
não são usadas como se fossem fatos confirmados. Campos sem suporte permanecem
pendentes, e uma proposta não confirma nacionalidade, vínculo, parentesco,
remuneração, disponibilidade ou qualquer experiência ausente do cadastro.

Configuração em `.local/settings.json`:

```json
{
  "automation_answers": {
    "enabled": true,
    "max_new_per_run": 8,
    "retry_seconds": 900
  }
}
```

O provedor deve ser `ollama`, com modelo `qwen3:8b` e endereço local. Há uma única
inferência por vez, inclusive entre processos. Perguntas equivalentes compartilham
a proposta. Falta de informação não provoca novas chamadas a cada vaga; mudanças
nos fatos ou no código relevante invalidam o registro de geração. Falhas
transitórias podem ser tentadas depois do intervalo configurado.

O cadastro e arquivos de GitHub pertinentes sustentam a proposta. O texto da vaga
serve como contexto e não prova experiência do candidato. Fatos com escopo de
outra empresa são excluídos quando necessário. Uma aprovação humana recebida
durante a inferência não é sobrescrita pelo resultado do modelo.

Essa integração está no código-fonte/CLI. O acesso real ao Gmail depende da
configuração e autorização acima; testes com respostas simuladas não substituem
uma validação em candidatura real.
