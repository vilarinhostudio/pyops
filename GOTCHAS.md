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

**A matriz que você deu para a porta é ignorada: ela nasce com a rotação da parede, o flip não muda nada e o cardinal inverte conforme o alinhamento da parede.**
Causa: com `preserve_placement=False` (o padrão), o `FilledOpeningGenerator` do Bonsai faz `filling_obj.matrix_world = voided_obj.matrix_world.copy()` — a rotação do preenchimento é a do **hospedeiro**, inteira, mais meia volta em Z quando o ponto está mais perto do eixo *side* do layer set do que do *base*. Como `base = OffsetFromReferenceLine` e `side = base + espessura`, quem decide essa meia volta é o **alinhamento da parede**: INTERIOR cai no side (gira), CENTER e EXTERIOR caem no base (não gira). O `translation.xy` vira a projeção do ponto no eixo escolhido, então a posição ao longo da parede sobrevive — só a orientação é descartada. Uma caixa delimitadora medida antes da chamada descreve uma orientação que deixou de existir, e é por isso que o cardinal erra em metade das combinações.
Solução: `preserve_placement=True` e assentar a porta você mesmo na face por onde ela abre. Convenção do Bonsai para preenchimento: origem **sobre** uma face do hospedeiro, **+Y local para dentro** da parede.

**Rodar `add_opening` uma segunda vez à mão "conserta" a posição da porta.**
Causa: a mesma de cima — a segunda execução reposiciona porque o gerador sempre reposiciona. Não é o argumento que muda entre a primeira e a segunda vez; é que a primeira colocação nunca chegou a valer.

## Bonsai / API e operadores

**`bpy.ops.bim.*` "falha" mas não levanta exceção nenhuma.**
Causa: um operador que reprova no `poll()` devolve `{'CANCELLED'}` silenciosamente. Tratar a chamada como sucesso esconde a falha.
Solução: sempre checar `"FINISHED" in result`.

**`relating_type_id (IfcDoor): enum "1215" not found in ('254', '260')`.**
Causa: o enum do painel do Bonsai é filtrado pela classe que o painel está mostrando naquele momento. Definir a classe certa antes de atribuir o tipo nem sempre refiltra a lista em tempo de dirigir pela UI via código.
Solução: construir a ocorrência pela API do IFC (`root.create_entity` + `type.assign_type`) em vez de dirigir o painel — determinístico, sem depender de estado de UI.

**`ModuleNotFoundError: No module named 'bonsai'` com o Bonsai instalado e funcionando.**
Causa: o nome importável do pacote não é sempre `bonsai`. O build de PR/alpha se instala como `bonsaiPR` (wheel em `extensions/.local/lib/pythonX.Y/site-packages/bonsaiPR`), e como extensão ele também responde por `bl_ext.<repo>.<id>` — que, dependendo do build, aponta para a pasta da extensão, onde pode não haver `tool/` nenhum. Os operadores (`bpy.ops.bim.*`) continuam iguais; só o import muda.
Solução: `bim_helpers.ensure()` descobre o nome — varre `sys.modules` por raiz que comece com "bonsai" (top-level ou `bl_ext.*`), completa com as grafias conhecidas, e decide importando `<candidato>.tool` de verdade. `bim_helpers.status()["PACKAGE"]` diz qual venceu.

**`add_representation` levanta `AssertionError` sem mensagem nenhuma — o print sai `erro: ` e acabou.**
Causa: não é o `add_representation` que falha, é um **post-listener** do Bonsai (`model.product.generate_box`, registrado em `geometry.add_representation`). Ele faz `assert product` sobre `tool.Ifc.get_entity(settings["blender_object"])` — assert nu, mensagem vazia. Passar um objeto descartável, não ligado a nenhuma entidade IFC (o carrier que segura a malha de um Type), reprova o assert e derruba a chamada inteira **depois** da representação já ter sido criada: ela fica órfã no arquivo e o `run()` devolve exceção em vez do resultado.
Solução: `should_run_listeners=False` quando o objeto não está ligado a uma entidade (`mesh_representation` decide por `tool.Ifc.get_entity`). Para objeto ligado os listeners continuam ligados — é o que gera o Box do produto.

**Nome de operador ou de módulo da API muda entre versões sem aviso** (ex.: `void` deixou de existir; `add_constr_type_instance` não existe em todas as versões).
Solução: descobrir por palavra-chave em tempo de execução (`find_operator("add", "type", "instance")`) com fallback, em vez de hard-code.

**`Type 'X' sem objeto Blender — nada atualizado.` num type que aparece normalmente no Type Manager.**
Causa: um type lido de volta de um `.ifc` não tem objeto Blender. O Bonsai lista o Type Manager direto do arquivo e só constrói o objeto quando algo pede ele na cena, então `tool.Ifc.get_object(type)` devolve `None` — e sem objeto não há para onde escrever representação.
Solução: **adotar**, nunca recriar. `link_object` cria o objeto e faz `tool.Ifc.link` na entidade existente. Recriar o type para ganhar um objeto destruiria o `IfcRepresentationMap` que toda ocorrência já colocada referencia.

**O operador cria um type novo a cada refresh em vez de atualizar o que já existe** (`Cube`, `Cube.001`, `Cube.002`...).
Causa: o `name_callback` do Bonsai escreve o nome do objeto Blender **de volta** na entidade IFC. Um objeto criado com o nome nu (`Cube`) colide com o objeto de referência, o Blender resolve para `Cube.001`, e o callback renomeia o *type* para `Cube.001`. O refresh seguinte procura por `Cube`, não acha, e cria outro.
Solução: nomear o objeto na convenção do próprio Bonsai, `"<Classe>/<Nome>"` — sem colisão e o callback lê de volta exatamente o nome pretendido (`link_object` em `bim_helpers.py`, e o rename final em `op_bim_type_geometry`).

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

**As ocorrências já inseridas saltam de lugar quando o type é atualizado.**
Causa: a geometria do type estava sendo recentrada num ponto calculado da própria malha (centroide dos vértices). Como o type não tem placement — sua geometria mora na origem do `IfcRepresentationMap` —, qualquer edição que mova esse ponto desloca a geometria de **toda** ocorrência já colocada. De quebra, centroide de vértices é ponderado por densidade de malha, não por volume: subdividir um lado já basta para movê-lo.
Solução: não recentrar. A geometria do type vai no espaço local do objeto de referência, então o pivô é a única coisa que a operação nunca toca. O preço é que um grupo modelado longe da origem nasce deslocado dela — modele perto da origem em vez de mover o pivô.

**`Error: Evaluated geometry from modifier does not contain a mesh` ao assar um Geometry Nodes.**
Causa: a saída da árvore são **instâncias**, não malha. `modifier_apply` não realiza instância nenhuma, então não há o que assar — e o container do PyOps fica sem malha, o que derruba qualquer operação que leia dele.
Solução: um nó *Realize Instances* no fim da árvore. Para separar o que era cada instância depois disso, pintar um atributo inteiro **de ponto** e agrupar por ele (é o que `op_bim_type_geometry` faz no modo Multiple) — instância realizada perde a identidade, atributo não. Ponto e não face porque o mesmo atributo agrupa os símbolos 2D, que são wireframe e não têm face para pintar.

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
