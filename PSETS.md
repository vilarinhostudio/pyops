# Psets usados pelo PyOps

## Pertença a cômodo (Radiance e EnergyPlus)

Nenhum dos dois operadores seleciona geometria por Collection. Cada papel (Construction,
Openings, Lights, HVAC) tem seu próprio Pset configurável (**Construction Pset**, **Openings
Pset**, **Lights Pset**, **HVAC Room Pset**), e um único campo **Room** por operador dá o nome
da propriedade booleana procurada em cada um desses Psets.

| Pset (nome padrão)       | Papel                          |
| ------------------------ | ------------------------------- |
| `PyOps_RoomConstruction` | paredes/lajes/telhados          |
| `PyOps_RoomOpenings`     | janelas/portas                  |
| `PyOps_RoomLights`       | luminárias                      |
| `PyOps_RoomHVAC`         | aparelhos (só EnergyPlus)       |

Um elemento entra na simulação de "Bedroom" tendo `Bedroom = True` no Pset do seu papel — nada
impede o mesmo elemento (uma parede compartilhada, por exemplo) de ter `Bedroom = True` **e**
`LivingRoom = True` ao mesmo tempo, então não é preciso mover objeto nenhum entre Collections
pra simular outro cômodo. Os quatro Pset são independentes entre si e independentes de
`PyOps_Glazing`/`PyOps_Light`/`PyOps_HVAC` abaixo, que continuam descrevendo propriedades
físicas do elemento, não pertença.

### Como marcar um elemento (workflow no Bonsai)

O campo **Room** do operador não é o nome do Pset — é o nome de uma **propriedade booleana
dentro** do Pset do papel. Pra uma parede do "Bedroom":

1. Seleciona a parede.
2. Painel de Property Sets → **Add Pset** → nome `PyOps_RoomConstruction` (tem que bater com o
   que está no campo **Construction Pset** do operador).
3. Dentro desse Pset → **Add Property** → nome `Bedroom` (tem que bater com o campo **Room**) →
   tipo **Boolean** → valor `True`.

Pra essa mesma parede também servir a sala vizinha: **não cria outro Pset** — adiciona uma
segunda propriedade booleana dentro do mesmo `PyOps_RoomConstruction`: `LivingRoom = True`.

Mesmo padrão pros outros papéis: janela → propriedade booleana em `PyOps_RoomOpenings`;
luminária → em `PyOps_RoomLights`; aparelho → em `PyOps_RoomHVAC`. Sempre uma propriedade
por cômodo, nomeada como o cômodo, dentro do Pset do papel.

**Cuidado — comparação é string exata, sem autocomplete** (diferente dos campos
`search: "attribute"` de outros operadores): `Bedroom` ≠ `bedroom` ≠ `Bedroom ` (espaço a
mais). Erro de digitação não dá exceção — só devolve lista vazia e cai no aviso "nenhum
elemento marcado", fácil de confundir com "esqueci de marcar".

## Honeybee Radiance

### `PyOps_Glazing` (custom, nas janelas/portas marcadas em Openings Pset)

| Propriedade            | Tipo        | Uso                                                        |
| ---------------------- | ----------- | ---------------------------------------------------------- |
| `VisibleTransmittance` | Real 0–1    | > 0.01 → glass; ≤ 0.01 → opaco (plastic). Sem ela: VT 0.6  |
| `Reflectance`          | Real 0–1    | só no opaco; ignorada em vidro. Sem ela: 0.5               |

Nome do Pset é configurável no campo **Glazing Pset**.

### `PyOps_Light` (custom, nas luminárias marcadas em Lights Pset)

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

### `PyOps_HVAC` (custom, nos aparelhos marcados em HVAC Room Pset)

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

**Unidades e zeros — nada disso é convertido nem validado pelo PyOps:**

- sempre SI: `MassDensity` em kg/m³ (concreto **2400**, não 2,4), `SpecificHeatCapacity` em J/kgK (**840**, não 0,84), `ThermalConductivity` em W/mK;
- propriedade **presente com 0** não cai no padrão: derruba a construção inteira do elemento para a genérica. Preencher ou apagar;
- valor ausente usa o padrão e avisa; valor em unidade errada passa **calado** — conferir `Material` no `in.idf` (ver GOTCHAS).

Referência para conferência (ordem de grandeza): concreto 1,75 / 2400 / 1000; bloco cerâmico ~0,7–0,9 / 1300–1600 / 840–920; EPS 0,035–0,04 / 15–35 / 1400.
