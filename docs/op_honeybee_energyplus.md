# op_honeybee_energyplus — design

Estado em 2026-09-16: **implementado** (`op_honeybee_energyplus.py` + seção ENERGYPLUS de `honeybee_helpers.py` + `bim.material_layers`). Testado em Blender background com IFC simulado; falta teste com Bonsai real — ver STATUS.md.

## Ideia

Simulação térmica de **um** ambiente. Parent Object = **Room Reference** (malha fechada, volume do EnergyPlus); a cópia do motor recebe os resultados como atributos FACE uniformes. Duas rodadas por execução: `free` (sem HVAC/setpoint) e `active` (climatização + luz).

Reaproveita o Radiance reformado: `planar_regions`, `pair_voids`, `match_openings`, `largest_outline`, `_inside_loop`, `material_properties`, leitura de Pset por collection.

## Decidido

- **Execução sem OpenStudio**: `honeybee_energy.writer.model_to_idf` + `run.run_idf`. Por isso HVAC é sempre `IdealAirSystem`.
- **Room**: uma `Face` por região plana do Room Reference (só contorno externo). Abortar se não for sólido ou volume ≤ 0.
- **Face ↔ Construction**: região paralela, distância ≤ espessura + 2 cm, centroide **ou** algum ponto a meio caminho entre o centroide e um vértice da face dentro do contorno externo (2026-09-17: só o centroide falhava com porta até o piso centrada na parede — o entalhe engolia o centro); vence a mais próxima. Sem par: construção genérica + aviso.
- **Vãos no Room Reference**: enum com duas opções
  - *Recortado*: furos do Room Reference → `match_openings` (novo parâmetro `pad` = espessura do elemento casado).
  - *Projetado*: vãos detectados na Construction (pipeline do Radiance) projetados na face do Room casada.
- **Camadas**: `bim.material_layers(element)` novo → `[(espessura_m, IfcMaterial)]` via `get_material` + `calculate_unit_scale`. Por camada: `ThermalConductivity`, `MassDensity` (no IFC4 fica em `Pset_MaterialCommon`, lido por nome em qualquer Pset do material), `SpecificHeatCapacity`. Falta → 0.9 / 1920 / 790 + aviso. Cache por assinatura de camadas.
- **Janela**: `glazing_values(obj, pset)` (refatorar do `glazing_modifier`) lê VT, Reflectance, `U_Factor`, `SHGC` de `PyOps_Glazing`. VT > 0.01 → `SimpleGlazSys`; VT ≤ 0.01 → `Door` com `EnergyMaterialNoMass(R=1/U)`.
- **Program**: `program_type_by_identifier`; não encontrou → 5 mais próximos (`difflib`) e para.
- **Setpoints**: dois floats visíveis nos dois modos de climatização.
- **HVAC Collection** (`PyOps_HVAC`: CoolingCapacity, HeatingCapacity, COP_Cooling, COP_Heating): Ideal Air com limite = Σ capacidades; COP agregado = Σ Cap / Σ (Cap/COP); eletricidade = carga/COP pós-simulação. É aproximação — dizer no console.
- **Luz Collection**: `light_readings(collection, pset)` (refatorar do `light_sources`); Σ Wattage / área de piso → `Lighting` com agenda do program. Só `LuminousFlux` → W = lm/90 + aviso.
- **Passe free**: mesmas cargas internas do active, só sem HVAC e setpoint.
- **Conforto**: enum *ASHRAE-55 adaptativo* / *Faixa fixa*; enum *Todas as horas* / *Horas ocupadas do program*.
- **Granularidade**: Annual e Monthly rodam o ano todo (saída horária agregada em Python); Single Month roda só o mês. Sufixo `_mMM` fora do Annual.
- **Saídas**: `free_/active_air_temp_mean`, `free_/active_comfortable_hours_pct`, `active_cooling_load_kwh`, `active_heating_load_kwh`, `active_electricity_kwh`. Custom props `hb_<nome>_min/max/mean`; no Monthly, min/max entre meses.
- **EnergyPlus**: `energyplus_path()` + comparar `energyplus_idf_version()` com o executável antes de rodar.

## Fechado em 2026-09-16 (eram as perguntas em aberto)

1. **Iluminação = None** zera a luz do ProgramType (`program.lighting = None`). Input explícito vence o default da biblioteca.
2. **Eletricidade no Ideal Air** = só luz + equipamentos. Carga de aquecimento/resfriamento sai separada em kWh de carga, sem COP inventado. No modo HVAC Collection soma-se carga ÷ COP agregado.
3. **Ordem das camadas**: enum *First Layer Outside* (padrão) / *Last Layer Outside*. *Auto* via `IfcMaterialLayerSetUsage` fica para depois — só acrescentar item no enum e o ramo em `thermal_construction`.
4. **Condição de contorno**: `IsExternal` de `Pset_WallCommon/SlabCommon/RoofCommon` (tenta os três). False → Adiabatic; True ou ausente → Ground se a face é piso, Outdoors nos demais; ausente avisa. Abertura só entra em face Outdoors (honeybee recusa nas outras) — senão aviso e descarta.
5. **Pontos frágeis**: aviso no console e segue — face cobrindo dois elementos (usa o mais próximo), abertura projetada fora da face (descartada), camada > 2 m, HVAC Collection sem carga parcial.

HVAC Collection imprime aviso explícito: capacidade somada como limite, COP agregado aplicado fora da simulação, sem variação por temperatura externa ou carga parcial — compara aparelhos, consumo absoluto otimista.

## Decisões tomadas na implementação (revisar)

- `match_openings`: o `pad` virou chave por vão (`void["pad"]`), não parâmetro da função — no modo Recortado cada furo tem a espessura do seu próprio elemento.
- Defaults quando o pset de Openings não tem valor (sempre avisados): U 5.8 / SHGC 0.8 para vidro, U 2.5 para porta opaca; VT padrão 0.6 (o mesmo do Radiance). Vão sem objeto casado usa a janela genérica do honeybee.
- Faixa fixa de conforto padrão 18–26 °C; conforto calculado sobre temperatura **operativa**; `air_temp_mean` é a temperatura média do ar sobre todas as horas (o filtro de horas ocupadas só vale para o conforto).
- Horas ocupadas vêm de `Zone People Occupant Count` > 0 pedido ao próprio EnergyPlus, não da agenda recalculada em Python — alinhamento garantido.
- Luminárias com Program sem luz: agenda de ocupação; sem ocupação também: sempre ligadas (avisado).
- Setpoints viram agendas constantes (perdem o setback do Program).
- Resultados em `%TEMP%\pyops_energyplus\free` e `\active`, apagados a cada execução.

## Pré-requisito verificado

2026-09-16: `tools/energyplus_smoke.py` (sala caixa + Ideal Air + EPW de São Paulo, `model_to_idf` + `run_idf`, fora do Blender, contra `C:\pyops_libs`) **roda**: honeybee-energy 1.123.10 + EnergyPlus 26.1.0 (`C:\EnergyPlusV26-1-0`, achado sem `config.json`), IDF `Version` = exe, 0 severe / 0 fatal, `.sql` gerado, ano inteiro em ~2 s. Warnings não fatais: sem `Site:GroundTemperature`, OA do Ideal Air excede limite, `Temperature out of range (PsyPsatFnTemp)` com 6383 °C no `CalcPurchAirLoads`, só no design day de entalpia (sizing, não no ano) — olhar antes de confiar em capacidade autodimensionada. Dentro do Blender (background, IFC simulado) também roda — ver STATUS.md. ladybug_tools tem honeybee-energy 1.104.3 (diferente), irrelevante aqui: E+ roda como exe.

Descoberto no caminho: `model_to_idf` não escreve `Version` nem `SimulationParameter` (o CLI concatena `energyplus_idf_version()` + `sim_par.to_idf()` + modelo), e o Ideal Air sai como `HVACTemplate:Zone:IdealLoadsAirSystem` → `run_idf` precisa de `expand_objects=True` (padrão). Zone sizing ligado por padrão exige design days (DDY ou aproximados do EPW).
