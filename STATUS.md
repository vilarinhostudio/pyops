## Como preencher

Marque o estado real, não o esperado. `?` para "implementado mas nunca rodado no Blender".

Estados: `ok` testado e funcionando · `parcial` funciona com ressalva anotada · `bug` com problema conhecido, ver [GOTCHAS](GOTCHAS.md) · `?` não testado · `-` não se aplica

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

| Operador                      | Estado           | Nota                                                       |
| ----------------------------- | ---------------- | ---------------------------------------------------------- |
| `op_bim_wall.py`              | ==requer teste== | ok em 18-08-2026; Renovation Status removido depois disso  |
| `op_bim_slab.py`              | ==requer teste== | ok em 18-08-2026; Renovation Status removido depois disso  |
| `op_bim_face_component.py`    | ==requer teste== | sem cutter; material com um atributo só; storey por atributo |
| `op_bim_panel.py`             | ==requer teste== | sem cutter; material com um atributo só                    |
| `op_bim_component.py`         | ==requer teste== | reescrita pendente; ainda tem cutter, único que sobrou      |
| `op_bim_distribute.py`        | ==requer teste== | reescrito: ver nota abaixo                                 |
| `op_bim_column.py`            | ==requer teste== | via `bim_profiles.py`, sem cutter                          |
| `op_bim_beam.py`              | ==requer teste== | via `bim_profiles.py`, sem cutter                          |
| `op_bim_profile_component.py` | ==requer teste== | não carregava (`with_ifc_class` inexistente); sem cutter    |
| `op_bim_space.py`             | ==requer teste== | Renovation Status removido                                 |

`op_bim_distribute.py`, 18-08-2026 — três mudanças, nenhuma rodada no Blender:

- saíram `cut_material`, `cutter_object` e o par `_apply_cutters`/`_finish`; o
  operador não toca mais em material, o type do Bonsai manda
- `Distribute As` virou um enum só de três opções (`IFC Door / Window`,
  `IFC Component`, `Blender Objects`); `enum_kind` deixou de existir
- o caminho `Blender Objects` nunca tinha rodado — exigia projeto IFC aberto e
  o laço só chamava o construtor IFC. **É o primeiro a testar.**

## ops/ladybug

| Operador                 | Estado           | Nota                                           |
| ------------------------ | ---------------- | ---------------------------------------------- |
| `op_ladybug_graphs.py`   | ok               | requer instalação pip completa (lbt-ladybug)   |
| `op_ladybug_analysis.py` | ==requer teste== | lento sem BVH, ver roadmap item de performance |

## ops/honeybee

| Operador                  | Estado           | Nota                                           |
| ------------------------- | ---------------- | ---------------------------------------------- |
| `op_honeybee_radiance.py` | ==requer teste== | requer Radiance instalado além dos pacotes pip |

## ops/arch

| Operador             | Estado           | Nota                                                  |
| -------------------- | ---------------- | ----------------------------------------------------- |
| `op_space_syntax.py` | ==requer teste== | decomposição volumétrica e conexões por IFC validadas |

## ops/basic

| Operador        | Estado | Nota                                 |
| --------------- | ------ | ------------------------------------ |
| `op_extrude.py` | ok     | exemplo/teste do motor, não gera IFC |
| `op_bevel.py`   | ok     | exemplo/teste do motor, não gera IFC |

## assets-bonsai (CSS/SVG)

| Item                                      | Estado           | Nota                                      |
| ----------------------------------------- | ---------------- | ----------------------------------------- |
| Hachuras + paleta com níveis              | ok               | testado via inspetor do navegador         |
| Prefixo `p`/`c` (projeção/corte)          | ok               | inclusive filhos `path.outline`/`.crease` |
| 6 temas gerados                           | ok               |                                           |
| Peso em produção web (múltiplas pranchas) | ==requer teste== | ver nota de peso em CLAUDE.md             |

## Dependências externas

| Pacote                                   | Estado | Nota                                                            |
| ---------------------------------------- | ------ | --------------------------------------------------------------- |
| lbt-ladybug (pip)                        | ok     |                                                                 |
| lbt-recipes + pollination-handlers (pip) | ok     | histórico de instalação incompleta, reinstalar com dependências |
| Radiance (binário)                       | ok     | necessário além dos pacotes Python                              |
