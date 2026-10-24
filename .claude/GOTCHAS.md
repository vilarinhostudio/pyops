## Como usar

Sintoma primeiro, causa depois — é como você vai lembrar disso daqui a um mês: pelo que viu na tela, não pelo nome técnico da causa. Ver também [CLAUDE.md](../CLAUDE.md) para as regras que nasceram destes casos.

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

**Os painéis perdem a cor a cada Refresh, mesmo com o set de material certo.**
Causa: uma ocorrência recriada nasce sem slots de material, e a cor no viewport vem do slot. O set atribuído ao type não basta.
Solução: `op_bim_panel` grava um slot com `bim.set_blender_material(set)` — o material Blender já ligado ao estilo do material do set. Nada é criado: estilo sem material Blender vira aviso no console e painel sem cor.

**Não consigo deletar nem salvar um `IfcSurfaceStyle` (aparece no Styles do Bonsai, mas o delete falha e trava o save).**
Causa: estilo órfão — sem material Blender ligado (`tool.Ifc.get_object(style)` é `None`) e sem nenhuma referência no arquivo (`file.get_inverse(style)` vazio). Os botões do Bonsai trabalham pelo material Blender, então não o alcançam. Veio da versão antiga do painel (`sync_material_style`).
Solução: confirmar `get_inverse` vazio e remover direto com `ifcopenshell.api.run("style.remove_style", f, style=style)`; reabrir o painel de estilos para limpar a lista. Hipótese não confirmada: o traceback real do save não foi lido.

**Objetos `IfcMember/...` (ou qualquer `Ifc*/...`) que não deleta, console do Bonsai com erro, e o IFC não tem nenhum elemento daquela classe.**
Causa: `.blend` e `.ifc` dessincronizados. O objeto Blender guarda `BIMObjectProperties.ifc_definition_id` (STEP ID) e o `.blend` salva isso; se o `.ifc` foi regenerado, não foi salvo junto ou foi revertido, os IDs mudam. O objeto passa a apontar para uma entidade de outra classe (`IfcMaterialConstituent`, `IfcAxis2Placement3D`, `IfcCartesianTransformationOperator3D`) ou para um ID inexistente — e o delete do Bonsai age sobre a entidade apontada, não sobre o que o nome do objeto diz. Dois objetos podem até dividir o mesmo ID. Ocorrido em 2026-10-21 com `Curt_Wall_Profile*` (7 fantasmas, `IfcMember` = 0 no arquivo). Não confirmado se o operador de profile component ou o ciclo salvar/fechar/abrir causou; o log original não foi visto.
Solução: confirmar que é fantasma (`f.by_id(o.BIMObjectProperties.ifc_definition_id)` levanta ou devolve classe que não casa com o prefixo do nome) e remover **só pelo Blender**, `bpy.data.objects.remove(o, do_unlink=True)` + a malha órfã — nunca por `bim.delete_object`. Varrer o resto da cena com o mesmo teste. Prevenção: salvar `.blend` e `.ifc` juntos; ao reabrir, checar se os dois são do mesmo momento.

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

**Criar uma layer de atributo (`bm.verts.layers.bool.new(...)` etc.) depois de já ter construído geometria invalida toda referência Python a `BMFace`/`BMVert` seguradas antes — `ReferenceError: BMesh data ... has been removed` numa linha que só lê a lista.**
Causa: não documentado na API do Blender; parece que adicionar uma layer realoca o bloco de custom data de todos os elementos existentes, e as referências Python antigas ficam apontando pro que foi liberado.
Solução: criar TODAS as layers de atributo antes de construir qualquer vértice/face no bmesh, nunca depois.

## Honeybee / Radiance

**Procurar "face com furo" na malha de uma parede com vão não acha nada.**
Causa: polígono do Blender tem um único laço — não existe face com furo. O vão recortado pelo Bonsai aparece como faces (triângulos) *em volta* do buraco.
Solução: agrupar faces conectadas e coplanares em região (`planar_regions`); as bordas da região formam laços, o maior é o contorno, os outros são vãos. Cada vão aparece nas duas faces da parede — parear (`pair_voids`) para não virar vidro duplo.

**Porta até o piso não é detectada como vão.**
Causa: o recorte encosta na borda e vira entalhe no contorno externo, não laço interno.
Solução: objeto de Openings sem vão casado vira abertura pela sua maior região plana.

**Luminária num cubo ilumina a parede, ou a luz artificial "não aparece" no Point-in-Time.**
Causa: (1) o emissor é a maior face; num cubo as 6 empatam. (2) Ao meio-dia o sol pela janela dá dezenas de milhares de lux e a escala de cor, normalizada pelo máximo, esconde as centenas de lux da lâmpada.
Solução: luminária como placa achatada; rodar à noite (ex.: 23h) para isolar a luz artificial.

**Campo *Pset.Prop* de sobrescrita óptica é ignorado, mas o valor chega mesmo assim.**
Causa: `pset_value` separa no **primeiro** ponto e só lê Psets do elemento, nunca do material. O valor chega pela busca por nome no material, que roda depois. Não corrigido.
Solução por ora: deixar os campos vazios e pôr as propriedades no material.

**`PyOps_Glazing` (portas/janelas da collection Openings) no material não faz nada — só `VisibleTransmittance` padrão (vidro) aparece no resultado, mesmo numa porta de madeira opaca.**
Causa: `glazing_values`/`glazing_modifier` leem `PyOps_Glazing` só via `bim.pset_value(element, ...)` → `ifcopenshell.util.element.get_psets(element)`, que **não** inclui propriedade de material nenhuma (confirmado empiricamente — diferente de `optical_properties`, usado pela Construction, que tem fallback pro material). Toda porta de Openings precisa do Pset no **elemento** (`IfcDoor`/`IfcWindow`), nunca no material.
Solução: sem `VisibleTransmittance` explícita, a porta cai no padrão de vidro — se for opaca (madeira, muxarabi), setar `VisibleTransmittance=0.0` + `Reflectance` no elemento, ou ela "brilha" como vidro genérico no Point-in-Time.

**Material errado recebe o Pset_MaterialOptical numa cena com múltiplos IfcProject linkados (Revit merge).**
Causa: mais de um `IfcMaterial` pode ter o mesmo `Name` (um por projeto linkado — `CYH_edificios`, `CYH_edificios_vizinhos`, etc.), então resolver o material só pelo nome (`{m.Name: m for m in ifc.by_type("IfcMaterial")}`) pega uma entidade arbitrária, não necessariamente a que os objetos da sala realmente referenciam.
Solução: resolver o material a partir do **objeto/elemento real** da collection (`ifcopenshell.util.element.get_material`), nunca por busca de nome solta no arquivo inteiro.

**Material com cor definida, mas o log diz que nenhuma fonte óptica foi encontrada.**
Causa: o estilo padrão do Bonsai é `IfcSurfaceStyleShading`; a leitura antiga só aceitava `IfcSurfaceStyleRendering`.
Solução: aceitar Shading (Rendering é subtipo) e usar `SurfaceColour` quando não houver `DiffuseColour`.

**`Warning: Roughness values above .2 is uncommon` repetido no log.**
Causa: `Roughness` do plastic no Radiance vai de 0 a 0.2.
Solução: 0 para materiais foscos.

## Honeybee / EnergyPlus

**IDF de `model_to_idf` não roda: EnergyPlus reclama de `Version` ausente ou sem período de simulação.** (evitado desde o início — lido no CLI do honeybee-energy, não visto como erro)
Causa: `model_to_idf` escreve só o modelo. O CLI (`cli/translate.py`) junta `energyplus_idf_version()` + `SimulationParameter.to_idf()` + modelo, nessa ordem.
Solução: `simulate_energy` em `honeybee_helpers.py` monta o IDF do mesmo jeito.

**Ideal Air sumiu do IDF / simulação sem carga nenhuma.** (evitado desde o início)
Causa: o honeybee escreve o Ideal Air como `HVACTemplate:Zone:IdealLoadsAirSystem`, que só vira objeto de verdade depois do ExpandObjects.
Solução: nunca passar `expand_objects=False` para `run_idf`.

**EnergyPlus aborta no dimensionamento por falta de design days.** (evitado desde o início)
Causa: `SimulationControl` do honeybee liga o zone sizing por padrão, e ele precisa de `SizingPeriod:DesignDay`.
Solução: DDY ao lado do EPW (`add_from_ddy_996_004`); sem DDY, `EPW.approximate_design_day`.

**`Temperature out of range [-100. to 200.] (PsyPsatFnTemp)` com ~6000 °C no `.err`.**
Causa: `CalcPurchAirLoads` do Ideal Air durante o design day de entalpia (São Paulo, `CLG .4% CONDNS ENTH=>MDB`). Só no sizing, não no ano; a simulação termina normalmente.
Solução: nenhuma ainda. Desconfiar de capacidade *autodimensionada*; as cargas anuais não são afetadas por esse aviso.

**Parede virou teto (ou piso virou cobertura) no IDF.** (evitado desde o início)
Causa: a `Face` do honeybee decide Wall/Floor/RoofCeiling pela normal **no momento em que é criada**. `Room(tolerance=...)` vira a geometria para fora depois, mas não reclassifica. Uma malha com normais para dentro sai com os tipos trocados.
Solução: virar primeiro (`Polyface3D.from_faces` → `.faces` já saem para fora, na mesma ordem) e criar as `Face` a partir disso (`energy_room`).

**`AssertionError: Adiabatic cannot be assigned to a Face with apertures or doors`.** (evitado desde o início)
Causa: o honeybee só aceita abertura em face `Outdoors` (ou `Surface`), e confere nos dois sentidos — condição de contorno depois das aberturas também quebra.
Solução: contorno primeiro (`apply_envelope`), aberturas depois, e abertura em face não-Outdoors é descartada com aviso.

**Todo elemento sai `sem IfcMaterialLayerSet utilizável` — mesmo com `get_material` mostrando o `IfcMaterialLayerSetUsage` no console.**
Causa: `import ifcopenshell.util.unit` **dentro** de `material_layers`. Um `import a.b.c` numa função liga o nome `a` como variável **local da função inteira**, inclusive nas linhas antes do import — então `ifcopenshell.util.element.get_material(...)` no topo dava `UnboundLocalError`, e o `except Exception: return []` engolia como "sem camadas". O teste com `bim_helpers` simulado não passava por essa função.
Solução: import com alias (`import ifcopenshell.util.unit as ifc_unit`), que liga só o alias. Vale para qualquer helper: import pontilhado local de um módulo cujo nome raiz já é usado na função é bug garantido.

**`face refops_room_fN (Wall) sem elemento de Construction casado` numa parede que está lá, na distância certa.**
Causa: porta até o piso **centrada** na parede. O vão vira entalhe no contorno externo da região (não laço interno), e o centro da face do Room — meio da largura, meia altura — cai dentro do entalhe. O teste antigo usava só o centro.
Solução: `match_room_faces` testa o centro **e** os pontos a meio caminho entre o centro e cada vértice da face; basta um cair dentro. Porta larga o bastante para engolir todos (portão) ainda falha — com aviso.

**`Input number material specific heat must be between 100.0 and inf. Got 0.0` e a parede inteira sai genérica.**
Causa: o Pset térmico do material existe com o campo **zerado** (típico de Pset adicionado pelo template do Bonsai e não preenchido). O padrão só entra quando a propriedade está *ausente*; zero é lido como valor e o honeybee recusa. O log diz `sem IfcMaterialLayerSet utilizável`, o que engana — o layer set está lá. Não corrigido.
Solução por ora: preencher o valor, ou apagar a propriedade para cair no padrão.

**Cargas e conforto absurdos (resfriamento e aquecimento ~3×) sem aviso nenhum.**
Causa: unidade errada no Pset do material — `MassDensity` 2,4 (t/m³) em vez de 2400 kg/m³. A laje vira uma folha sem massa térmica. Nada reclama: 2,4 é um número válido.
Solução: tudo em SI (kg/m³, J/kgK, W/mK — ver PSETS). Para conferir o que realmente entrou, abrir `%TEMP%\refops_energyplus\active\in.idf` e ler os objetos `Material` e `BuildingSurface:Detailed` (construção e condição de contorno por face).

## Instalação de dependências (pip / Windows)

**Point-in-Time falha com `No such option: --create-grids`; o Daylight Factor funciona. Depois vem `Invalid result folder`.**
Causa: a receita é montada pelo `lbt-recipes` do Blender (`C:\refops_libs`), mas executada pelo `honeybee-radiance` CLI do Python do ladybug_tools. `lbt-recipes 0.28.6` passa uma opção que o `honeybee-radiance 1.66.54` não tem; só algumas receitas usam essa opção. `run_succeeded` não pegou a falha (não corrigido).
Solução: fixar `lbt-recipes==0.26.6` (a versão do ladybug_tools) em `C:\refops_libs` com `--no-deps`. Sempre casar as versões entre os dois Pythons.

**`pip install` relata "Successfully installed" mas o pacote não funciona / `ModuleNotFoundError` num submódulo dele.**
Causa: com `--target`, o pip instala numa pasta temporária e depois move os arquivos. Se a etapa de mover falhar (arquivo travado por outro processo, ex. Blender aberto, ou OneDrive sincronizando), parte dos arquivos nunca chega ao destino — mas o pip já tinha reportado sucesso da instalação em si.
Solução: fechar o Blender antes de instalar; evitar instalar dentro de pasta sincronizada (OneDrive); em caso de instalação pela metade, apagar a pasta do pacote e reinstalar (sem `--upgrade`, que tenta apagar o que já existe e pode falhar pelo mesmo motivo).

**Erro de gravação de arquivo `.ifc` dentro de pasta OneDrive: "could not replace the existing file".**
Causa: o OneDrive mantém o arquivo aberto durante sincronização; o `ifcopenshell` escreve num temporário e tenta substituir o original.
Solução: salvar fora de pasta sincronizada, ou pausar a sincronização durante a sessão de trabalho.

## CSS / SVG (drawing-bonsai/assets)

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

**A hachura de um FILL-AREA (`IfcAnnotation`, `PredefinedType-FILLAREA`) não aparece; sai só o fundo liso.**
Causa: toda regra de hachura exige `material-` na classe, e o Bonsai só põe esse prefixo em corte/projeção. Numa anotação o nome do estilo vai cru (`diagonal1-fgwarning1-...`), então nenhum seletor casa e sobra `.PredefinedType-FILLAREA { fill: bg1 }`.
Solução: o nome do estilo/material do FILL-AREA começa com `material-` (`material-diagonal1-fgwarning1-...`). Zero código, testado. O stroke/pen com prefixo `c` segue sem efeito em anotação — os seletores são escopados com `.cut`.
