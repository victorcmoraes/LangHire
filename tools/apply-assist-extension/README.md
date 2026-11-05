# LangHire Apply Assist

Percorre a fila inteira (Lever, Greenhouse, Gupy, Ashby, Workable e LinkedIn Easy Apply) no seu próprio
Chrome. A extensão preenche as respostas confirmadas e lista o que falta. Você revisa, resolve CAPTCHA ou
código por e-mail e clica em enviar. Depois da confirmação, a próxima vaga abre sozinha. Não há limite diário.

## Instalar (uma vez)

1. `chrome://extensions` → ativar **Modo do desenvolvedor**.
2. **Carregar sem compactação** → escolher esta pasta (`tools/apply-assist-extension`).
   O ID fixo é `flpmcolipdjehnjmhpkcamihempmdcic`: o servidor só responde a essa origem.
3. Fixe o ícone na barra. Depois de atualizar o código, clique em ↻ na extensão.

## Usar

```
uv run python -m cli.external assist            # lever, greenhouse, gupy, ashby, workable, linkedin
uv run python -m cli.external assist --platform gupy --platform linkedin
```

O primeiro link abre no navegador. Clicar no ícone da extensão preenche a vaga aberta ou, fora de uma vaga,
abre a próxima da fila.

| Plataforma | O que a extensão faz |
|---|---|
| Lever, Greenhouse | Preenche o formulário inteiro. Você resolve o CAPTCHA ou o código e clica em Submit. |
| LinkedIn Easy Apply | Avança as etapas até "Enviar candidatura". Quando falta uma resposta, ela para; preencha e clique em **Continuar preenchimento**. |
| Gupy | Abre a candidatura. Entre na sua conta Gupy, que preenche o perfil. Em cada etapa com perguntas, clique em **Preencher esta etapa** e, no fim, em **Finalizar candidatura**. |
| Ashby, Workable | Preenchimento genérico dos campos vazios. Botões Sim/Não e listas customizadas podem precisar de clique seu. |

- Confirmação reconhecida → `confirmed`. Erro do portal ou "Portal não confirmou" → `uncertain`, sem reenvio.
- Página fechada antes do envio → volta para a fila quando o servidor reinicia.
  Clique de envio sem resultado → `uncertain`.
- Depois de mudar `FIELDS` em `external_apply.py` ou `easy_apply.py`, rode
  `uv run python -m cli.external assist --write-extension` e recarregue a extensão.

## De onde vêm as vagas

```
uv run python -m cli.external sources --register   # descobre e registra boards de empresas que contratam no Brasil
uv run python -m cli.external discover              # coleta as vagas de todos os boards registrados e faz a triagem
uv run python -m cli.external linkedin-collect --title "backend developer" --external   # LinkedIn, inclusive "Candidatar-se" externo
```

`sources` lê o portal do Gupy, as issues do GitHub (`backend-br/vagas`, `frontendbr/vagas`), Himalayas, a busca
da Workable, o Get on Board e os destinos externos do LinkedIn. Só registra boards que tenham vagas elegíveis
novas.
