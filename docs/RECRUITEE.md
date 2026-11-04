# Recruitee

O LangHire coleta apenas ofertas publicadas pela Careers Site API oficial, com
`recruitee:empresa-ficticia` como identificador do quadro. As vagas passam pela
mesma triagem de localização, modalidade e requisitos das demais plataformas.

`external run --platform recruitee --execution review` prepara o formulário e
salva respostas, anexos e SHA-256 do currículo nas evidências. Perguntas
obrigatórias sem resposta ficam pendentes no mesmo AnswerBook. O modo `submit`
envia por HTTP uma única vez, sem navegador ou CAPTCHA, respeitando o limite
diário. A criação de candidato dispara o email de confirmação do empregador.
Respostas opcionais não resolvidas são omitidas.

A exigência anunciada do cabeçalho `X-Careers-Sites-Token` começa em
**2027-02-10**. HTTP 401/403 bloqueia a tentativa e grava
`external/recruitee-verification-hold.json`, pausando novos envios da plataforma.
A integração não provisiona tokens. Erros 400/422 bloqueiam sem repetição;
timeout, falha de conexão, 429, 5xx ou resposta sem candidato deixam o resultado
incerto. Um envio já tentado nunca é repetido automaticamente.

Referência: [Careers Site API — candidaturas](https://docs.recruitee.com/reference/offersoffer_idcandidates).
