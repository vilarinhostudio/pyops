# Sessões MCP — hábitos de fluxo

Não é sobre o addon — é sobre como eu (Claude, via MCP do Blender) devo operar numa sessão. Ver [CLAUDE.md](CLAUDE.md) pro resto.

## Material de visualização de resultado

Antes de rodar uma simulação climática (Ladybug/Honeybee), checar se existe no arquivo um material chamado **"Visualize Data"** ou algo com nome sugestivo de análise (`dry-bulb-analysis`, etc.). Se não existir, **avisar** — não criar um sozinho.

Se existir, depois de escrever o resultado:

- **Map Range**: `From Min`/`From Max` = mínimo e máximo REAIS alcançados na simulação (não o range genérico que já está no node).
- **Named Attribute**: conferir se o nome bate com o atributo que a simulação escreveu (`hb_value_*`, `daylight_factor`, `da`, `free_air_temp_mean`, etc.); trocar só se necessário.
- Simulações diferentes na mesma cena → **duplicar** o material, não reaproveitar o mesmo — cada cópia leva seu próprio Map Range/Named Attribute.

## Radiance — Optical Properties

`use_optics=True` é o padrão certo. Os campos de override do operador (`reflectance_pset`, `transmittance_pset`, etc. — "Pset.Prop") ficam **em branco de propósito**: GOTCHAS.md documenta que esse campo é ignorado mas o valor chega mesmo assim pela busca no material. Preenchê-los não muda nada. O que precisa estar preenchido é o `Pset_MaterialOptical` (ou `PyOps_MatOptical`) do **material** em si — é isso que conferir, não os campos do operador.

## Sensor plane / grade de análise

Se o grau de definição (número de sensores) não estiver explícito no pedido, **perguntar** antes de gerar — não assumir baixa nem alta resolução. Em dúvida sobre tempo de execução, começar com poucas faces (mais barato reprocessar do que esperar uma grade densa travar a sessão).

## Rodar o mesmo operador várias vezes numa varredura

Cada rodada testa UMA variável (ou uma combinação deliberada e nomeada) — nunca repetir a mesma configuração. Anotar no resumo final o que mudou em cada rodada, pra ficar claro que não foi redundância.

## Nunca chamar `bpy.ops.script.reload()` numa sessão MCP

Recarrega TODOS os addons, inclusive o próprio `blender_mcp` — derruba a conexão da sessão que está chamando, sem chance de reconectar sozinho (precisa reiniciar o servidor MCP no Blender). Pra recarregar só o PyOps depois de editar um arquivo novo do addon: `import addon_utils; addon_utils.disable("pyops"); addon_utils.enable("pyops")` — não toca no `blender_mcp`.

## Descoberta de pacote / API a cada sessão nova

O nome do pacote Bonsai (`bonsai` vs `bonsaiPR` vs `bl_ext.*`) pode mudar entre sessões — sempre chamar `bim.ensure()` (ou descobrir via `sys.modules`) antes de qualquer chamada direta a `bonsaiPR.tool`/`ifcopenshell.api`. Ver GOTCHAS.md.

## bmesh

Gotcha de layers (`bm.*.layers.*.new()` invalida referências já seguradas) documentado em GOTCHAS.md, seção Geometria e placement.
