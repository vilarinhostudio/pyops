# Psets usados pelo PyOps

## Honeybee Radiance

### `PyOps_Glazing` (custom, nas janelas/portas da collection Openings)

| Propriedade            | Tipo        | Uso                                                        |
| ---------------------- | ----------- | ---------------------------------------------------------- |
| `VisibleTransmittance` | Real 0–1    | > 0.01 → glass; ≤ 0.01 → opaco (plastic). Sem ela: VT 0.6  |
| `Reflectance`          | Real 0–1    | só no opaco; ignorada em vidro. Sem ela: 0.5               |

Nome do Pset é configurável no campo **Glazing Pset**.

### `PyOps_Light` (custom, nas luminárias da Light Collection)

| Propriedade    | Tipo | Uso                                       |
| -------------- | ---- | ----------------------------------------- |
| `LuminousFlux` | Real | lm, preferencial                          |
| `Wattage`      | Real | W, fallback × 90 lm/W. Sem nenhum: ignorada |

Nome do Pset é configurável no campo **Light Pset**.

### Óptica dos elementos da Construction (existente, leitura via `optical_properties`)

Ordem: campos *Pset.Prop* do operador → qualquer Pset do elemento → propriedades do material → estilo de superfície → padrão Radiance.

| Chave         | Nomes aceitos                                                  |
| ------------- | -------------------------------------------------------------- |
| reflectance   | `VisibleReflectanceFront`, `SolarReflectanceFront`, `Reflectance` |
| transmittance | `VisibleTransmittance`, `SolarTransmittance`, `Transmittance`  |
| specularity   | `Specularity`, `SpecularReflectance`                           |
| roughness     | `Roughness`                                                    |

Nenhum Pset do Bonsai é obrigatório; `Pset_MaterialOptical` (padrão IFC, no material) já casa pelos nomes acima.

## Honeybee EnergyPlus

### `PyOps_Glazing` (mesmo Pset do Radiance, mais duas propriedades)

| Propriedade            | Tipo     | Uso                                                                  |
| ---------------------- | -------- | -------------------------------------------------------------------- |
| `VisibleTransmittance` | Real 0–1 | > 0.01 → janela `SimpleGlazSys`; ≤ 0.01 → porta opaca. Sem ela: 0.6  |
| `U_Factor`             | Real     | W/m²K com filmes de ar. Sem ela: 5.8 (vidro) / 2.5 (porta)           |
| `SHGC`                 | Real 0–1 | só em vidro. Sem ela: 0.8                                            |

### `PyOps_Light` — mesmo Pset; aqui `Wattage` é o preferencial e `LuminousFlux` vira W a 90 lm/W.

### `PyOps_HVAC` (custom, nos aparelhos da HVAC Collection)

| Propriedade       | Tipo | Uso                                                             |
| ----------------- | ---- | --------------------------------------------------------------- |
| `CoolingCapacity` | Real | W; somado como limite de resfriamento do Ideal Air              |
| `HeatingCapacity` | Real | W; somado como limite de aquecimento                            |
| `COP_Cooling`     | Real | média ponderada Σ Cap / Σ (Cap/COP); sem ele o aparelho fica fora do COP |
| `COP_Heating`     | Real | idem                                                            |

Nome do Pset é configurável no campo **HVAC Pset**.

### Elementos da Construction (padrão IFC)

| Onde                                              | Propriedade            | Uso                                                        |
| ------------------------------------------------- | ---------------------- | ---------------------------------------------------------- |
| `Pset_WallCommon` / `SlabCommon` / `RoofCommon`   | `IsExternal`           | False → Adiabatic; True → Outdoors (piso: Ground); ausente → externo + aviso |
| material de cada camada (qualquer Pset)           | `ThermalConductivity`  | W/mK. Sem ela: 0.9                                         |
| material (IFC4: `Pset_MaterialCommon`)            | `MassDensity`          | kg/m³. Sem ela: 1920                                       |
| material                                          | `SpecificHeatCapacity` | J/kgK. Sem ela: 790                                        |

Espessuras vêm do `IfcMaterialLayerSet` (convertidas pela unidade do projeto).
