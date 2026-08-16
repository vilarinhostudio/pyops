## Como usar

Sintoma primeiro, causa depois — é como você vai lembrar disso daqui a um mês: pelo que viu na tela, não pelo nome técnico da causa. Ver também [CLAUDE.md](CLAUDE.md) para as regras que nasceram destes casos.

## Bonsai / representação

**A parede perde material, cutter e modificador booleano ao receber uma abertura.**
Causa: `switch_representation` reconstrói a malha do zero sempre que a representação muda. Adicionar um vazio muda a representação. Qualquer coisa escrita na malha antes se perde.
Solução: capturar slots de material + booleanos antes da operação, restaurar depois (`capture_finish` / `restore_finish` em `bim_helpers.py`).

**`bpy.ops.bim.add_opening()` responde "Select openings and a target element" e não faz nada.**
Causa: o operador espera uma **malha comum**, não um `IfcOpeningElement` já criado — ele mesmo cria a entidade de vazio a partir do objeto recebido. Entregar o vazio pronto é entregar algo que já deixou de ser candidato.
Solução: passar a porta/janela (ou uma caixa burra) + parede ativa; deixar o Bonsai converter.

**A abertura fica no lugar certo mas não corta a parede / o `IfcRelVoidsElement` existe mas nada muda visualmente.**
Causa: escrever a relação no arquivo registra o dado, mas não regenera a malha do Blender. É preciso `switch_representation(apply_openings=True)`.

**Rodar `add_opening` uma segunda vez à mão "conserta" a posição da porta.**
Causa: o argumento `preserve_placement=False` permite ao Bonsai reposicionar o preenchimento conforme o vazio. Sem ele (ou com `True`), a colocação anterior é mantida. A segunda execução manual é a primeira vez que o argumento certo foi usado.

## Bonsai / API e operadores

**`bpy.ops.bim.*` "falha" mas não levanta exceção nenhuma.**
Causa: um operador que reprova no `poll()` devolve `{'CANCELLED'}` silenciosamente. Tratar a chamada como sucesso esconde a falha.
Solução: sempre checar `"FINISHED" in result`.

**`relating_type_id (IfcDoor): enum "1215" not found in ('254', '260')`.**
Causa: o enum do painel do Bonsai é filtrado pela classe que o painel está mostrando naquele momento. Definir a classe certa antes de atribuir o tipo nem sempre refiltra a lista em tempo de dirigir pela UI via código.
Solução: construir a ocorrência pela API do IFC (`root.create_entity` + `type.assign_type`) em vez de dirigir o painel — determinístico, sem depender de estado de UI.

**Nome de operador ou de módulo da API muda entre versões sem aviso** (ex.: `void` deixou de existir; `add_constr_type_instance` não existe em todas as versões).
Solução: descobrir por palavra-chave em tempo de execução (`find_operator("add", "type", "instance")`) com fallback, em vez de hard-code.

## Geometria e placement

**Um elemento simétrico parece girado 180° e "corrigir" girando mais 180° não resolve — ou espelha de novo.**
Causa: a matriz foi montada trocando duas colunas (ex. `(x, z, y)` em vez de `(x, y, z)`), o que inverte o determinante para negativo. Isso é **espelhamento**, não rotação, e é visualmente idêntico a 180° num objeto simétrico.
Solução: checar `det(matriz[:3,:3]) > 0` antes de aplicar. Corrigir negando uma das colunas trocadas, não girando.

**Um objeto colocado num vértice no pé de uma parede sai inclinado ~30-45°.**
Causa: a normal foi calculada pela média das faces ligadas ao vértice, e esse vértice também toca a face do piso. A normal do piso puxa a média para cima.
Solução: filtrar por verticalidade (`abs(normal.z) < 0.5`) antes de somar, e ponderar por área.

**Plano de parede indetectável quando há duas portas na mesma parede, ou portas em paredes diferentes.**
Causa: tentar achar o plano por soma de normais de face falha quando o ponto de referência (porta, vão) não está centrado ou está numa junção.
Solução: usar as **arestas horizontais** que passam pelo ponto — elas correm ao longo da parede, então a normal é a perpendicular delas. Funciona independente de quantas portas existem na parede.

## Instalação de dependências (pip / Windows)

**`pip install` relata "Successfully installed" mas o pacote não funciona / `ModuleNotFoundError` num submódulo dele.**
Causa: com `--target`, o pip instala numa pasta temporária e depois move os arquivos. Se a etapa de mover falhar (arquivo travado por outro processo, ex. Blender aberto, ou OneDrive sincronizando), parte dos arquivos nunca chega ao destino — mas o pip já tinha reportado sucesso da instalação em si.
Solução: fechar o Blender antes de instalar; evitar instalar dentro de pasta sincronizada (OneDrive); em caso de instalação pela metade, apagar a pasta do pacote e reinstalar (sem `--upgrade`, que tenta apagar o que já existe e pode falhar pelo mesmo motivo).

**Erro de gravação de arquivo `.ifc` dentro de pasta OneDrive: "could not replace the existing file".**
Causa: o OneDrive mantém o arquivo aberto durante sincronização; o `ifcopenshell` escreve num temporário e tenta substituir o original.
Solução: salvar fora de pasta sincronizada, ou pausar a sincronização durante a sessão de trabalho.

## CSS / SVG (assets-bonsai)

**Uma cor aplicada só à projeção (`pfillwarning3`) também aparece no corte, ou vice-versa.**
Causa: `pfillwarning3` **contém** `fillwarning3` como substring — a regra sem prefixo casa a classe prefixada também.
Solução: toda regra sem escopo leva `:not([class*="p..."]):not([class*="c..."])` contra as versões prefixadas.

**Cor ou espessura aplicada num elemento em projeção não aparece em nada.**
Causa: em projeção, o Bonsai desenha por **filhos** (`path.outline`, `path.crease`), que têm regra CSS própria e mais específica que o grupo pai. Uma regra no grupo perde.
Solução: os seletores escopados precisam nomear os filhos (`.projection[class*="..."] path`).

**O fundo de uma hachura aparece preto/errado mesmo sem `bg` nenhum declarado.**
Causa: a variável `--color-bg` deixou de existir quando a paleta virou níveis (`bg1`, `bg2`, `bg3`); alguma regra ainda referenciava `var(--color-bg)` sem nível — variável inexistente sem fallback não pinta nada, e o que aparece é o que está atrás.
Solução: toda referência de cor tem que usar o nome com nível.

**Rodar o gerador de hachuras duas vezes faz o arquivo explodir de tamanho** (32 hachuras → 1952, ~40 MB).
Causa: `generate_hatches.py` lia e escrevia o mesmo `patterns.svg` — a segunda execução reprocessava a própria saída como se fosse a fonte original.
Solução: entrada (`patterns-source.svg`) e saída (`patterns.svg`) são arquivos **separados**, nunca o mesmo caminho.

**Comentário de bloco em CSS gerado corta no meio e o resto vira regra solta.**
Causa: `*/` fechando antes do fim pretendido do comentário, dentro do script gerador.
Solução: ao editar comentários multi-linha gerados por script, contar aberturas e fechamentos antes de publicar.
