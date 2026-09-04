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

| Operador                      | Estado  | Nota                                                                                     |
| ----------------------------- | ------- | ---------------------------------------------------------------------------------------- |
| `op_bim_wall.py`              | parcial | direção pela normal da face confirmada; falta testar align/tipo/pavimento como chave de agrupamento — face não pintada agora vira parede própria |
| `op_bim_slab.py`              | ok      |                                                                                          |
| `op_bim_face_component.py`    | parcial | reteste pendente: herdou de `create_type` a nomeação `<Classe>/<Nome>` do objeto do type |
| `op_bim_panel.py`             | parcial | reteste pendente: mesma nomeação de type do `face_component`                             |
| `op_bim_type_geometry.py`     | ok      |                                                                                          |
| `op_bim_distribute.py`        | ok      | `direction` removido; flip = sentido da normal da face; cardinal independente do flip e do alinhamento da parede (`preserve_placement=True`, ver GOTCHAS) |
| `op_bim_column.py`            | ok      |                                                                                          |
| `op_bim_beam.py`              | ok      |                                                                                          |
| `op_bim_profile_component.py` | ok      |                                                                                          |
| `op_bim_space.py`             | ?       |                                                                                          |


## ops/ladybug

| Operador                 | Estado  | Nota                                                                                   |
| ------------------------ | ------- | -------------------------------------------------------------------------------------- |
| `op_ladybug_graphs.py`   | parcial | Tudo funciona, mas preciso que o gráfico *wind* esteja junto do conjunto *sky e dome*. |
| `op_ladybug_analysis.py` | ok      |                                                                                        |

## ops/honeybee

| Operador                  | Estado | Nota                                                                                                                                                                                  |
| ------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `op_honeybee_radiance.py` | ?      | Precisa de BVH pois está lento. Fora isso, vou repensar bastante ele. Herdou a correção do `write_vertex_group` — a saída em Vertex Group também nunca funcionou na primeira execução |
| `op_honeybee_radiance.py` | -      | Falta pensar                                                                                                                                                                          |

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
