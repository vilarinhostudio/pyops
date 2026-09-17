# Simulações climáticas por fase de projeto

Guia de **qual operador rodar, com que configuração e para responder o quê**, da leitura do clima ao executivo. Um só EPW para o projeto inteiro — trocar de arquivo no meio invalida toda comparação entre fases.

O que cada modo tem de teste real está em [STATUS.md](STATUS.md); os modos marcados *(não testado)* abaixo rodaram só com dado simulado. Psets em [PSETS.md](PSETS.md), armadilhas em [GOTCHAS.md](GOTCHAS.md).

## Princípio

Cada fase só simula o que o modelo daquela fase **sabe**. Radiance com material inventado ou EnergyPlus com parede genérica não são "resultado preliminar" — são número sem dono. Em fase inicial, a pergunta é sempre **comparativa** (A ou B?), nunca absoluta (quantos kWh?).

| Fase                | Modelo disponível                                   | Pergunta                                  | Operadores                                   |
| ------------------- | --------------------------------------------------- | ----------------------------------------- | -------------------------------------------- |
| 0. Clima e programa | só o EPW                                            | o que o clima pede?                       | `ladybug_graphs`                             |
| 1. Estudo de massa  | volumes (malha), entorno                            | onde implantar, como orientar?            | `ladybug_analysis`                           |
| 2. Estudo preliminar| malha com faces pintadas, aberturas desenhadas      | quanta abertura, onde, com que proteção?  | `honeybee_radiance` (Face Attributes), `honeybee_energyplus` (genérico) |
| 3. Anteprojeto      | IFC com paredes/lajes/esquadrias e layer sets       | qual envoltória? qual vidro?              | `honeybee_energyplus` + `honeybee_radiance` (Construction) |
| 4. Projeto legal/básico | IFC com materiais preenchidos                   | atende ao desempenho pretendido?          | os mesmos, com todos os Psets reais          |
| 5. Executivo        | IFC completo, luminárias e aparelhos                | a luz e o ar condicionado escolhidos dão conta? | `honeybee_energyplus` (Collections), `honeybee_radiance` (luz) |

---

## Fase 0 — Clima e programa

**Objetivo:** entender o clima antes de desenhar. Sai daqui a lista de estratégias (ventilar, sombrear, massa térmica, aquecimento solar).

`ladybug_graphs`, Parent = um Empty ou plano na área de trabalho:

1. **Time → Hourly → Dry Bulb Temperature** e **Relative Humidity**: onde estão as horas quentes, frias e úmidas no ano e no dia.
2. **Climate & Comfort → Psychrometric**, *Comfort Zones* ligado, *Comfort Overlay = PMV*: quantas horas já caem no conforto; o resto aponta a estratégia.
   **Adaptive** para edificação ventilada naturalmente.
3. **Sky & Solar → Sun Path** com *Colour Suns By = Dry Bulb*: quais horas de sol são desejáveis (frias) e quais precisam ser barradas (quentes). É daqui que sai o ângulo de brise.
4. **Sky & Solar → Radiation Rose** (Annual): orientações que recebem mais radiação.
5. **Wind → Wind Rose**, período *Custom* com as estações quente e fria separadas: de onde vem a ventilação útil.

**Registrar:** meses de superaquecimento, faixa de horas de sol a barrar, direção de vento predominante no período quente. É a referência das fases seguintes.

---

## Fase 1 — Estudo de massa e implantação

**Objetivo:** escolher implantação, orientação e forma com o entorno real.

`ladybug_analysis`, Parent = volumes, *Context = Collection* + *Context Collection* com o entorno (vizinhos, relevo, árvores como malha):

1. **Direct Sun Hours** sobre as fachadas e o solo, *Custom Period* no **inverno** (sol desejado) e no **verão** (sol a evitar) — duas rodadas, duas saídas.
2. **Solar Radiation** *All Year* nas fachadas e coberturas: onde vai o fotovoltaico, que fachada precisa de proteção.
3. **Outdoor Comfort → UTCI → Comfortable %** no solo dos espaços abertos (praças, recuos), *met* 2.4 para circulação, menor para permanência.

**Como comparar alternativas:** um volume por alternativa, mesmo entorno, mesmo período, mesma escala de cor (fixar o Display Material). O que vale é a diferença entre eles.

**Não fazer ainda:** Radiance ou EnergyPlus — não há abertura nem material.

---

## Fase 2 — Estudo preliminar

**Objetivo:** dimensionar aberturas e proteções. Ainda sem IFC — tudo em malha pintada.

### Luz natural — `honeybee_radiance`, *Model = Face Attributes* *(não testado)*

- Pintar atributos de face (Walls, Floors, Roofs, Apertures, Doors) no mesh de referência.
- Plano de análise: *Result Geometry = Other Object*, uma malha a 0,75 m do piso.
- **Daylight Factor**: rápido, independe de orientação — primeira triagem do tamanho de janela.
- **Annual Daylight** *(não testado)*: DA/UDI, já considera orientação e clima. Usar para decidir entre as alternativas que passaram no DF.

Materiais são os padrões do Radiance: vale para comparar janela, não para prometer número.

### Térmico — `honeybee_energyplus` com construção genérica

- **Parent = Room Reference** (malha fechada do ambiente crítico: o de maior fachada oeste, a cobertura, o dormitório mais exposto).
- *Construction* vazia (todas as faces genéricas — o log avisa), *Voids From = Cut in Room Reference* *(não testado)* com os furos das janelas.
- *HVAC = Ideal Air*, *Lighting = None*, *Criterion = ASHRAE-55 Adaptive*, *Period = Monthly* *(não testado)*.
- Ler **`free_comfortable_hours_pct`** e **`free_air_temp_mean` mensal**: o passe *free* é o que mostra o efeito da janela e da proteção sem o ar condicionado mascarando.

**Comparar:** mesma sala com janela maior/menor, com/sem brise (brise como objeto em *Context* não entra no EnergyPlus — ver limitações).

---

## Fase 3 — Anteprojeto

**Objetivo:** escolher envoltória e vidro com os elementos reais.

Pré-requisito no IFC: paredes, lajes e cobertura com **layer set** e `IsExternal`; janelas com `PyOps_Glazing` (`U_Factor`, `SHGC`, `VisibleTransmittance`).
Checklist antes de rodar (cada item já custou uma rodada):

- Psets térmicos dos materiais em **SI**, sem campo **zerado** ([PSETS](PSETS.md));
- Room Reference encostado na face **interna** das paredes (tolerância = espessura + 2 cm);
- porta até o piso aparece como `não casou com laço interno` — é esperado.

### Térmico — `honeybee_energyplus`

- *Construction* = collection com as paredes/lajes; *Voids From = Projected*; *Layer Order* conforme o type (camada 1 externa ou interna).
- Rodar **uma variável por vez** e anotar: parede A × B, com × sem isolamento na cobertura, vidro simples × controle solar.
- Ler os dois passes:
  - `free_*` — desempenho passivo, a métrica de decisão de envoltória;
  - `active_cooling_load_kwh` / `active_heating_load_kwh` — quanto a envoltória exige do condicionamento.
- **Conferir o `in.idf`** (`%TEMP%\refops_energyplus\active`) na primeira rodada de cada envoltória nova: `Material` com os valores certos, todas as faces com `refops_layers_*`.

### Luz — `honeybee_radiance`, *Model = Construction*

- *Construction* + *Openings* (mesmas collections), *Read from IFC* ligado para as refletâncias do acabamento.
- **Annual Daylight** *(não testado)* nos ambientes de permanência; **Point-in-Time** nos solstícios para ver ofuscamento e mancha de sol.
- O vidro escolhido no térmico (SHGC baixo costuma vir com VT baixo) **tem de ser re-rodado aqui** — é o conflito clássico entre as duas simulações.

---

## Fase 4 — Projeto legal / básico

**Objetivo:** confirmar que a solução escolhida entrega o desempenho pretendido.

- Mesmas rodadas da fase 3, agora com **todos** os Psets reais — nenhum aviso de padrão no log (`sem valor, usando padrão`, `sem IsExternal`, `construção genérica`). Log limpo é o critério de pronto.
- Paredes para ambiente vizinho condicionado: `IsExternal = False` → Adiabatic *(não testado)*.
- Para laudo de norma, ver limitações abaixo: o PyOps **orienta**, não substitui o procedimento normativo.

---

## Fase 5 — Executivo

**Objetivo:** validar luminárias e aparelhos escolhidos.

### `honeybee_energyplus` com Collections

- *Lighting = Collection* com as luminárias (`PyOps_Light.Wattage`); o log mostra a densidade em W/m² — conferir com o projeto luminotécnico.
- *HVAC = Collection* com os aparelhos (`PyOps_HVAC`: capacidades e COP).
- Ler `active_electricity_kwh` (equipamentos + luz + carga/COP) e `active_comfortable_hours_pct`: se o conforto ativo cai em relação ao *Ideal Air*, a capacidade não deu conta.
- **Cuidado:** a carga horária já passou do limite de capacidade num teste (em aberto em STATUS). Até investigar, não usar este modo para dimensionar aparelho — só para comparar aparelhos entre si.

### `honeybee_radiance` com luz artificial

- *Sources = Collection* (`PyOps_Light.LuminousFlux`), **Point-in-Time à noite** (ex.: 21/06 23h) para isolar a luz artificial e conferir o nível no plano de trabalho.

---

## Limitações que mudam a leitura

- **EnergyPlus é um ambiente só.** Sem troca de calor com vizinhos além de Adiabatic; rodar o ambiente crítico, não o edifício.
- **Brise, beiral e entorno não sombreiam no EnergyPlus** — o operador não tem entrada de sombreamento. O efeito do brise se vê no Radiance e no `ladybug_analysis`, não nas cargas.
- **Setpoints constantes** (sem setback noturno) e **solo a 18 °C fixo**: aquecimento tende a sair alto.
- **Ideal Air** não é um sistema real: capacidade infinita no modo padrão; no modo Collection, COP aplicado depois da simulação.
- **Não é procedimento normativo.** NBR 15575 (desempenho habitacional) e INI-C/INI-R (PBE Edifica) exigem modelo de referência, agendas e parâmetros próprios — conferir o procedimento na versão vigente. Use o PyOps para chegar à solução; a verificação formal segue o método da norma.
- **Comparação só vale com tudo igual:** mesmo EPW, período, program, critério de conforto e horas. Anotar a configuração junto do resultado — as custom props `epw_path` e `hb_*` guardam parte disso no objeto.
