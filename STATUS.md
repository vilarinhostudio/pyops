## Como preencher

Marque o estado real, não o esperado. 

Estados:

- `ok` testado e funcionando;
- `parcial` funciona com ressalva anotada;
- `bug` com problema conhecido, ver [GOTCHAS](GOTCHAS.md);
- `?` implementado mas nunca rodado no Blender;
- `-` não se aplica;

## Motor

| Item                                | Estado | Nota                  |
| ----------------------------------- | ------ | --------------------- |
| Loader (descoberta de operações)    | ok     | anteriormente testado |
| Duplicação não-destrutiva / refresh | ok     | anteriormente testado |
| Ordem por profundidade de cadeia    | ok     | anteriormente testado |
| Ordem manual (desempate de irmãos)  | ok     | anteriormente testado |
| Refresh on Frame Change             | ok     | anteriormente testado |
| Limpeza por pset de família         | ok     | anteriormente testado |
| Profiling (`profiling.py`)          | ok     | anteriormente testado |

## ops/bonsai

| Operador                      | Estado | Nota                                                   |
| ----------------------------- | ------ | ------------------------------------------------------ |
| `op_bim_wall.py`              | ?      | seção de Material removida, 20-08-2026 — ver nota      |
| `op_bim_slab.py`              | ?      | seção de Material removida, 20-08-2026 — ver nota      |
| `op_bim_face_component.py`    | ?      |                                                        |
| `op_bim_panel.py`             | ?      |                                                        |
| `op_bim_component.py`         | ?      | reescrita pendente; ainda tem cutter, único que sobrou |
| `op_bim_distribute.py`        | ?      |                                                        |
| `op_bim_column.py`            | ?      | seção de Material removida, 20-08-2026 — ver nota      |
| `op_bim_beam.py`              | ?      | seção de Material removida, 20-08-2026 — ver nota      |
| `op_bim_profile_component.py` | ?      | seção de Material removida, 20-08-2026 — ver nota      |
| `op_bim_space.py`             | ?      |                                                        |

`op_bim_distribute.py`, 18-08-2026 — três mudanças, nenhuma rodada no Blender:

- saíram `cut_material`, `cutter_object` e o par `_apply_cutters`/`_finish`; o
  operador não toca mais em material, o type do Bonsai manda
- `Distribute As` virou um enum só de três opções (`IFC Door / Window`,
  `IFC Component`, `Blender Objects`); `enum_kind` deixou de existir
- o caminho `Blender Objects` nunca tinha rodado — exigia projeto IFC aberto e
  o laço só chamava o construtor IFC. **É o primeiro a testar.**

`op_bim_wall.py` e `op_bim_slab.py`, 20-08-2026 — nenhuma rodada no Blender:

- saiu a seção de Material inteira (`enum_mat_name`, `material_attr`) dos dois.
  São consumidores puros de type agora: leem a espessura do
  `IfcMaterialLayerSet` e não escrevem nada de volta
- isso mata de raiz o `ReferenceError: BMFace has been removed` da laje — o
  `faces_by_type` guardava BMFaces que `dissolve_regions` já tinha destruído, e
  só era lido no modo `Rename`. Sem o modo, não há o que segurar
- **testar que a laje termina sem exceção**, que era o caso que quebrava, e que
  wall e slab continuam lendo a espessura do type
- edição de material fica só em quem cria type: `op_bim_panel.py` e
  `op_bim_face_component.py`

`bim_profiles.EdgeComponent` (base de `op_bim_column.py`, `op_bim_beam.py` e
`op_bim_profile_component.py`), 20-08-2026 — nenhuma rodada no Blender:

- mesma remoção, mesmo motivo: a base era consumidora pura de type e só sabia
  renomear o `IfcMaterialProfileSet`. Saíram `material_props("edge")`, a chave
  `material` de `_layers`, `name_votes` e `_edge_vgroup_name`
- `_build_edge` voltou a devolver `(element, type_index)` em vez da tupla de
  quatro; o `parent_obj` saiu da assinatura porque só servia ao rename
- **testar os três**, que a mudança é na classe base e atinge todos de uma vez

Helpers deletados de `bim_helpers.py` na mesma leva: `rename_type_material_set`,
`MATERIAL_NAME_MODES_READ`, `_SET_NAME_ATTR` e o ramo READ de `material_props`
(que perdeu o parâmetro `mints_type`). `vgroup_string` e
`vgroup_name_from_faces` continuam vivos, usados por panel e face component.

## ops/ladybug

| Operador                 | Estado  | Nota                                                                                   |
| ------------------------ | ------- | -------------------------------------------------------------------------------------- |
| `op_ladybug_graphs.py`   | parcial | Tudo funciona, mas preciso que o gráfico *wind* esteja junto do conjunto *sky e dome*. |
| `op_ladybug_analysis.py` | ?       | Três mudanças por testar: (1) obstrução via BVHTree (`lb.obstacle_bvh`/`is_blocked_bvh`) — conferir se os valores batem com o caminho Face3D; (2) Display Material agora limpa `mesh.materials` antes de aplicar, em vez de acumular slots; (3) saída em Vertex Group destravada — testar num Result Object **sem nenhum grupo**, que era o caso que falhava |

## ops/honeybee

| Operador                  | Estado | Nota                                                                 |
| ------------------------- | ------ | -------------------------------------------------------------------- |
| `op_honeybee_radiance.py` | ?      | Precisa de BVH pois está lento. Fora isso, vou repensar bastante ele. Herdou a correção do `write_vertex_group` — a saída em Vertex Group também nunca funcionou na primeira execução |

## ops/arch

| Operador             | Estado | Nota                                                                                                       |
| -------------------- | ------ | ---------------------------------------------------------------------------------------------------------- |
| `op_space_syntax.py` | bug    | Não detecta quando dois espaços são conectados por uma porta. Mas tirando isso, vou repensar bastante ele. |

## ops/basic

| Operador        | Estado | Nota                                 |
| --------------- | ------ | ------------------------------------ |
| `op_extrude.py` | ok     | exemplo/teste do motor, não gera IFC |
| `op_bevel.py`   | ok     | exemplo/teste do motor, não gera IFC |

## assets-bonsai (CSS/SVG)

| Item                                      | Estado | Nota                                      |
| ----------------------------------------- | ------ | ----------------------------------------- |
| Hachuras + paleta com níveis              | ok     | testado via inspetor do navegador         |
| Prefixo `p`/`c` (projeção/corte)          | ok     | inclusive filhos `path.outline`/`.crease` |
| 6 temas gerados                           | ok     |                                           |
| Peso em produção web (múltiplas pranchas) | ?      | ver nota de peso em CLAUDE.md             |

## Dependências externas

| Pacote                                   | Estado | Nota                                                            |
| ---------------------------------------- | ------ | --------------------------------------------------------------- |
| lbt-ladybug (pip)                        | ok     |                                                                 |
| lbt-recipes + pollination-handlers (pip) | ok     | histórico de instalação incompleta, reinstalar com dependências |
| Radiance (binário)                       | ok     | necessário além dos pacotes Python                              |
