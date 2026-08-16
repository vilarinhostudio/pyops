## Como preencher

Marque o estado real, não o esperado. `?` para "implementado mas nunca rodado no Blender".

Estados: `ok` testado e funcionando · `parcial` funciona com ressalva anotada · `bug` com problema conhecido, ver [GOTCHAS](GOTCHAS.md) · `?` não testado · `-` não se aplica

## Motor

| Item                                | Estado | Nota                                                     |
| ----------------------------------- | ------ | -------------------------------------------------------- |
| Loader (descoberta de operações)    | ok     |                                                          |
| Duplicação não-destrutiva / refresh | ok     |                                                          |
| Ordem por profundidade de cadeia    | ok     |                                                          |
| Ordem manual (desempate de irmãos)  | ok     |                                                          |
| Refresh on Frame Change             | ok     |                                                          |
| Limpeza por pset de família         | ok     |                                                          |
| Profiling (`profiling.py`)          | ok     | instrumentado, sem `step()` marcado nos operadores ainda |

## ops/bonsai

| Operador                      | Estado | Nota                                         |
| ----------------------------- | ------ | -------------------------------------------- |
| `op_bim_wall.py`              | ok     | trim CORNER/T corrigido e validado           |
| `op_bim_slab.py`              | ok     | split por atributo de aresta testado         |
| `op_bim_face_component.py`    | ok     |                                              |
| `op_bim_panel.py`             | ok     |                                              |
| `op_bim_component.py`         | ok     |                                              |
| `op_bim_distribute.py`        | ok     | door/window com abertura automática validado |
| `op_bim_column.py`            | ok     |                                              |
| `op_bim_beam.py`              | ok     |                                              |
| `op_bim_profile_component.py` | ok     |                                              |
| `op_bim_space.py`             | ok     |                                              |

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
