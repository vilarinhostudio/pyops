## Quem sou

Arthur, arquiteto e desenvolvedor. Uso Blender + Bonsai BIM + IfcOpenShell para modelagem paramétrica não-destrutiva orientada a IFC. Este repositório é o **PyOps**, um addon de Blender que gera elementos IFC reais (não geometria decorativa) a partir de atributos pintados num mesh de referência.

Trabalho sozinho neste projeto. Não escreva para uma equipe — escreva para mim revisar rápido e seguir trabalhando.

## O que o PyOps é

Motor de operações: uma pasta é varrida recursivamente, cada `.py` com uma classe tendo `id` e `props_definition` vira uma operação disponível na UI, gerada a partir do dicionário. Fluxo não-destrutivo por duplicação — o pai é duplicado, modificadores são assados na cópia, a operação roda sobre ela.

A tese central: **atributo de malha é a interface universal**. Um número pintado numa face vira espessura de parede, índice de pavimento, largura de porta, material — e o mesmo mecanismo serve qualquer motor plugado depois (Bonsai, Ladybug, Honeybee).

## Estrutura do repositório

```
pyops/          addon Blender — __init__.py e infraestrutura compartilhada
  bim_helpers.py        ponte Blender ↔ Bonsai ↔ IfcOpenShell
  bim_profiles.py       componentes de aresta (coluna, viga, perfil)
  ladybug_helpers.py    ponte Blender ↔ Ladybug Tools
  honeybee_helpers.py   ponte Blender ↔ Honeybee-Radiance
  engine.py             motor de refresh, ordem de execução, profiling
  registry.py           descoberta de operações, ordenação por dependência
  properties.py, search.py, attributes.py, ui.py, ...

ops/             operações individuais (arquivos .py exec'd pelo loader)
  bonsai/        wall, slab, column, beam, panel, component, distribute, space
  ladybug/       graphs (gráficos climáticos), analysis (análise de face/vértice)
  honeybee/      radiance (daylight via IFC space + openings)
  arch/          space_syntax (grafo espacial via decomposição volumétrica)
  basic/         extrude, bevel — exemplos/testes do motor, não-IFC

drawing-bonsai/assets/   estilização SVG das plantas/cortes exportados pelo Bonsai
  patterns-source.svg   as 32 hachuras, desenhadas uma vez — fonte, não gerado
  patterns.svg          hachuras + variantes de cor, GERADO
  generate_hatches.py   gera patterns.svg + hatches.css a partir do source
  build_themes.py       monta os theme-*.css a partir de paletas + hatches.css
  theme-*.css           6 temas prontos (default/flatly/gruvbox × light/dark)
```

## Como o CSS funciona (resumo — ver [Estilização SVG por CSS](../vault-ifc/06-presentation/svg-css-styling.md) para o raciocínio completo)

O nome do material no Bonsai vira classe CSS, sem separadores: `concrete-fgprincipal1-pen050` → `material-concretefgprincipal1pen050`. Seletores casam por `[class*=...]`.

Regras de nomenclatura que **não podem ser quebradas** sem regenerar tudo:
- toda cor semântica termina em nível — `danger1`, `danger2`, `danger3` — nunca sem número, porque `danger` seria substring de `danger1`
- cor fixa sem nível termina em `0` — `blue0`
- prefixo `p` = só projeção, `c` = só corte, sem prefixo = ambos; regras sem prefixo levam `:not()` contra as prefixadas, senão vazam

`generate_hatches.py` lê de `patterns-source.svg` e escreve em `patterns.svg` — **nunca aponte os dois para o mesmo arquivo**, isso já causou reprocessamento acidental da própria saída (32 hachuras viraram 1952, arquivo foi a 38 MB).

## Fase atual

O motor e a maioria dos operadores Bonsai estão funcionando (parede, laje, abertura/distribuição, componente de perfil). Ladybug e Honeybee foram implementados mas **pouco testados** — dependem de instalação externa (pip + Radiance) que ainda está sendo configurada. Ver `STATUS.md` para o que está confirmado por operador.

CSS de plantas/cortes está funcional e testado interativamente (inspetor do navegador), com prefixo `p`/`c` e paleta com níveis.

Documentação de IFC (schema, Bonsai, IfcOpenShell) vive em repositório separado — não é escopo deste `CLAUDE.md`.

## Roadmap aprovado (ordem de dependência, não de prioridade)

1. **Vertex group como tabela de strings.** Blender não tem atributo string, mas nome de vertex group é string e seu índice é inteiro. Um par `atributo int → índice → nome` substitui todo campo de texto da UI por atributo pintável — material, classe IFC, nome de pavimento, o que for. Isso destrava um `MaterialLayerSet`/`MaterialConstituentSet` definido inteiramente por atributo.
2. **Ler o que o IFC já sabe.** Parar de pedir espessura, material cutter etc. quando o `IfcMaterialLayerSet` do type já carrega isso. Ler em vez de perguntar elimina a chance do input discordar do modelo.
3. **IDS como saída.** O PyOps sabe o que gerou; emitir IDS a partir disso valida o modelo contra a intenção declarada.

Os demais itens do roadmap anterior (Qto_*, Pset_PyOps de proveniência, util.selector como input, BVH para performance) ainda não foram aprovados — não assuma que valem, pergunte antes de implementar.

## Como responder

**Direto, sem preâmbulo.** Não abra explicando o que vai fazer — faça, e explique o que importa depois ou junto. Nunca "vou analisar o código e...".

**Curto por padrão.** Se a correção é uma linha, a resposta é uma frase. Reserve parágrafos para decisão de design que precisa de justificativa.

**Nunca reescreva o que não mudou.** Edição pontual (diff, str_replace) sempre que possível. Reescrever um arquivo inteiro por uma correção de 2 linhas já causou perda de trabalho anterior nesta base de código — aconteceu de verdade, mais de uma vez.

**Verifique antes de declarar pronto.** Rode o arquivo (import, lint, teste de sintaxe) antes de dizer que está corrigido. Se a verificação não é possível, diga explicitamente que não foi verificado.

**Não invente assinatura de API.** Blender, Bonsai, IfcOpenShell, Ladybug e Honeybee mudam nome de operador/módulo entre versões sem aviso — isso já custou várias rodadas de correção nesta sessão. Quando a assinatura não está confirmada, ou pergunte, ou escreva o código para descobrir em tempo de execução (`find_operator` por palavra-chave, `try/except` com fallback) em vez de chutar o nome exato. Ver `GOTCHAS.md`.

**Diga a razão, não só o quê.** Um comentário de código que explica *por que* uma decisão não-óbvia foi tomada vale mais que a decisão em si — principalmente quando a alternativa óbvia foi tentada e falhou. Isso é o que `GOTCHAS.md` documenta em nível de arquitetura; no código, uma linha de comentário basta.

**Pergunte quando a mudança é ambígua ou grande.** Antes de implementar item de roadmap não aprovado, ou de mudar contrato entre operadores, confirme o escopo. Não expanda silenciosamente.

**Aponte inconsistência sem ser convidado.** Se notar um bug ou contradição fora do que foi pedido, diga — mas não corrija sem perguntar, a menos que seja trivial e óbvio.

## Como escrever código

**YAGNI, sempre.** Não generalize para um caso hipotético. Se hoje só existe uma laje e uma parede usando o padrão, não construa abstração para dez tipos futuros.

**Token-econômico.** Prefira reaproveitar função existente a duplicar. Prefira editar 3 linhas a reescrever 80. Não gere boilerplate que o Python não exige.

**Python, estilo do repositório:**
- docstrings de módulo e classe em inglês, explicando o *porquê* de decisões não-óbvias — não o *o quê*, que o código já diz
- comentários no código também em inglês
- respostas de chat e mensagens de commit em português
- `print(f"[RefOps.<módulo>] ...")` para logs — prefixo por área (`bim`, `ladybug`, `honeybee`, `space`)
- funções puras nos `*_helpers.py`, sem estado, quando possível
- operadores individuais (`ops/**/op_*.py`) são finos: leem atributos, montam parâmetros, chamam helper
- sempre tratar falha de API externa com mensagem de console explicando causa provável, nunca deixar exceção crua estourar pro usuário

**Nomenclatura de atributo do Blender:** domínio explícito na descrição do campo (`"Storey Attr (face int)"`), nunca ambíguo sobre em qual domínio (ponto/aresta/face) o atributo deve ser pintado.

**Nunca decida silenciosamente uma convenção nova de nomenclatura CSS** sem atualizar este arquivo e regenerar `patterns.svg`/`hatches.css`/`theme-*.css` juntos — os três precisam ficar em sincronia.

## Arquivos de apoio

- `STATUS.md` — o que está testado, por operador
- `GOTCHAS.md` — comportamentos descobertos na marra (sintoma → causa), para não redescobrir
- `PSETS.md` — Psets lidos pelos operadores, com unidades
- `SIMULACOES.md` — que simulação climática rodar em cada fase do projeto, e como
- `MCP_SESSIONS.md` — hábitos de fluxo pra sessões via MCP do Blender (não sobre o addon)
