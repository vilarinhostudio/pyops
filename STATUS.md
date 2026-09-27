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

| Operador                      | Estado  | Nota                                                         |
| ----------------------------- | ------- | ------------------------------------------------------------ |
| `op_bim_wall.py`              | ok      |                                                              |
| `op_bim_slab.py`              | ok      |                                                              |
| `op_bim_face_component.py`    | ok      |                                                              |
| `op_bim_panel.py`             | parcial | reteste pendente: mesma nomeação de type do `face_component` |
| `op_bim_type_geometry.py`     | ok      |                                                              |
| `op_bim_distribute.py`        | ok      |                                                              |
| `op_bim_column.py`            | ok      |                                                              |
| `op_bim_beam.py`              | ok      |                                                              |
| `op_bim_profile_component.py` | ok      |                                                              |
| `op_bim_space.py`             | ?       |                                                              |


## ops/ladybug

| Operador                 | Estado  | Nota                                                                                   |
| ------------------------ | ------- | -------------------------------------------------------------------------------------- |
| `op_ladybug_graphs.py`   | parcial | Tudo funciona, mas preciso que o gráfico *wind* esteja junto do conjunto *sky e dome*. |
| `op_ladybug_analysis.py` | ok      |                                                                                        |

## ops/honeybee

| Operador                    | Estado | Nota                                                                                                                                                                                                                                                                                                                                                                                            |
| --------------------------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `op_honeybee_radiance.py`   | ok     | 2026-09-16: Construction + Openings + luz por Pset testados no Blender com Daylight Factor e Point-in-Time (luz visível à noite). 2026-09-18, via MCP (sala 5×10×3 m, WAL200/FLR200/ROOF200/DT01+WT01): Annual Daylight e Annual Irradiance testados com Model=Construction — resultados plausíveis (DA médio 8%, máx. 89% perto da janela), custom props `hb_*_min/max/mean` conferidos. Vidro de Openings reconferido (`PyOps_Glazing` lido certo pra janela; porta com VT≤0.01 tratada como opaca). ==requer teste==: Model = Face Attributes, aviso de fonte óptica ausente, estilo `IfcSurfaceStyleShading`, luz por Pset (`light_sources`/`light_readings`) desde o refactor pro EnergyPlus. Bug conhecido: campos *Pset.Prop* de sobrescrita (ver GOTCHAS) |
| `op_honeybee_energyplus.py` | ok     | 2026-09-17, Bonsai real (sala 5×10×3 m, EPW São Paulo): Projected + Ideal Air + Annual + adaptativo + horas ocupadas; depois Light Collection + HVAC Collection. IDF e `.sql` conferidos: 6 faces com `refops_layers_*`, espessura mm→m, Outdoors/Ground, janela SimpleGlazSys e porta NoMass pelo Pset, porta até o piso centrada casando com a parede, luz 1,2 W/m² com agenda do program, limites 3500 W, eletricidade = equip + luz + carga/COP. Corrigidos no caminho: `bim.material_layers` sempre `[]` e critério de casamento de face (ver GOTCHAS). **Em aberto**: carga horária de resfriamento chegou a ~6 kW com limite de 3,5 kW (40 h ≥ limite) — limite do Ideal Air talvez não valha para a carga total com ar externo, não investigado. 2026-09-18, via MCP (mesma sala, ROOF200 novo): `IsExternal` False (Adiabatic — aberturas corretamente ignoradas), Monthly (84 atributos), Single Month, Fixed Band + All Hours (resultado mudou como esperado), erros controlados (Program inexistente, EPW vazio) — todos ok. Cut rodou sem erro mas com 0 aberturas (a Room Reference de teste não tinha furo literal — não valida detecção real de vão). Last Layer Outside rodou sem erro mas deu resultado idêntico ao First Layer (a parede de teste só tem uma camada — não valida diferença real). ==requer teste==: `IsExternal` herdado do type (ausente no elemento), Room Reference vindo de outro operador do motor. Design em [op_honeybee_energyplus.md](op_honeybee_energyplus.md) |

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

| Pacote                                   | Estado | Nota                                                                     |
| ---------------------------------------- | ------ | ------------------------------------------------------------------------ |
| lbt-ladybug (pip)                        | ok     |                                                                          |
| lbt-recipes + pollination-handlers (pip) | ok     | histórico de instalação incompleta, reinstalar com dependências          |
| Radiance (binário)                       | ok     | necessário além dos pacotes Python                                       |
| lbt-recipes 0.26.6 em `C:\refops_libs`   | ok     | fixado para casar com o honeybee-radiance do ladybug_tools (ver GOTCHAS) |
| EnergyPlus (binário)                     | ok     | 26.1.0 em `C:\EnergyPlusV26-1-0`, achado sozinho pelo honeybee-energy 1.123.10 de `C:\refops_libs`; `tools/energyplus_smoke.py` confirma |
