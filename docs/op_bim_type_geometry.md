# op_bim_type_geometry — design

Estado em 2026-09-27: **implementado** (`style_attr` + `_paint_styles`/`_paint_styles_from_mesh` em
`op_bim_type_geometry.py`). Só `py_compile` — falta teste no Blender, ver STATUS.md.

## Ideia

Multi-material dentro de **um** type (porta, janela, elemento com detalhes): partes diferentes do
corpo (moldura, vidro, painel) virando Representation Items diferentes, sem tocar na mecânica de
Material Constituent Set que já existia (`material_set` no Single, `material_attr` no Multiple).

## Workflow (Multiple mode)

1. No `parent_obj`, cria vertex groups com todos os nomes que vai usar — tanto nomes de
   Constituent Set quanto nomes de Style. Os dois tipos de nome moram na mesma lista de vertex
   groups; são índices diferentes na mesma tabela.
2. Pinta **Group Attr** (point int) — decide quais faces viram qual *type* separado. Em branco cai
   no fallback de ilha solta.
3. Pinta **Material Attr** (point int) — voto por maioria de área **do type inteiro**
   (`_dominant_value`) → nome do vertex group → `IfcMaterialConstituentSet` já existente com esse
   nome exato (`find_constituent_set`, nunca cria) → atribuído ao type inteiro.
4. Pinta **Style Attr** (point int) — voto **por face individual** (`_element_value`, sem
   ponderação por área do grupo) → nome do vertex group → material Blender com esse nome, **criado
   se não existir** → vira `material_index` daquela face. Um type pode ter várias faces com nomes
   diferentes, cada uma virando seu próprio Representation Item automaticamente — o
   `geometry.add_representation` do Bonsai já separa por slot de material sozinho, sem código novo
   pra isso.
5. Por fora do PyOps: os membros de cada Constituent Set apontam pras Styles que o Arthur quiser —
   isso é manual, sem relação de código com o nome usado no passo 4.

O nome do Constituent Set (passo 3) não precisa ser igual a nenhum nome de Style (passo 4) — são
buscas independentes na mesma lista de vertex groups, uma votando por type inteiro e a outra por
face.

## Decidido

- Terceiro atributo de vertex-group-table (`style_attr`), ao lado de `group_attr`/`material_attr` —
  mesmo truque do roadmap 1 (`bim.vgroup_string`), papel diferente: nomeia **slot de material
  Blender**, não Constituent Set nem IFC Style.
- Nome sem material Blender correspondente: **cria** o material (vazio) — ao contrário de
  `find_constituent_set`, que nunca cria nada. Decisão explícita do Arthur: "existindo ou não
  existindo representation item, nada quebra."
- Não verifica `IfcSurfaceStyle` nem nada do lado IFC — é só nome de slot de material Blender.
  Ligar isso a uma IFC Style ou aos membros do Constituent Set é manual, feito à parte no Bonsai.
- Vale nos dois modos (Single e Multiple). Em Multiple roda **dentro** de cada mesh já separado por
  `group_attr` — várias portas geradas podem repetir os mesmos nomes de Style.
- Mecânica de Constituent Set não mudou uma linha.

## Fechado em 2026-09-27 (eram perguntas em aberto)

1. Cheguei a supor que "Style" seria algo do lado IFC — um `IfcShapeAspect` ligando Constituent a
   um subconjunto de geometria, ou um `IfcSurfaceStyle` que precisaria já existir. Errado nas duas
   vezes: é só o nome do slot de material Blender.
2. Cheguei a supor que precisaria achar (nunca criar) um material já existente, mesma filosofia de
   `find_constituent_set`. Errado — aqui cria se não existir.
3. Primeira proposta usava domínio por FACE; Arthur pediu pra voltar pra PONTO, mesmo domínio de
   `group_attr`/`material_attr` — o voto por maioria roda pelos vértices de cada face
   (`_element_value`), igual aos outros dois atributos.
